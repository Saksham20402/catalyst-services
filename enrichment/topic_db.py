"""
DB helpers for the topic-classification pass (v2).

No claim/lock machinery needed — topic tagging is idempotent, so two runs
on the same question just overwrite with the same value.  We use simple
offset-based pagination tracked in a progress file.

For retrying failures, use fetch_unclassified_batch() which filters to rows
whose topic is still not one of the 8 canonical values.
"""
import json
import logging
import os
from typing import Any

from supabase import Client

from enrichment.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, QUESTIONS_TABLE, BATCH_SIZE
from enrichment.topic_prompts import CANONICAL_TOPICS

log = logging.getLogger(__name__)

TOPIC_PROGRESS_FILE = os.getenv("TOPIC_PROGRESS_FILE", ".topic_v2_progress.json")
SUBJECT_FILTER = "Operating Systems"

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        from supabase import create_client
        log.info("Connecting to Supabase (topic-v2)...")
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


# ---------------------------------------------------------------------------
# Progress file helpers (offset tracking)
# ---------------------------------------------------------------------------

def _load_offset() -> int:
    try:
        with open(TOPIC_PROGRESS_FILE) as f:
            return int(json.load(f).get("offset", 0))
    except (FileNotFoundError, ValueError, KeyError):
        return 0


def _save_offset(offset: int) -> None:
    with open(TOPIC_PROGRESS_FILE, "w") as f:
        json.dump({"offset": offset}, f)


def reset_progress() -> None:
    """Call this to restart from the beginning (e.g. after a full pass)."""
    _save_offset(0)
    log.info("Topic-v2 progress reset to offset 0")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_batch(offset: int | None = None) -> tuple[list[dict[str, Any]], int]:
    """
    Return (rows, next_offset).

    If offset is None, reads from the progress file.
    Returns an empty list when there are no more rows.
    """
    client = _get_client()
    if offset is None:
        offset = _load_offset()

    response = (
        client.table(QUESTIONS_TABLE)
        .select("id, text, options, correct_index, explanation, topic")
        .eq("subject", SUBJECT_FILTER)
        .range(offset, offset + BATCH_SIZE - 1)
        .execute()
    )

    rows = response.data or []
    next_offset = offset + len(rows)
    return rows, next_offset


def save_progress(offset: int) -> None:
    _save_offset(offset)


def update_topic(row_id: Any, topic: str) -> None:
    """Write the new canonical topic back to Supabase."""
    client = _get_client()
    client.table(QUESTIONS_TABLE).update({"topic": topic}).eq("id", row_id).execute()
    log.debug("id=%s → topic set to %r", row_id, topic)


def fetch_unclassified_batch(offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """
    Return (rows, next_offset) for OS questions whose topic is NOT yet one of
    the 8 canonical values — i.e. the ones that failed or were skipped.

    Uses offset pagination so callers can loop until the list is empty.
    """
    client = _get_client()

    response = (
        client.table(QUESTIONS_TABLE)
        .select("id, text, options, correct_index, explanation, topic")
        .eq("subject", SUBJECT_FILTER)
        .not_.in_("topic", CANONICAL_TOPICS)
        .range(offset, offset + BATCH_SIZE - 1)
        .execute()
    )

    rows = response.data or []
    return rows, offset + len(rows)


def count_unclassified() -> int:
    """Count OS questions that still have a non-canonical topic."""
    client = _get_client()
    response = (
        client.table(QUESTIONS_TABLE)
        .select("id", count="exact")
        .eq("subject", SUBJECT_FILTER)
        .not_.in_("topic", CANONICAL_TOPICS)
        .execute()
    )
    return response.count or 0


def count_total() -> int:
    """Total OS questions — used for progress logging."""
    client = _get_client()
    response = (
        client.table(QUESTIONS_TABLE)
        .select("id", count="exact")
        .eq("subject", SUBJECT_FILTER)
        .execute()
    )
    return response.count or 0