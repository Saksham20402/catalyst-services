import logging

from ingestion.models import PipelineState
from .groq_client import enrich_questions

log = logging.getLogger(__name__)

_VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def enrich_batch(state: PipelineState) -> dict:
    idx = state["current_batch_idx"]
    batch = state["batches"][idx]
    retry_count = state.get("retry_count", 0)

    log.info("Enriching batch %d (%d questions, attempt %d)", idx, len(batch), retry_count + 1)

    results = enrich_questions(batch)

    enriched = []
    failed = []

    for q in batch:
        qid = str(q.get("id"))
        result = results.get(qid, {})

        if not result or not result.get("explanation"):
            log.warning("id=%s: no usable result from Groq", qid)
            failed.append(q)
            continue

        enriched_q = {**q, "explanation": result["explanation"]}

        if (
            result.get("difficulty_correct") is False
            and result.get("difficulty_suggested") in _VALID_DIFFICULTIES
        ):
            log.info("id=%s: difficulty corrected → %s", qid, result["difficulty_suggested"])
            enriched_q["difficulty"] = result["difficulty_suggested"]
        elif not enriched_q.get("difficulty"):
            enriched_q["difficulty"] = result.get("difficulty_suggested", "medium")

        enriched.append(enriched_q)

    log.info("Batch %d: %d enriched, %d failed", idx, len(enriched), len(failed))

    is_valid = len(enriched) > 0

    update: dict = {
        "failed": failed,
        "is_batch_valid": is_valid,
        "retry_count": retry_count + 1 if not is_valid else 0,
    }

    if is_valid:
        # Only replace batches[idx] when enrichment actually produced results.
        # If we overwrite with [] on failure the retry loop sends an empty list
        # to Groq, making recovery impossible.
        new_batches = list(state["batches"])
        new_batches[idx] = enriched
        update["batches"] = new_batches

    return update