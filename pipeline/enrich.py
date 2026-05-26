import logging

from ingestion.models import PipelineState
from .groq_client import enrich_questions
from .openai_client import enrich_code_questions

log = logging.getLogger(__name__)

_VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def _apply_groq_result(q: dict, result: dict) -> dict | None:
    """Merge a Groq enrichment result onto a question dict. Returns None if unusable."""
    if not result or not result.get("explanation"):
        return None
    enriched = {**q, "explanation": result["explanation"]}
    if (
        result.get("difficulty_correct") is False
        and result.get("difficulty_suggested") in _VALID_DIFFICULTIES
    ):
        enriched["difficulty"] = result["difficulty_suggested"]
    elif not enriched.get("difficulty"):
        enriched["difficulty"] = result.get("difficulty_suggested", "medium")
    return enriched


def _apply_openai_result(q: dict, result: dict) -> dict | None:
    """
    Merge an OpenAI code-enrichment result onto a question dict.
    Applies answer correction and cleaned code snippet. Returns None if unusable.
    """
    if not result or not result.get("explanation"):
        return None

    enriched = {**q, "explanation": result["explanation"]}

    # Update cleaned code snippet if provided
    if result.get("code_snippet_clean"):
        enriched["code_snippet"] = result["code_snippet_clean"]

    # Apply answer correction
    enriched["answer_verified"] = result.get("answer_verified", True)
    if result.get("answer_verified") is False:
        corrected = result.get("corrected_correct_index")
        if corrected is not None and 0 <= int(corrected) < len(q.get("options", [])):
            log.info(
                "id=%s: answer corrected by OpenAI %d → %d",
                q.get("id"), q.get("correct_index"), corrected,
            )
            enriched["correct_index"] = int(corrected)

    enriched["difficulty"] = result.get("difficulty_suggested", "medium")
    # Mark as fully enriched so the standalone enrichment service skips it
    enriched["enrichment_status"] = "enriched"

    return enriched


def enrich_batch(state: PipelineState) -> dict:
    idx = state["current_batch_idx"]
    batch = state["batches"][idx]
    retry_count = state.get("retry_count", 0)

    log.info("Enriching batch %d (%d questions, attempt %d)", idx, len(batch), retry_count + 1)

    # Split into code-snippet questions (OpenAI) and regular questions (Groq)
    code_qs = [q for q in batch if q.get("has_code_snippet")]
    regular_qs = [q for q in batch if not q.get("has_code_snippet")]

    log.info("Batch %d: %d code questions (OpenAI), %d regular questions (Groq)", idx, len(code_qs), len(regular_qs))

    enriched, failed = [], []

    # --- OpenAI: inline enrichment for code questions ---
    if code_qs:
        code_results = enrich_code_questions(code_qs)
        for q in code_qs:
            qid = str(q.get("id"))
            result = code_results.get(qid, {})
            enriched_q = _apply_openai_result(q, result)
            if enriched_q:
                enriched.append(enriched_q)
            else:
                log.warning("id=%s: OpenAI returned no usable result", qid)
                failed.append(q)

    # --- Groq: standard enrichment for regular questions ---
    if regular_qs:
        groq_results = enrich_questions(regular_qs)
        for q in regular_qs:
            qid = str(q.get("id"))
            result = groq_results.get(qid, {})
            enriched_q = _apply_groq_result(q, result)
            if enriched_q:
                enriched.append(enriched_q)
            else:
                log.warning("id=%s: Groq returned no usable result", qid)
                failed.append(q)

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