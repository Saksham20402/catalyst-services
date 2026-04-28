"""
One-time script to backfill question vectors in Qdrant from Supabase.

Usage:
  python -m pipeline.script

Behavior:
  - Reads questions from Supabase in batches.
  - Defaults difficulty to "Medium" when difficulty is null/empty.
  - Embeds question text (+ topic + subject) using existing pipeline embed logic.
  - Upserts vectors into Qdrant with payload schema.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor
from qdrant_client.models import PointStruct
from dotenv import load_dotenv

# Load local .env before importing modules that read env at import time. load_env before importing modules that might need it
# how do imports and django in general work how does application loads apps and settings.py and models in general the order
# init file and py cahces and their usages. loading env file before importing modules that might need it. singleton service pattern in django
# gc in python and the refernce counter in python for it. mtv architecture and lazy loading of the queries and iterators and n+1 problem etc
load_dotenv()

from pipeline.qdrant_upsert import (
    QDRANT_COLLECTION,
    _encode_texts,
    _ensure_collection,
    _get_qdrant_client,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DEFAULT_DIFFICULTY = "Medium"
FETCH_BATCH_SIZE = max(1, int(os.getenv("BACKFILL_BATCH_SIZE", "200")))
QUESTIONS_TABLE = os.getenv("QUESTIONS_TABLE", "questions")


def _db_params_from_env() -> dict[str, Any]:
    """
    Build psycopg2 connection params from either SUPABASE_URL or DB_* env vars.
    """
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    if supabase_url:
        parsed = urlparse(supabase_url)
        if parsed.scheme.startswith("postgres"):
            dbname = (parsed.path or "/postgres").lstrip("/")
            return {
                "dbname": dbname or "postgres",
                "user": parsed.username,
                "password": parsed.password,
                "host": parsed.hostname,
                "port": parsed.port or 5432,
                "sslmode": "require",
            }

    host = os.getenv("DB_HOST")
    user = os.getenv("DB_USER") or os.getenv("SUPABASE_USER")
    password = os.getenv("DB_PASSWORD") or os.getenv("SUPABASE_PASSWORD")
    port = os.getenv("DB_PORT") or os.getenv("SUPABASE_PORT") or "6543"
    dbname = os.getenv("DB_NAME", "postgres")

    if not host or not user or not password:
        raise RuntimeError(
            "Missing DB credentials. Set SUPABASE_URL (postgres://...) or DB_HOST/DB_USER/DB_PASSWORD."
        )

    return {
        "dbname": dbname,
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "sslmode": "require",
    }


def _normalize_difficulty(raw: Any) -> str:
    value = (str(raw).strip() if raw is not None else "")
    return value if value else DEFAULT_DIFFICULTY


def _embedding_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        text = (row.get("text") or "").strip()
        topic = (row.get("topic") or "").strip()
        subject = (row.get("subject") or "").strip()
        line = " ".join([text, topic, subject]).strip()
        lines.append(line if line else " ")
    return lines


def _fetch_questions(cur, offset: int, limit: int) -> list[dict[str, Any]]:
    query = f"""
        SELECT id, text, topic, subject, difficulty, options, correct_index, source
        FROM {QUESTIONS_TABLE}
        WHERE subject = 'Operating Systems'
        ORDER BY id
        LIMIT %s OFFSET %s
    """
    cur.execute(query, (limit, offset))
    return list(cur.fetchall())


def _points_for_batch(rows: list[dict[str, Any]]) -> list[PointStruct]:
    lines = _embedding_lines(rows)
    vectors = _encode_texts(lines)
    if vectors.ndim != 2 or vectors.shape[0] != len(rows):
        raise RuntimeError(f"Unexpected vectors shape {vectors.shape} for {len(rows)} rows")

    points: list[PointStruct] = []
    for row, vec in zip(rows, vectors):
        qid = str(row["id"])
        difficulty = _normalize_difficulty(row.get("difficulty"))
        payload = {
            "question_id": qid,
            "difficulty": difficulty,
            "schema": {
                "id": qid,
                "text": row.get("text"),
                "options": row.get("options"),
                "correct_index": row.get("correct_index"),
                "topic": row.get("topic"),
                "subject": row.get("subject"),
                "source": row.get("source"),
                "difficulty": difficulty,
            },
        }
        points.append(PointStruct(id=qid, vector=vec.tolist(), payload=payload))
    return points


def backfill_qdrant() -> None:
    db_params = _db_params_from_env()
    client = _get_qdrant_client()
    total = 0
    offset = 0
    collection_ready = False

    with psycopg2.connect(**db_params) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            while True:
                rows = _fetch_questions(cur, offset=offset, limit=FETCH_BATCH_SIZE)
                if not rows:
                    break

                points = _points_for_batch(rows)
                if points and not collection_ready:
                    vector_dim = len(points[0].vector)
                    _ensure_collection(client, QDRANT_COLLECTION, vector_dim)
                    collection_ready = True

                if points:
                    client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)

                total += len(points)
                offset += FETCH_BATCH_SIZE
                log.info(
                    "Upserted batch=%s points=%s total=%s collection=%s",
                    (offset // FETCH_BATCH_SIZE),
                    len(points),
                    total,
                    QDRANT_COLLECTION,
                )

    log.info("Backfill completed. Total points upserted: %s", total)


if __name__ == "__main__":
    backfill_qdrant()