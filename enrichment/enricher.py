import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from enrichment.db import update_questions_batch
from enrichment.ollama_client import enrich_batch

log = logging.getLogger(__name__)

_VALID_DIFFICULTIES = {"easy", "medium", "hard"}

def process_batch(questions: list[dict[str, Any]]) -> tuple[int, int]:
    """
    Enrich a batch of questions with a single Groq API call, then write all
    results to DB in one upsert. Returns (ok_count, fail_count).
    """
    results = enrich_batch(questions)
    ok = fail = 0
    db_updates: list[dict[str, Any]] = []

    for q in questions:
        qid = q.get("id")
        result = results.get(str(qid), {})

        if not result or not result.get("explanation"):
            log.warning(f"id={qid}: no usable result — will retry on next run")
            fail += 1
            continue

        update: dict[str, Any] = {"id": qid, "explanation": result["explanation"]}

        if result.get("difficulty_correct") is False and result.get("difficulty_suggested"):
            update["difficulty"] = result["difficulty_suggested"]
            log.info(f"id={qid}: difficulty corrected → {result['difficulty_suggested']}")

        db_updates.append(update)
        ok += 1

    if db_updates:
        update_questions_batch(db_updates)

    return ok, fail
