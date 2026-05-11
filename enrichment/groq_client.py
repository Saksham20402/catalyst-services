"""
Groq client for question enrichment.

Single responsibility: take a question dict, return a parsed enrichment dict,
or {} on any failure. Rate limits are handled internally with exponential
backoff — the caller never sees a 429.
"""
import json
import logging
import random
import time

from groq import Groq, RateLimitError, APIStatusError

from enrichment.config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    RATE_LIMIT_MAX_RETRIES,
    RATE_LIMIT_BACKOFF_BASE_SECONDS,
)
from enrichment.prompts import build_prompt, EXPLANATION_PROMPT_VERSION

log = logging.getLogger(__name__)

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def _extract_retry_after(err: Exception) -> float | None:
    """Pull a Retry-After hint (in seconds) from a Groq rate-limit error."""
    headers = getattr(getattr(err, "response", None), "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _call_groq_with_retry(system_prompt: str, user_prompt: str, qid) -> str:
    """
    Make the Groq API call with rate-limit backoff. Returns raw response
    content on success, raises on permanent failure or exhausted retries.
    """
    client = _get_client()
    last_err: Exception | None = None

    for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""

        except RateLimitError as e:
            last_err = e
            retry_after = _extract_retry_after(e)
            if retry_after is None:
                # exponential backoff: 5s -> 15s -> 45s (with jitter)
                retry_after = RATE_LIMIT_BACKOFF_BASE_SECONDS * (3 ** (attempt - 1))
            # jitter avoids thundering-herd when many workers hit 429 simultaneously
            sleep_for = retry_after + random.uniform(0, 2)
            log.warning(
                "Rate limited on id=%s (attempt %d/%d) — sleeping %.1fs",
                qid, attempt, RATE_LIMIT_MAX_RETRIES, sleep_for,
            )
            time.sleep(sleep_for)

        except APIStatusError as e:
            # 5xx is retryable, 4xx is not
            status = getattr(e, "status_code", None) or 0
            if 500 <= status < 600 and attempt < RATE_LIMIT_MAX_RETRIES:
                sleep_for = RATE_LIMIT_BACKOFF_BASE_SECONDS * attempt
                log.warning(
                    "Groq %d on id=%s (attempt %d/%d) — sleeping %.1fs",
                    status, qid, attempt, RATE_LIMIT_MAX_RETRIES, sleep_for,
                )
                time.sleep(sleep_for)
                last_err = e
            else:
                raise

    # Exhausted retries
    raise last_err if last_err else RuntimeError("retry loop exited without error")


def enrich(question: dict) -> dict:
    """
    Call Groq with the Catalyst prompt and return parsed enrichment, or {}
    on failure.

    Successful return keys:
        explanation                : str   — 50–110 word principle-first text
        distractor_explanations    : str | None — diagnostic note or None
        bloom_level                : int   — 1-6
        difficulty                 : int   — 1-5
        is_relevant                : bool
        relevance_reason           : str
        _prompt_version            : str   — for audit / re-enrichment filtering
    """
    qid = question.get("id")
    system_prompt, user_prompt = build_prompt(question)

    raw = ""
    try:
        raw = _call_groq_with_retry(system_prompt, user_prompt, qid)
        result = json.loads(raw)
        result["_prompt_version"] = EXPLANATION_PROMPT_VERSION
        return result

    except json.JSONDecodeError as e:
        log.error("Groq returned invalid JSON for id=%s: %s — raw: %.200s",
                  qid, e, raw)
    except RateLimitError:
        log.error("Groq rate limit exhausted for id=%s after %d retries",
                  qid, RATE_LIMIT_MAX_RETRIES)
    except Exception as e:
        log.error("Groq error for id=%s: %s", qid, e)

    return {}