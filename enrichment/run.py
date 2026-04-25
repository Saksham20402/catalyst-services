"""
Enrichment service — optimised entry point.

Run:
    python -m enrichment.run

Idempotency guarantee
---------------------
The DB query always filters by  explanation IS NULL.  Once a question is
successfully enriched its explanation field is non-null, so it is invisible to
every subsequent fetch — no progress file, no offset bookkeeping, no risk of
re-processing.  Re-running the script after an interruption is safe: it picks
up exactly where it left off at zero extra cost.

Batch sizing demo
-----------------
Set BATCH_SIZE=10 (env or .env) to verify 10-question batches.  Each batch
log line shows which questions are processed; re-running shows they are not
repeated because the DB filter excludes them.
"""
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def main() -> None:
    from enrichment.config import (
        BATCH_SIZE, MAX_WORKERS, OLLAMA_MODEL, OLLAMA_BASE_URL, USE_SEARCH
    )
    from enrichment.db import fetch_unenriched_batch
    from enrichment.enricher import process_batch

    log.info("=" * 60)
    log.info("Enrichment service starting")
    log.info(f"  Ollama   : {OLLAMA_BASE_URL}  model={OLLAMA_MODEL}")
    log.info(f"  Workers  : {MAX_WORKERS}  batch_size={BATCH_SIZE}")
    log.info(f"  Search   : {'on' if USE_SEARCH else 'off'}")
    log.info(f"  Idempotency: explanation IS NULL filter (DB-level, no progress file)")
    log.info("=" * 60)

    t_start = time.monotonic()
    total_ok = total_fail = 0
    batch_num = 0

    while True:
        # Always fetch from offset=0 with the unenriched filter.
        # Rows we wrote in the previous iteration now have explanation IS NOT NULL
        # and are excluded by the DB query — we never see them again.
        batch = fetch_unenriched_batch(offset=0)
        if not batch:
            log.info("No unenriched questions remaining — all done.")
            break

        batch_num += 1
        ids = [q.get("id") for q in batch]
        log.info(f"--- Batch {batch_num} | {len(batch)} questions | ids: {ids} ---")

        ok, fail = process_batch(batch)
        total_ok += ok
        total_fail += fail

        log.info(
            f"Batch {batch_num} done | ok={ok} fail={fail} | "
            f"running total ok={total_ok} fail={total_fail}"
        )

        if len(batch) < BATCH_SIZE:
            # Last partial batch — no more rows to fetch
            break

    elapsed = time.monotonic() - t_start
    log.info("=" * 60)
    log.info(f"Finished in {elapsed:.1f}s | total ok={total_ok} fail={total_fail}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()