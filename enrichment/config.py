"""
Catalyst enrichment service — configuration.

All settings are read from environment variables (or a .env file).
Only Supabase credentials and the Groq API key are required.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Supabase ────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
QUESTIONS_TABLE: str = os.getenv("QUESTIONS_TABLE", "questions")

# ── Groq (fallback when OpenAI is throttled) ────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# ── OpenAI (fallback when Groq is throttled) ────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
# gpt-4o-mini: cheap, fast, accurate — good fallback for enrichment
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ── Rate-limit handling ─────────────────────────────────────────────────────
# Max retry attempts when Groq returns 429. Each attempt sleeps longer:
# base * 3^(attempt-1) → 5s, 15s, 45s by default.
RATE_LIMIT_MAX_RETRIES: int = int(os.getenv("RATE_LIMIT_MAX_RETRIES", "3"))
RATE_LIMIT_BACKOFF_BASE_SECONDS: float = float(
    os.getenv("RATE_LIMIT_BACKOFF_BASE_SECONDS", "5")
)

# ── Concurrency / idempotency ───────────────────────────────────────────────
# Unique tag for this worker — written into enrichment_error column on claim
# so you can see which worker is processing which row. Defaults to hostname+pid.
import socket
WORKER_ID: str = os.getenv(
    "WORKER_ID",
    f"{socket.gethostname()}-{os.getpid()}",
)

# Reclaim rows stuck in 'enriching' status older than this many minutes.
# Protects against crashed workers leaving rows locked forever.
STUCK_CLAIM_MINUTES: int = int(os.getenv("STUCK_CLAIM_MINUTES", "15"))

# Don't retry rows that have already failed this many times.
MAX_ENRICHMENT_ATTEMPTS: int = int(os.getenv("MAX_ENRICHMENT_ATTEMPTS", "3"))

# ── Runtime ─────────────────────────────────────────────────────────────────
# Rows fetched and claimed per batch
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "50"))

# Parallel workers for Groq calls. Groq is fast — start at 8, tune up if no
# rate-limit warnings appear.
MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "8"))

# Skip questions that already have an explanation (enrichment_status != "raw")
SKIP_ENRICHED: bool = os.getenv("SKIP_ENRICHED", "true").lower() == "true"