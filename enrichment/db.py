import logging
from typing import Any

from supabase import create_client, Client

from enrichment.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, QUESTIONS_TABLE, BATCH_SIZE

log = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        log.info("Connecting to Supabase...")
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        log.info("Supabase client ready")
    return _client


def fetch_questions(offset: int = 0, unenriched_only: bool = False) -> list[dict[str, Any]]:
    """Fetch a batch of questions from the database."""
    client = get_client()
    query = client.table(QUESTIONS_TABLE).select("*")
    if unenriched_only:
        query = query.is_("explanation", "null")
    response = query.range(offset, offset + BATCH_SIZE - 1).execute()
    rows = response.data if isinstance(response.data, list) else []
    log.debug(f"Fetched {len(rows)} questions (offset={offset}, unenriched_only={unenriched_only})")
    return rows


def fetch_all_unenriched() -> list[dict[str, Any]]:
    """Fetch all questions that have no explanation yet, in paginated batches."""
    all_rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = fetch_questions(offset, unenriched_only=True)
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < BATCH_SIZE:
            break
        offset += BATCH_SIZE
    log.info(f"Total unenriched questions: {len(all_rows)}")
    return all_rows


def update_question(row_id: Any, updates: dict[str, Any]) -> dict[str, Any]:
    """Push updated fields back for a single question row."""
    client = get_client()
    response = (
        client.table(QUESTIONS_TABLE)
        .update(updates)
        .eq("id", row_id)
        .execute()
    )
    updated = response.data[0] if response.data else {}
    log.debug(f"Updated question id={row_id}")
    return updated