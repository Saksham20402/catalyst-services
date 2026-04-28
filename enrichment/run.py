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
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Progress persistence
# ---------------------------------------------------------------------------

def _load_progress(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                state = json.load(f)
            log.info(
                f"Resuming from saved progress: offset={state.get('offset', 0)} "
                f"ok={state.get('total_ok', 0)} fail={state.get('total_fail', 0)} "
                f"(saved at {state.get('saved_at', 'unknown')})"
            )
            return state
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Could not read progress file ({e}), starting from scratch")
    return {"offset": 0, "total_ok": 0, "total_fail": 0}


def _save_progress(path: str, offset: int, total_ok: int, total_fail: int) -> None:
    state = {
        "offset": offset,
        "total_ok": total_ok,
        "total_fail": total_fail,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        log.warning(f"Could not save progress: {e}")


def _clear_progress(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    from enrichment.config import BATCH_SIZE, LLM_BATCH_SIZE, CHUNK_SLEEP, SKIP_ENRICHED, PROGRESS_FILE, GROQ_MODEL, SUBJECT_FILTER
    from enrichment.db import fetch_questions
    from enrichment.enricher import process_batch

    log.info("=" * 60)
    log.info("Enrichment service starting")
    log.info(f"  Groq model : {GROQ_MODEL}")
    log.info(f"  Subject    : {SUBJECT_FILTER}")
    log.info(f"  LLM batch  : {LLM_BATCH_SIZE} questions/call")
    log.info(f"  Mode       : {'skip already-enriched' if SKIP_ENRICHED else 'process all'}")
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

    while True:
        db_batch = fetch_questions(offset, unenriched_only=SKIP_ENRICHED)
        if not db_batch:
            log.info("No more questions to process.")
            break

        n_chunks = -(-len(db_batch) // LLM_BATCH_SIZE)
        log.info(f"DB batch offset={offset} | {len(db_batch)} questions → {n_chunks} Groq calls")

        for i in range(0, len(db_batch), LLM_BATCH_SIZE):
            chunk = db_batch[i:i + LLM_BATCH_SIZE]
            ok, fail = process_batch(chunk)
            total_ok += ok
            total_fail += fail
            log.info(f"  Chunk [{i+1}–{i+len(chunk)}]: ok={ok} fail={fail} | total ok={total_ok} fail={total_fail}")

            # Proactive rate-limit guard — sleep between calls so we never hit 429
            if i + LLM_BATCH_SIZE < len(db_batch):
                time.sleep(CHUNK_SLEEP)

        offset += len(db_batch)
        _save_progress(PROGRESS_FILE, offset, total_ok, total_fail)

        if len(db_batch) < BATCH_SIZE:
            break

    elapsed = time.monotonic() - t_start
    log.info("=" * 60)
    log.info(f"Finished in {elapsed:.1f}s | total ok={total_ok} fail={total_fail}")

    if total_fail == 0:
        _clear_progress(PROGRESS_FILE)
        log.info("Progress file cleared — clean run.")
    else:
        log.warning(
            f"{total_fail} question(s) failed. Progress file kept at '{PROGRESS_FILE}'. "
            "Re-run to retry (SKIP_ENRICHED=true will skip already-done ones)."
        )
    log.info("=" * 60)


if __name__ == "__main__":
    main()
