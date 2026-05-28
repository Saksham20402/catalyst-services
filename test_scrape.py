"""
Quick scrape test — fetches a single Sanfoundry page and prints all parsed questions.
Does NOT follow any chapter links.

Usage:
    python test_scrape.py           # normal output
    python test_scrape.py --debug   # also dumps raw HTML of every GeSHi block found
"""
import sys
import logging
import re
from bs4 import BeautifulSoup
from ingestion.web_scrapper import parse_chapter, _GESHI_OUTER
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

_args   = [a for a in sys.argv[1:] if not a.startswith("--")]
DEBUG   = "--debug" in sys.argv
TARGET_URL = _args[0] if _args else "https://www.sanfoundry.com/java-questions-answers-freshers-experienced/"


def dump_geshi_blocks(html: str) -> None:
    """
    Inspect every GeSHi wrapper on the raw page BEFORE any processing.
    Prints structure details so we can spot why some blocks come out empty.
    """
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", {"class": "entry-content"})
    if not content:
        print("No entry-content div found.")
        return

    blocks = content.find_all("div", class_=_GESHI_OUTER)
    print(f"\n{'='*60}")
    print(f"RAW GESHI BLOCKS FOUND: {len(blocks)}")

    for idx, block in enumerate(blocks, 1):
        print(f"\n--- Block {idx} ---")
        # Show all div classes inside to understand structure
        for div in block.find_all("div"):
            print(f"  div.classes={div.get('class')}")

        li_tags = block.find_all("li")
        print(f"  <li> count: {len(li_tags)}")
        for i, li in enumerate(li_tags[:3]):  # first 3 lines max
            text = li.get_text()
            print(f"  li[{i}] text={repr(text[:80])}")
            # Also show the raw HTML to catch unexpected nesting
            print(f"  li[{i}] html={str(li)[:200]}")

        # Check for collapseomatic or any hidden wrappers
        hidden = block.find_all(style=re.compile(r"display\s*:\s*none", re.I))
        if hidden:
            print(f"  WARNING: {len(hidden)} hidden element(s) inside this block")


def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            "./browser_session",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0]
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        log.info("Navigating to %s", TARGET_URL)
        log.info(">>> If a Cloudflare challenge appears in the browser, solve it manually. <<<")
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_selector("div.entry-content", timeout=60000)
            log.info("Content loaded.")
        except Exception:
            log.error("Timed out waiting for content. The page may still be on a CF challenge.")
            context.close()
            return

        html = page.content()
        context.close()

    if not html:
        log.error("Failed to fetch page.")
        return

    if DEBUG:
        dump_geshi_blocks(html)

    questions = parse_chapter(html, TARGET_URL)
    log.info("Found %d questions total.", len(questions))

    for i, q in enumerate(questions, 1):
        print(f"\n{'='*60}")
        print(f"[{i}] {q.text}")
        if q.has_code_snippet:
            print(f"  [CODE: {q.snippet_language}]")
            if q.code_snippet:
                print("  " + "\n  ".join(q.code_snippet.splitlines()))
            else:
                print("  <EMPTY — extraction failed>")
        for j, opt in enumerate(q.options):
            marker = ">>>" if j == q.correct_index else "   "
            print(f"  {marker} {chr(ord('a') + j)}) {opt}")
        if q.source_explanation:
            print(f"\n  [Explanation] {q.source_explanation}")
        if q.explanation_output:
            print(f"  [Output]\n  " + "\n  ".join(q.explanation_output.splitlines()))

    # Summary
    code_qs = [q for q in questions if q.has_code_snippet]
    empty_qs = [q for q in code_qs if not q.code_snippet]
    print(f"\n{'='*60}")
    print(f"Total questions  : {len(questions)}")
    print(f"With code snippet: {len(code_qs)}")
    print(f"Empty code blocks: {len(empty_qs)}")
    if code_qs:
        langs = {}
        for q in code_qs:
            langs[q.snippet_language] = langs.get(q.snippet_language, 0) + 1
        print(f"Languages found  : {langs}")


if __name__ == "__main__":
    main()