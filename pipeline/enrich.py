"""
Pipeline enrichment node — backed by the enrichment module's LLM clients.

Uses enrichment.openai_client.enrich() per question (which falls back to
groq_client.enrich() on rate-limit internally). Parallel via ThreadPoolExecutor.

Converts the numeric difficulty (1–5) returned by the LLM to the string
('easy'/'medium'/'hard') expected by validate.py, and stores the raw
numeric value as 'difficulty_score'.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from enrichment.config import MAX_WORKERS
from enrichment.openai_client import enrich as _enrich_one
from ingestion.models import PipelineState

log = logging.getLogger(__name__)

_PIPELINE_MAX_WORKERS = min(MAX_WORKERS, 4)  # be conservative inside the pipeline


def _is_valid_response(result: dict) -> bool:
    """Reject obvious LLM failures before using the result."""
    if not result:
        return False
    explanation = result.get("explanation")
    if not isinstance(explanation, str) or len(explanation.strip()) < 30:
        return False
    bloom = result.get("bloom_level")
    if not isinstance(bloom, int) or not (1 <= bloom <= 6):
        return False
    difficulty = result.get("difficulty")
    if not isinstance(difficulty, int) or not (1 <= difficulty <= 5):
        return False
    return True


def _difficulty_to_str(d: int) -> str:
    if d <= 2:
        return "easy"
    if d == 3:
        return "medium"
    return "hard"


def _enrich_question(q: dict) -> tuple[dict, dict | None]:
    """Call the LLM for one question. Returns (question, result_or_None)."""
    result = _enrich_one(q)
    if not _is_valid_response(result):
        log.warning("id=%s — LLM returned no usable result", q.get("id"))
        return q, None
    return q, result


def enrich_batch(state: PipelineState) -> dict:
    idx = state["current_batch_idx"]
    batch = state["batches"][idx]
    retry_count = state.get("retry_count", 0)

    log.info(
        "Enriching batch %d (%d questions, attempt %d)",
        idx, len(batch), retry_count + 1,
    )

    enriched: list[dict] = []
    failed: list[dict] = []

    with ThreadPoolExecutor(max_workers=_PIPELINE_MAX_WORKERS) as pool:
        futures = {pool.submit(_enrich_question, q): q for q in batch}
        for future in as_completed(futures):
            q, result = future.result()
            if result is None:
                failed.append(q)
                continue

            enriched_q = {
                **q,
                "explanation": result["explanation"],
                "bloom_level": result["bloom_level"],
                "difficulty": _difficulty_to_str(result["difficulty"]),
                "difficulty_score": result["difficulty"],
            }
            if result.get("distractor_explanations"):
                enriched_q["distractor_explanations"] = result["distractor_explanations"]
            if result.get("is_relevant") is False:
                log.info(
                    "id=%s flagged not_relevant — %s",
                    q.get("id"), (result.get("relevance_reason") or "")[:120],
                )
            enriched.append(enriched_q)

    log.info("Batch %d: %d enriched, %d failed", idx, len(enriched), len(failed))

    is_valid = len(enriched) > 0
    update: dict = {
        "failed": failed,
        "is_batch_valid": is_valid,
        "retry_count": retry_count + 1 if not is_valid else 0,
    }

    if is_valid:
        new_batches = list(state["batches"])
        new_batches[idx] = enriched
        update["batches"] = new_batches

    return update