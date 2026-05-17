"""
Supabase access for the enrichment service.

The `claim_batch` function is the linchpin of multi-worker idempotency:
it atomically reserves a batch of rows by transitioning them to 'enriching'
in a single update, then returns only the rows the current worker won.
Other workers will not see those rows on their next fetch.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import create_client, Client

from enrichment.config import (
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
    QUESTIONS_TABLE,
    BATCH_SIZE,
    WORKER_ID,
    STUCK_CLAIM_MINUTES,
    MAX_ENRICHMENT_ATTEMPTS,
)

log = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        log.info("Connecting to Supabase...")
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        log.info("Supabase client ready")
    return _client


def claim_batch() -> list[dict[str, Any]]:
    """
    Atomically claim a batch of questions for this worker.

    Returns rows that were either:
      - in status='raw', OR
      - in status='enriching' but updated_at older than STUCK_CLAIM_MINUTES
        (a previous worker crashed mid-processing)

    The claim works in two phases:
      1. SELECT candidate row IDs matching the eligibility criteria.
      2. UPDATE those rows to status='enriching' with the current timestamp
         and this worker's WORKER_ID. Only rows that were still eligible at
         update time are actually returned — so two workers racing on the
         same SELECT will each only get a non-overlapping subset.

    Because Supabase's PostgREST does row-level locking on UPDATE, the second
    worker's UPDATE of an already-claimed row is a no-op (status no longer
    matches the filter), and that row is excluded from the response.
    """
    client = get_client()
    now = datetime.now(timezone.utc)
    stuck_threshold = (now - timedelta(minutes=STUCK_CLAIM_MINUTES)).isoformat()

    # Phase 1: find candidate IDs.
    # Eligible: status='raw' AND attempts < MAX
    # OR     : status='enriching' AND updated_at < stuck_threshold
    raw_query = (
        client.table(QUESTIONS_TABLE)
        .select("id")
        .eq("enrichment_status", "raw")
        .lt("enrichment_attempts", MAX_ENRICHMENT_ATTEMPTS)
        .limit(BATCH_SIZE)
    )
    raw_response = raw_query.execute()
    raw_ids = [r["id"] for r in (raw_response.data or [])]

    stuck_ids: list[str] = []
    if len(raw_ids) < BATCH_SIZE:
        # Fill remainder with stuck claims
        remaining = BATCH_SIZE - len(raw_ids)
        stuck_query = (
            client.table(QUESTIONS_TABLE)
            .select("id")
            .eq("enrichment_status", "enriching")
            .lt("updated_at", stuck_threshold)
            .lt("enrichment_attempts", MAX_ENRICHMENT_ATTEMPTS)
            .limit(remaining)
        )
        stuck_response = stuck_query.execute()
        stuck_ids = [r["id"] for r in (stuck_response.data or [])]
        if stuck_ids:
            log.warning("Reclaiming %d stuck row(s) from crashed workers", len(stuck_ids))

    candidate_ids = raw_ids + stuck_ids
    if not candidate_ids:
        return []

    # Phase 2: atomically claim them. We update only rows still matching
    # one of the eligible states — a row claimed by another worker between
    # phase 1 and phase 2 will silently be skipped (the filter no longer matches).
    # We do two separate updates to keep each one's status filter strict.
    claimed_rows: list[dict[str, Any]] = []
    claim_payload = {
        "enrichment_status": "enriching",
        "enrichment_error": f"claimed_by:{WORKER_ID}",
    }

    if raw_ids:
        resp = (
            client.table(QUESTIONS_TABLE)
            .update(claim_payload)
            .in_("id", raw_ids)
            .eq("enrichment_status", "raw")
            .execute()
        )
        claimed_rows.extend(resp.data or [])

    if stuck_ids:
        resp = (
            client.table(QUESTIONS_TABLE)
            .update(claim_payload)
            .in_("id", stuck_ids)
            .eq("enrichment_status", "enriching")
            .lt("updated_at", stuck_threshold)
            .execute()
        )
        claimed_rows.extend(resp.data or [])

    log.debug(
        "Worker %s claimed %d row(s) (requested %d)",
        WORKER_ID, len(claimed_rows), len(candidate_ids),
    )
    return claimed_rows


def update_question(row_id: Any, updates: dict[str, Any]) -> dict[str, Any]:
    """Push updated fields back to Supabase for a single question row."""
    client = get_client()
    response = (
        client.table(QUESTIONS_TABLE)
        .update(updates)
        .eq("id", row_id)
        .execute()
    )
    updated = response.data[0] if response.data else {}
    log.debug("Updated question id=%s", row_id)
    return updated


def count_remaining() -> int:
    """Return the count of rows still eligible for enrichment. Used for logging."""
    client = get_client()
    response = (
        client.table(QUESTIONS_TABLE)
        .select("id", count="exact")
        .eq("enrichment_status", "raw")
        .lt("enrichment_attempts", MAX_ENRICHMENT_ATTEMPTS)
        .execute()
    )
    return response.count or 0