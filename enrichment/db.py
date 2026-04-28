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


def fetch_questions(offset: int = 0, unenriched_only: bool = False) -> list[dict[str, Any]]:
    """Fetch a batch of OS questions from the database."""
    from enrichment.config import SUBJECT_FILTER
    client = get_client()
    query = client.table(QUESTIONS_TABLE).select("*").eq("subject", SUBJECT_FILTER)
    if unenriched_only:
        query = query.is_("explanation", "null")
    response = query.range(offset, offset + BATCH_SIZE - 1).execute()
    rows = response.data if isinstance(response.data, list) else []
    log.debug(f"Fetched {len(rows)} questions (offset={offset}, subject={SUBJECT_FILTER}, unenriched_only={unenriched_only})")
    return rows


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
    updated = response.data[0] if response.data else {}
    log.debug(f"Updated question id={row_id}")
    return updated


def update_questions_batch(updates: list[dict[str, Any]]) -> None:
    """Update multiple question rows, one UPDATE per row (avoids upsert's not-null constraints)."""
    client = get_client()
    for row in updates:
        row_id = row.pop("id")
        client.table(QUESTIONS_TABLE).update(row).eq("id", row_id).execute()
        row["id"] = row_id  # restore so callers aren't surprised
    log.debug(f"Batch updated {len(updates)} questions")
