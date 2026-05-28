import re
import time
import uuid
import logging
import random
from bs4 import BeautifulSoup
from ingestion.models import QuestionDTO
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
MAX_RETRIES = 3
MIN_REQUEST_DELAY = 12.0  # Slow and steady to avoid re-triggering CF
MAX_REQUEST_DELAY = 18.0

# --- REGEX PATTERNS ---
QNUM_RE         = re.compile(r"^(\d+)\.\s*(.+)", re.IGNORECASE)
OPTION_RE       = re.compile(r"^([a-e])\)\s*(.+)", re.IGNORECASE)
ANSWER_RE       = re.compile(r"Answer\s*[:\-]?\s*([a-e])", re.IGNORECASE)
EXPLANATION_RE  = re.compile(r"^Explanation\s*[:\-]?\s*(.*)", re.IGNORECASE)
GESHI_START_RE  = re.compile(r"^\[CODESNIPPET:(.+?)\]$")
GESHI_END       = "[/CODESNIPPET]"
TERMOUT_START   = "[TERMOUTPUT]"
TERMOUT_END     = "[/TERMOUTPUT]"

# GeSHi wrapper classes — outermost div to find, inner divs to skip when detecting language
_GESHI_OUTER     = "hk1_style-wrap4"
_GESHI_WRAPPERS  = {"hk1_style-wrap4", "hk1_style-wrap3", "hk1_style-wrap2", "hk1_style-wrap", "hk1_style"}

def _preprocess_geshi(content) -> None:
    """
    Find every GeSHi syntax-highlighted block and replace it with a plain-text
    marker so the line parser can reconstruct code snippets cleanly.

    GeSHi structure on Sanfoundry:
      div.hk1_style-wrap4 > ... > div.[language] > ol > li.li1 > pre.de1
    Each <li> is one line of code; <span> children are just syntax tokens.
    """
    for geshi in content.find_all("div", class_=_GESHI_OUTER):
        # The language is the class of the first inner div that isn't a wrapper
        lang = "text"
        for div in geshi.find_all("div"):
            classes = div.get("class", [])
            non_wrapper = [c for c in classes if c not in _GESHI_WRAPPERS]
            if non_wrapper:
                lang = non_wrapper[0]
                break

        li_tags = geshi.find_all("li")
        if li_tags:
            # Line-numbered mode: ol > li.li1 > pre.de1 (one line per <li>)
            lines = [li.get_text() for li in li_tags]
        else:
            # No-line-numbers mode: code is in a <pre> directly inside the language div
            pre = geshi.find("pre")
            lines = pre.get_text().splitlines() if pre else []

        code_text = "\n".join(lines)

        first_line = next((l.strip() for l in lines if l.strip()), "")
        if first_line.startswith("$"):
            # Terminal output block — preserve as a marker for the explanation parser
            geshi.replace_with(f"{TERMOUT_START}\n{code_text}\n{TERMOUT_END}")
        else:
            geshi.replace_with(f"[CODESNIPPET:{lang}]\n{code_text}\n{GESHI_END}")


def slug_to_topic(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    prefix = "operating-system-questions-answers-"
    if slug.startswith(prefix):
        slug = slug[len(prefix):]
    return slug.replace("-", " ").title()

def finalize_q(q_dict, topic, url) -> QuestionDTO:
    letter_map = {chr(ord('a') + i): i for i in range(5)}
    correct_idx = letter_map.get(q_dict['correct_letter'].lower(), 0)
    return QuestionDTO(
        id=str(uuid.uuid4()),
        text=q_dict['text'],
        options=q_dict['options'],
        correct_index=min(correct_idx, len(q_dict['options']) - 1),
        topic=topic,
        subject="Operating Systems",
        source=url,
        has_code_snippet=q_dict.get('has_code_snippet', False),
        code_snippet=q_dict.get('code_snippet'),
        snippet_language=q_dict.get('snippet_language'),
        source_explanation=q_dict.get('source_explanation'),
        explanation_output=q_dict.get('explanation_output'),
    )

def parse_chapter(html: str, url: str) -> list[QuestionDTO]:
    soup = BeautifulSoup(html, "html.parser")
    topic = slug_to_topic(url)
    content = soup.find("div", {"class": "entry-content"})
    if not content: return []

    # Clean UI artifacts
    for span in content.find_all("span", class_=re.compile(r"collapseomatic|sf-spawn")):
        span.decompose()

    # Replace GeSHi code blocks with plain-text markers before stripping HTML
    _preprocess_geshi(content)

    all_text = content.get_text(separator="\n", strip=True).splitlines()
    questions, current_q = [], None

    # Parser state
    in_code        = False
    in_term_output = False
    in_explanation = False
    code_lang, code_lines       = None, []
    term_output_lines           = []
    explanation_lines           = []

    def _flush(q):
        """Attach accumulated explanation/output to question dict before finalizing."""
        if explanation_lines:
            q['source_explanation'] = " ".join(explanation_lines)
        if term_output_lines:
            q['explanation_output'] = "\n".join(term_output_lines)

    for line in all_text:
        line = line.strip()
        if not line:
            continue

        # ── Code block (source) ──────────────────────────────────────────────
        geshi_m = GESHI_START_RE.match(line)
        if geshi_m:
            in_code = True
            code_lang = geshi_m.group(1)
            code_lines = []
            continue
        if line == GESHI_END:
            if current_q is not None and not in_explanation:
                current_q['code_snippet'] = "\n".join(code_lines)
                current_q['snippet_language'] = code_lang
                current_q['has_code_snippet'] = True
            in_code = False
            code_lang = None
            code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue

        # ── Terminal output block (explanation appendix) ─────────────────────
        if line == TERMOUT_START:
            in_term_output = True
            term_output_lines = []
            continue
        if line == TERMOUT_END:
            in_term_output = False
            continue
        if in_term_output:
            term_output_lines.append(line)
            continue

        # ── New question number — the ONLY thing that exits explanation mode ──
        q_m = QNUM_RE.match(line)
        if q_m:
            if current_q and len(current_q['options']) >= 2 and current_q['correct_letter']:
                _flush(current_q)
                questions.append(finalize_q(current_q, topic, url))
            current_q = {
                "text": q_m.group(2).strip(),
                "options": [],
                "correct_letter": None,
                "has_code_snippet": False,
                "code_snippet": None,
                "snippet_language": None,
                "source_explanation": None,
                "explanation_output": None,
            }
            in_explanation = False
            explanation_lines = []
            term_output_lines = []
            continue

        if not current_q:
            continue

        # ── Explanation mode — collect text, ignore option/answer patterns ───
        if in_explanation:
            exp_m = EXPLANATION_RE.match(line)
            if exp_m:
                rest = exp_m.group(1).strip()
                if rest:
                    explanation_lines.append(rest)
            else:
                explanation_lines.append(line)
            continue

        # ── Normal question parsing ───────────────────────────────────────────
        opt_m = OPTION_RE.match(line)
        if opt_m:
            current_q['options'].append(opt_m.group(2).strip())
            continue
        ans_m = ANSWER_RE.search(line)
        if ans_m:
            current_q['correct_letter'] = ans_m.group(1)
            in_explanation = True   # everything after this belongs to explanation
            explanation_lines = []
            term_output_lines = []
            continue

    # Flush final question
    if current_q and len(current_q['options']) >= 2 and current_q['correct_letter']:
        _flush(current_q)
        questions.append(finalize_q(current_q, topic, url))
    return questions

def _stealth_fetch(page, url: str) -> str | None:
    """Navigates with bot-detection bypass and handles Cloudflare challenges."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Navigating to {url} (Attempt {attempt})")
            
            # Go to URL
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            
            # 1. Check for immediate hard block
            if response and response.status == 403:
                logger.error(f"🛑 403 Forbidden. Cloudflare blocked the session at {url}")
                return None

            # 2. Wait for Cloudflare "Just a moment" or checkbox
            # We look for the common Sanfoundry content div to confirm we are 'in'
            try:
                page.wait_for_selector("div.entry-content", timeout=15000)
            except PlaywrightTimeoutError:
                logger.warning("Sanfoundry content not found. Solving challenge...")
                # If you see the browser window stuck on a checkbox, CLICK IT MANUALLY
                page.wait_for_timeout(10000) 

            # Final check before returning content
            if "Just a moment" in page.content():
                logger.error("Failed to bypass Cloudflare challenge.")
                return None

            return page.content()

        except Exception as e:
            logger.warning(f"Error fetching {url}: {e}")
            if attempt == MAX_RETRIES: return None
            time.sleep(5)
    return None

def scrape_sanfoundry(index_url: str) -> list[QuestionDTO]:
    all_results = []
    
    with sync_playwright() as p:
        user_data_dir = "./browser_session"
        context = p.chromium.launch_persistent_context(
            user_data_dir,
            headless=False, 
            args=["--disable-blink-features=AutomationControlled"],
            viewport={'width': 1280, 'height': 800}
        )
        page = context.pages[0]
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            logger.info("Opening Sanfoundry Index...")
            # CHANGE 1: Use 'commit' so we don't wait for heavy ads to load
            page.goto(index_url, wait_until="commit", timeout=60000)

            try:
                # This is the "Anchor" - it confirms the page is actually readable
                page.wait_for_selector("div.entry-content", timeout=45000)
                logger.info("✅ Index Content detected!")
            except:
                logger.error("🛑 Timeout waiting for content. Refresh the browser manually if it's stuck.")
                return []

            # Discover chapters
            soup = BeautifulSoup(page.content(), "html.parser")
            category_div = soup.find("div", {"class": "sf-postw-category"})
            chapter_urls = [a.get("href") for a in category_div.find_all("a") if a.get("href")] if category_div else [index_url]
            logger.info(f"Discovered {len(chapter_urls)} chapters.")

            for i, url in enumerate(chapter_urls):
                time.sleep(random.uniform(10, 15)) 
                
                # CHANGE 2: Repeat the stealth navigation for every chapter
                logger.info(f"Processing: {url}")
                page.goto(url, wait_until="commit", timeout=60000)
                
                try:
                    # Ensure the MCQs are visible before parsing
                    page.wait_for_selector("div.entry-content", timeout=20000)
                except:
                    logger.warning(f"Skipping {url} - MCQs didn't load in time.")
                    continue
                
                qs = parse_chapter(page.content(), url)
                all_results.extend(qs)
                logger.info(f"[{i+1}/{len(chapter_urls)}] Scraped {len(qs)} q")

            return all_results

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            return all_results # Return what we have instead of crashing
        finally:
            context.close()