"""
OpenAI fallback client for question enrichment.

Same interface as groq_client.enrich() — drop-in replacement used when
Groq rate-limits are exhausted. Uses gpt-4o-mini by default (cheap, fast,
accurate enough for MCQ enrichment).
"""
import json
import logging

from openai import OpenAI, RateLimitError, APIStatusError

from enrichment.config import OPENAI_API_KEY, OPENAI_MODEL, GROQ_API_KEY
from enrichment.prompts import build_prompt, EXPLANATION_PROMPT_VERSION

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set — cannot use OpenAI fallback")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def enrich(question: dict) -> dict:
    """
    Call OpenAI with the Catalyst prompt and return parsed enrichment, or {}
    on failure.

    Return keys are identical to groq_client.enrich():
        explanation, distractor_explanations, bloom_level, difficulty,
        is_relevant, relevance_reason, _prompt_version
    """
    qid = question.get("id")
    system_prompt, user_prompt = build_prompt(question)
    raw = ""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        result = json.loads(raw)
        result["_prompt_version"] = EXPLANATION_PROMPT_VERSION
        result["_backend"] = "openai"
        return result

    except RateLimitError as e:
        log.warning("OpenAI rate limit for id=%s — falling back to Groq", qid)
        if GROQ_API_KEY:
            from enrichment.groq_client import enrich as groq_enrich
            return groq_enrich(question)
        log.error("No GROQ_API_KEY set — cannot fall back. Dropping id=%s", qid)
    except APIStatusError as e:
        log.error("OpenAI API error %s for id=%s: %s", e.status_code, qid, e)
    except json.JSONDecodeError as e:
        log.error("OpenAI returned invalid JSON for id=%s: %s — raw: %.200s", qid, e, raw)
    except Exception as e:
        log.error("OpenAI error for id=%s: %s", qid, e)

    return {}
