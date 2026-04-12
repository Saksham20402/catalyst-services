import logging
from typing import Any

from enrichment.config import USE_SEARCH
from enrichment.db import update_question
from enrichment.ollama_client import enrich_with_ollama
from enrichment.search import search_context

log = logging.getLogger(__name__)


def process_question(q: dict[str, Any]) -> bool:
    """
    Full pipeline for a single question:
      1. Optional DuckDuckGo search for context
      2. Ollama enrichment (explanation + difficulty check + relevance check)
      3. Supabase update

    Returns True on success, False on failure.
    """
    qid = q.get("id")
    text = q.get("text", "")
    subject = q.get("subject", "")
    topic = q.get("topic", "")

    # 1. Search context
    context = ""
    if USE_SEARCH and text:
        parts = [p for p in [subject, topic, text[:80]] if p]
        query = " ".join(parts).strip()
        context = search_context(query)

    # 2. LLM call
    result = enrich_with_ollama(q, context)
    if not result:
        log.warning(f"Skipping id={qid} — Ollama returned no usable result")
        return False

    # 3. Build DB update — only touch fields that exist in the schema
    updates: dict[str, Any] = {}

    if result.get("explanation"):
        updates["explanation"] = result["explanation"]

    # Only overwrite difficulty if the model says it's wrong
    if result.get("difficulty_correct") is False and result.get("difficulty_suggested"):
        updates["difficulty"] = result["difficulty_suggested"]
        log.info(f"id={qid}: difficulty corrected {q.get('difficulty')} → {result['difficulty_suggested']}")

    if not updates:
        log.warning(f"id={qid}: nothing to update")
        return False

    update_question(qid, updates)
    log.debug(
        f"id={qid} | difficulty_ok={result.get('difficulty_correct')} "
        f"| relevant={result.get('is_relevant')} | relevance={result.get('relevance_reason', '')[:60]}"
    )
    return True