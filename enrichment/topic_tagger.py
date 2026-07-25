"""
Single-question topic classifier for the v2 OS topic-tagging pass.

Analogous to enricher.py but only updates the `topic` field.
Uses OpenAI (gpt-4o-mini by default) — deterministic temp=0 classification.
"""
import json
import logging
import re
from typing import Any

from openai import OpenAI, RateLimitError, APIStatusError

from enrichment.config import OPENAI_API_KEY, OPENAI_MODEL
from enrichment.topic_prompts import build_topic_prompt, CANONICAL_TOPICS
from enrichment.topic_db import update_topic

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def classify_topic(question: dict[str, Any]) -> str | None:
    """
    Ask OpenAI to classify the question into one canonical OS topic.

    Returns the topic string on success, None on any failure.
    """
    qid = question.get("id")
    system_prompt, user_prompt = build_topic_prompt(question)
    raw = ""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        result = json.loads(raw)
        raw_topic = result.get("topic", "").strip()
        # Strip leading "N. " prefix if the model echoed the numbered list
        topic = re.sub(r"^\d+\.\s*", "", raw_topic)

        if topic not in CANONICAL_TOPICS:
            log.warning("id=%s — model returned unknown topic %r, skipping", qid, topic)
            return None

        return topic

    except RateLimitError:
        log.error("OpenAI rate limit hit for id=%s", qid)
    except APIStatusError as e:
        log.error("OpenAI API error %s for id=%s: %s", e.status_code, qid, e)
    except json.JSONDecodeError as e:
        log.error("Invalid JSON for id=%s: %s — raw: %.200s", qid, e, raw)
    except Exception as e:
        log.error("Unexpected error for id=%s: %s", qid, e)

    return None


def process_question(question: dict[str, Any]) -> bool:
    """
    Classify and persist the topic for a single question.

    Returns True on success, False on failure.
    """
    qid = question.get("id")
    old_topic = question.get("topic", "")

    topic = classify_topic(question)
    if topic is None:
        return False

    if topic != old_topic:
        log.info("id=%s — %r → %r", qid, old_topic, topic)
    else:
        log.debug("id=%s — topic unchanged (%r)", qid, topic)

    update_topic(qid, topic)
    return True