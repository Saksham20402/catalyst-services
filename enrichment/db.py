import logging
from typing import Any

from supabase import create_client, Client

from enrichment.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, QUESTIONS_TABLE, BATCH_SIZE

log = logging.getLogger(__name__)

_client: Client | None = None

# Pull only what the enrichment pipeline needs — avoids fetching embeddings / large unused columns
_SELECT_FIELDS = "id,text,subject,topic,difficulty,options,correct_index"


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        log.info("Supabase client ready")
    return _client


def fetch_unenriched_batch(offset: int = 0) -> list[dict[str, Any]]:
    """
    Fetch one batch of questions that still need enrichment.

    Filtering by explanation IS NULL at the DB level means:
    - processed rows are excluded before they leave the database
    - always calling with offset=0 is safe — each successful write shrinks the
      result set, so there's no risk of re-processing or skipping rows
    """
    client = get_client()
    response = (
        client.table(QUESTIONS_TABLE)
        .select(_SELECT_FIELDS)
        .is_("explanation", "null")
        .range(offset, offset + BATCH_SIZE - 1)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    log.debug(f"Fetched {len(rows)} unenriched questions (offset={offset})")
    return rows


def bulk_update(updates: list[dict[str, Any]]) -> int:
    """
    Write enrichment results for a batch.  Each item must include "id" plus
    the fields to set.  Uses individual UPDATE calls — upsert is intentionally
    avoided because partial rows (only explanation + difficulty) would violate
    NOT NULL constraints on other columns during the INSERT fallback path.
    """
    if not updates:
        return 0
    client = get_client()
    for item in updates:
        row_id = item["id"]
        fields = {k: v for k, v in item.items() if k != "id"}
        client.table(QUESTIONS_TABLE).update(fields).eq("id", row_id).execute()
    log.debug(f"Updated {len(updates)} rows")
    return len(updates)
