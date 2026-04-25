import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from enrichment.config import MAX_WORKERS, USE_SEARCH
from enrichment.db import bulk_update
from enrichment.ollama_client import enrich_with_ollama
from enrichment.search import search_context

log = logging.getLogger(__name__)

_VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def _enrich_one(q: dict[str, Any]) -> dict[str, Any] | None:
    """
    Enrich a single question.

    Returns a dict ready for bulk_upsert (includes "id"), or None on failure.
    The LLM always assesses difficulty independently — we always write back
    whatever it returns (no conditional 'was it wrong?' gate).
    """
    qid = q.get("id")
    log.info(f"  id={qid}: starting ...")

    context = ""
    if USE_SEARCH:
        parts = [p for p in [q.get("subject", ""), q.get("topic", ""), q.get("text", "")[:80]] if p]
        log.info(f"  id={qid}: searching ...")
        context = search_context(" ".join(parts))

    log.info(f"  id={qid}: calling Ollama ...")
    result = enrich_with_ollama(q, context)
    if not result:
        log.warning(f"id={qid}: Ollama returned nothing — skipping")
        return None

    explanation = result.get("explanation", "").strip()
    if not explanation:
        log.warning(f"id={qid}: empty explanation — skipping")
        return None

    update: dict[str, Any] = {"id": qid, "explanation": explanation}

    difficulty = result.get("difficulty", "").strip().lower()
    if difficulty in _VALID_DIFFICULTIES:
        old = q.get("difficulty", "")
        if difficulty != old:
            log.info(f"id={qid}: difficulty {old!r} → {difficulty!r}")
        update["difficulty"] = difficulty
    else:
        log.warning(f"id={qid}: unexpected difficulty value {difficulty!r} — not updating")

    return update


def process_batch(questions: list[dict[str, Any]]) -> tuple[int, int]:
    """
    Enrich all questions in the batch in parallel, then write all results in
    one bulk DB round-trip.

    Returns (ok_count, fail_count).
    """
    updates: list[dict[str, Any]] = []
    fail = 0
    total = len(questions)
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="enrich") as executor:
        futures = {executor.submit(_enrich_one, q): q for q in questions}
        for future in as_completed(futures):
            q = futures[future]
            qid = q.get("id")
            done += 1
            try:
                update = future.result()
                if update is not None:
                    difficulty = update.get("difficulty", "?")
                    log.info(
                        f"  [{done}/{total}] id={qid} ready — "
                        f"difficulty={difficulty!r}  "
                        f"explanation={len(update.get('explanation',''))}chars"
                    )
                    updates.append(update)
                else:
                    log.warning(f"  [{done}/{total}] id={qid} FAILED — skipped")
                    fail += 1
            except Exception as e:
                log.error(f"  [{done}/{total}] id={qid} ERROR: {e}")
                fail += 1

    log.info(f"Writing {len(updates)} rows to DB ...")
    ok = bulk_update(updates)
    log.info(f"Write complete — {ok} written, {fail} failed")
    return ok, fail
