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
QNUM_RE   = re.compile(r"(\d+)\.\s*(.+)", re.IGNORECASE)
OPTION_RE = re.compile(r"([a-e])\)\s*(.+)", re.IGNORECASE)
ANSWER_RE = re.compile(r"Answer\s*[:\-]?\s*([a-e])", re.IGNORECASE)

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
        source=url
    )

def parse_chapter(html: str, url: str) -> list[QuestionDTO]:
    soup = BeautifulSoup(html, "html.parser")
    topic = slug_to_topic(url)
    content = soup.find("div", {"class": "entry-content"})
    if not content: return []

    # Clean UI artifacts
    for span in content.find_all("span", class_=re.compile(r"collapseomatic|sf-spawn")):
        span.decompose()
    
    all_text = content.get_text(separator="\n", strip=True).splitlines()
    questions, current_q = [], None

    for line in all_text:
        line = line.strip()
        if not line: continue
        q_m = QNUM_RE.search(line)
        if q_m:
            if current_q and len(current_q['options']) >= 2 and current_q['correct_letter']:
                questions.append(finalize_q(current_q, topic, url))
            current_q = {"text": q_m.group(2).strip(), "options": [], "correct_letter": None}
            continue
        if not current_q: continue
        opt_m = OPTION_RE.search(line)
        if opt_m:
            current_q['options'].append(opt_m.group(2).strip())
            continue
        ans_m = ANSWER_RE.search(line)
        if ans_m:
            current_q['correct_letter'] = ans_m.group(1)
            continue

    if current_q and len(current_q['options']) >= 2 and current_q['correct_letter']:
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