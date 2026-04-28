import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

QUESTIONS_TABLE: str = os.getenv("QUESTIONS_TABLE", "questions")

# How many rows to fetch from DB per paginated request
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "50"))

# Groq
GROQ_API_KEY: str = os.environ["GROQ_API_KEY"]
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# How many questions to pack into a single Groq API call
LLM_BATCH_SIZE: int = int(os.getenv("LLM_BATCH_SIZE", "10"))

# Seconds to sleep between Groq calls to stay under the free-tier rate limit
CHUNK_SLEEP: float = float(os.getenv("CHUNK_SLEEP", "22"))

# Only process questions matching this subject
SUBJECT_FILTER: str = os.getenv("SUBJECT_FILTER", "Operating Systems")

# Skip questions that already have an explanation
SKIP_ENRICHED: bool = os.getenv("SKIP_ENRICHED", "true").lower() == "true"

# File used to persist batch offset across runs (relative to CWD)
PROGRESS_FILE: str = os.getenv("PROGRESS_FILE", ".enrichment_progress.json")

SUPABASE_HOST = os.getenv("DB_HOST")
SUPABASE_PORT = os.getenv("PORT")
SUPABASE_PASSWORD = os.getenv("PASSWORD")
SUPABASE_USER = os.getenv("USER")
DB_NAME = os.getenv("DB_NAME")