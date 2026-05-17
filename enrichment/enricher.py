"""
Single-question enrichment pipeline.

By the time process_question is called, the row has already been atomically
claimed (status='enriching') by db.claim_batch. We just need to call Groq,
validate, and write the final state.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from enrichment.db import update_question
from enrichment.groq_client import enrich

log = logging.getLogger(__name__)


def _is_valid_response(result: dict) -> bool:
    """Minimal validation — reject the obvious failures before writing to DB."""
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


def process_question(q: dict[str, Any]) -> bool:
    """
    Enrich a single question. The row is already claimed (status='enriching')
    by claim_batch — this function only needs to call Groq and finalise.

    Returns True on success, False on failure.
    """
    qid = q.get("id")
    prev_attempts = q.get("enrichment_attempts") or 0

    result = enrich(q)

    if not _is_valid_response(result):
        log.warning("id=%s — Groq returned no usable result", qid)
        update_question(qid, {
            "enrichment_status": "failed",
            "enrichment_attempts": prev_attempts + 1,
            "enrichment_error": "invalid_or_empty_groq_response",
        })
        return False

    updates: dict[str, Any] = {
        "explanation": result["explanation"],
        "bloom_level": result["bloom_level"],
        "difficulty": result["difficulty"],
        "explanation_source": "llm_generated",
        "bloom_level_source": "llm_classified",
        "difficulty_source": "llm_estimated",
        "enrichment_status": "enriched",
        "enrichment_attempts": prev_attempts + 1,
        "enriched_at": datetime.now(timezone.utc).isoformat(),
        "enrichment_error": None,
    }

    distractors = result.get("distractor_explanations")
    if isinstance(distractors, str) and distractors.strip():
        updates["distractor_explanations"] = distractors.strip()

    if result.get("is_relevant") is False:
        log.info("id=%s flagged not_relevant — %s",
                 qid, (result.get("relevance_reason") or "")[:120])

    update_question(qid, updates)
    log.debug("id=%s enriched | bloom=%s difficulty=%s",
              qid, result["bloom_level"], result["difficulty"])
    return True