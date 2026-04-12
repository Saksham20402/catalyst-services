"""
Entry point for the enrichment service.

Run with:
    python -m enrichment.run

Resume after interruption:
    Re-run the same command — progress is automatically restored from
    .enrichment_progress.json (or the path set in PROGRESS_FILE).
"""
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Batch processing
# ---------------------------------------------------------------------------

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
            log.error(f"Unhandled error for id={q.get('id')}: {e}")
            fail += 1
    return ok, fail


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    from enrichment.config import BATCH_SIZE, MAX_WORKERS, SKIP_ENRICHED, PROGRESS_FILE, OLLAMA_MODEL, OLLAMA_BASE_URL
    from enrichment.db import fetch_questions

    log.info("=" * 60)
    log.info("Enrichment service starting")
    log.info(f"  Ollama   : {OLLAMA_BASE_URL}  model={OLLAMA_MODEL}")
    log.info(f"  Workers  : {MAX_WORKERS}  batch_size={BATCH_SIZE}")
    log.info(f"  Mode     : {'skip already-enriched' if SKIP_ENRICHED else 'process all'}")
    log.info("=" * 60)

    progress = _load_progress(PROGRESS_FILE)
    offset = progress["offset"]
    total_ok = progress["total_ok"]
    total_fail = progress["total_fail"]

    t_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="enrich") as executor:
        while True:
            batch = fetch_questions(offset, unenriched_only=SKIP_ENRICHED)
            if not batch:
                log.info("No more questions to process.")
                break

            log.info(f"Batch offset={offset} | processing {len(batch)} questions ...")
            ok, fail = _process_batch(batch, executor)
            total_ok += ok
            total_fail += fail

            log.info(
                f"Batch done | ok={ok} fail={fail} | "
                f"running total ok={total_ok} fail={total_fail}"
            )

            offset += len(batch)
            _save_progress(PROGRESS_FILE, offset, total_ok, total_fail)

            if len(batch) < BATCH_SIZE:
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
            f"Re-run to retry failed questions (set SKIP_ENRICHED=true to skip already-done ones)."
        )
    log.info("=" * 60)


if __name__ == "__main__":
    main()