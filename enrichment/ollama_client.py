import json
import logging

import httpx

from enrichment.config import OLLAMA_BASE_URL, OLLAMA_MODEL

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are an expert exam question analyst. Analyse the question below and respond ONLY with valid JSON — no markdown, no extra text.

{context_block}
Subject: {subject}
Topic: {topic}
Difficulty: {difficulty}
Question: {text}
Options: {options}
Correct Answer: {correct_answer}

Return this exact JSON structure:
{{
  "explanation": "Step-by-step explanation of why the correct answer is right and why the others are wrong.",
  "difficulty_correct": true or false,
  "difficulty_suggested": "easy | medium | hard",
  "is_relevant": true or false,
  "relevance_reason": "One sentence on whether this question fits real exam patterns for the subject."
}}"""


def _build_prompt(q: dict, context: str) -> str:
    options = q.get("options", [])
    correct_index = q.get("correct_index")
    try:
        correct_answer = options[int(correct_index)] if correct_index is not None else "N/A"
    except (IndexError, TypeError, ValueError):
        correct_answer = str(correct_index)

    context_block = f"Web context: {context}\n" if context else ""

    return _PROMPT_TEMPLATE.format(
        context_block=context_block,
        subject=q.get("subject", "General"),
        topic=q.get("topic", ""),
        difficulty=q.get("difficulty", "medium"),
        text=q.get("text", ""),
        options=", ".join(f"{i}. {o}" for i, o in enumerate(options)) if isinstance(options, list) else str(options),
        correct_answer=correct_answer,
    )


def enrich_with_ollama(q: dict, context: str = "") -> dict:
    """Call local Ollama and return the parsed enrichment dict, or {} on failure."""
    prompt = _build_prompt(q, context)
    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json"},
            timeout=120.0,
        )
        response.raise_for_status()
        raw = response.json().get("response", "")
        return json.loads(raw)
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200]
        log.error(f"Ollama HTTP {e.response.status_code} for id={q.get('id')}: {body}")
        if e.response.status_code == 404:
            log.error(f"  → Model '{OLLAMA_MODEL}' not found. Run: ollama pull {OLLAMA_MODEL}")
    except httpx.RequestError as e:
        log.error(f"Ollama connection error for id={q.get('id')}: {e} — is Ollama running?")
    except (json.JSONDecodeError, KeyError) as e:
        log.error(f"Ollama bad response for id={q.get('id')}: {e}")
    return {}