"""
Entry point for the Operating Systems topic-cleanup pass (v2).

Classifies every OS question into one of 8 canonical topics by reading the
question text, options, and explanation, then calling OpenAI gpt-4o-mini.

Usage:
    # Full pass (checkpoint-resumable via .topic_v2_progress.json):
    python -m enrichment.run_topic_v2

    # Restart the full pass from the beginning:
    python -m enrichment.run_topic_v2 --reset

    # Retry only questions that still have a non-canonical topic (the 101 failures):
    python -m enrichment.run_topic_v2 --retry-failed
"""
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def _process_batch(questions: list, executor: ThreadPoolExecutor) -> tuple[int, int]:
    from enrichment.topic_tagger import process_question

    futures = {executor.submit(process_question, q): q for q in questions}
    ok = fail = 0
    for future in as_completed(futures):
        q = futures[future]
        try:
            if future.result():
                ok += 1
            else:
                fail += 1
        except Exception as e:
            log.error("Unhandled error for id=%s: %s", q.get("id"), e)
            fail += 1
    return ok, fail


def _run_retry_failed(executor: ThreadPoolExecutor) -> tuple[int, int, int]:
    """
    Loop over questions whose topic is still non-canonical and reclassify them.
    Returns (total_ok, total_fail, total_batches).
    """
    from enrichment.topic_db import fetch_unclassified_batch, count_unclassified

    try:
        remaining = count_unclassified()
        log.info("Questions with non-canonical topic: %d", remaining)
    except Exception as e:
        log.warning("Could not count unclassified rows: %s", e)
        remaining = None

    total_ok = total_fail = total_batches = 0
    offset = 0

    while True:
        rows, next_offset = fetch_unclassified_batch(offset)
        if not rows:
            log.info("No more unclassified questions — retry pass complete.")
            break

        total_batches += 1
        pct = f"{(offset / remaining * 100):.1f}%" if remaining else "?%"
        log.info("Retry batch #%d | %d row(s) | processed so far: %s",
                 total_batches, len(rows), pct)

        ok, fail = _process_batch(rows, executor)
        total_ok += ok
        total_fail += fail

        # Advance by the number of rows still unclassified that we *successfully*
        # wrote — failures stay in the table, so the next fetch will naturally
        # pick them up again if we don't advance past them.  We always advance
        # by the full batch size so we don't infinite-loop on persistent failures.
        offset = next_offset

        log.info("Retry batch #%d done | ok=%d fail=%d | running total ok=%d fail=%d",
                 total_batches, ok, fail, total_ok, total_fail)

    return total_ok, total_fail, total_batches


def _run_full_pass(executor: ThreadPoolExecutor, reset: bool) -> tuple[int, int, int]:
    """
    Offset-paginated full pass over all OS questions.
    Returns (total_ok, total_fail, total_batches).
    """
    from enrichment.topic_db import fetch_batch, save_progress, count_total, reset_progress

    if reset:
        reset_progress()

    try:
        total = count_total()
        log.info("Total OS questions : %d", total)
    except Exception as e:
        log.warning("Could not count rows: %s", e)
        total = None

    total_ok = total_fail = total_batches = 0
    offset = None  # reads from progress file on first call

    while True:
        rows, next_offset = fetch_batch(offset)
        if not rows:
            log.info("No more questions — pass complete.")
            break

        total_batches += 1
        pct = f"{(next_offset / total * 100):.1f}%" if total else "?%"
        log.info("Batch #%d | rows %d–%d (%s) | processing...",
                 total_batches, (offset or 0) + 1, next_offset, pct)

        ok, fail = _process_batch(rows, executor)
        total_ok += ok
        total_fail += fail
        offset = next_offset
        save_progress(offset)

        log.info("Batch #%d done | ok=%d fail=%d | running total ok=%d fail=%d",
                 total_batches, ok, fail, total_ok, total_fail)

    return total_ok, total_fail, total_batches


def main() -> None:
    from enrichment.config import MAX_WORKERS, OPENAI_MODEL

    retry_failed = "--retry-failed" in sys.argv
    reset = "--reset" in sys.argv

    log.info("=" * 60)
    log.info("Catalyst topic-v2 pass starting")
    log.info("  Mode              : %s", "retry-failed" if retry_failed else "full pass")
    log.info("  Subject filter    : Operating Systems")
    log.info("  OpenAI model      : %s", OPENAI_MODEL)
    log.info("  Threads           : %d", MAX_WORKERS)
    log.info("=" * 60)

    t_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="topic") as executor:
        if retry_failed:
            total_ok, total_fail, total_batches = _run_retry_failed(executor)
        else:
            total_ok, total_fail, total_batches = _run_full_pass(executor, reset)

    elapsed = time.monotonic() - t_start
    log.info("=" * 60)
    log.info("Finished in %.1fs | %d batch(es) | ok=%d fail=%d",
             elapsed, total_batches, total_ok, total_fail)
    log.info("=" * 60)


if __name__ == "__main__":
    main()