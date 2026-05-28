import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

QUESTIONS_TABLE: str = os.getenv("QUESTIONS_TABLE", "questions")

# How many rows to fetch from DB per paginated request
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "50"))

# Groq
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# OpenAI (used for inline code enrichment in the ingestion pipeline)
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Ollama
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")

# Parallel workers for LLM calls (tune to your machine; 4 is safe for CPU Ollama)
MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "4"))

# Set to "false" to skip DuckDuckGo context search
USE_SEARCH: bool = os.getenv("USE_SEARCH", "true").lower() == "true"

# Skip questions that already have an explanation
SKIP_ENRICHED: bool = os.getenv("SKIP_ENRICHED", "true").lower() == "true"

# File used to persist batch offset across runs (relative to CWD)
PROGRESS_FILE: str = os.getenv("PROGRESS_FILE", ".enrichment_progress.json")

SUPABASE_HOST = os.getenv("DB_HOST")
SUPABASE_PORT = os.getenv("PORT")
SUPABASE_PASSWORD = os.getenv("PASSWORD")
SUPABASE_USER = os.getenv("USER")
DB_NAME = os.getenv("DB_NAME")