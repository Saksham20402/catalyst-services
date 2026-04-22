import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
QUESTIONS_TABLE: str = os.getenv("QUESTIONS_TABLE", "questions")

# Rows fetched from DB per paginated request
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "50"))

# Ollama
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT: float = float(os.getenv("OLLAMA_TIMEOUT", "90"))
OLLAMA_MAX_RETRIES: int = int(os.getenv("OLLAMA_MAX_RETRIES", "2"))

# Parallel LLM threads — tune to your hardware
MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "4"))

# DuckDuckGo context search — off by default (saves ~1s/question, rarely improves output)
USE_SEARCH: bool = os.getenv("USE_SEARCH", "false").lower() == "true"