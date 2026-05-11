"""
Entry point for the Catalyst enrichment service.

Safe to run in parallel across multiple machines with different Groq keys.
Each worker atomically claims its own batch via Supabase — no overlap.

Usage:
    python -m enrichment.run

Each run loops until claim_batch returns no rows.
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
    from enrichment.enricher import process_question

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


def main() -> None:
    from enrichment.config import (
        BATCH_SIZE,
        MAX_WORKERS,
        GROQ_MODEL,
        WORKER_ID,
        STUCK_CLAIM_MINUTES,
        MAX_ENRICHMENT_ATTEMPTS,
    )
    from enrichment.db import claim_batch, count_remaining

    log.info("=" * 60)
    log.info("Catalyst enrichment service starting")
    log.info("  Worker ID         : %s", WORKER_ID)
    log.info("  Groq model        : %s", GROQ_MODEL)
    log.info("  Threads x batch   : %d x %d", MAX_WORKERS, BATCH_SIZE)
    log.info("  Reclaim after     : %d min stuck in 'enriching'", STUCK_CLAIM_MINUTES)
    log.info("  Max attempts/row  : %d", MAX_ENRICHMENT_ATTEMPTS)
    log.info("=" * 60)

    try:
        remaining = count_remaining()
        log.info("Rows currently eligible: %d", remaining)
    except Exception as e:
        log.warning("Could not count remaining rows: %s", e)

    total_ok = total_fail = total_batches = 0
    t_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="enrich") as executor:
        while True:
            batch = claim_batch()
            if not batch:
                log.info("No more questions to process — queue drained.")
                break

            total_batches += 1
            log.info("Batch #%d | claimed %d row(s) | processing...",
                     total_batches, len(batch))

            ok, fail = _process_batch(batch, executor)
            total_ok += ok
            total_fail += fail

            log.info(
                "Batch #%d done | ok=%d fail=%d | running total ok=%d fail=%d",
                total_batches, ok, fail, total_ok, total_fail,
            )

    elapsed = time.monotonic() - t_start
    log.info("=" * 60)
    log.info(
        "Finished in %.1fs | %d batch(es) | ok=%d fail=%d",
        elapsed, total_batches, total_ok, total_fail,
    )
    log.info("=" * 60)


if __name__ == "__main__":
    main()