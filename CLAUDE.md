# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

Catalyst Services is an MCQ (Multiple Choice Question) ingestion, enrichment, and vector-storage system. It has three independently runnable services:

1. **Embedding API** (`main.py`) — FastAPI microservice serving 384-d normalized vectors via `POST /embed`. Deployed to Cloud Run via `cloudbuild.yaml`.
2. **Enrichment Service** (`enrichment/`) — Fetches unenriched questions from Supabase, calls a local Ollama LLM for explanations and difficulty validation, with DuckDuckGo web context. Parallel via `ThreadPoolExecutor`.
3. **Ingestion Pipeline** (`pipeline/` + `ingestion/`) — LangGraph `StateGraph` that extracts MCQs from Sanfoundry (Playwright) or PDF, validates, writes to Supabase (PostgreSQL), then embeds and upserts to Qdrant.

## Running Services

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in credentials

# Embedding API
uvicorn main:app --reload --port 8000

# Enrichment service (requires Ollama running)
ollama serve &
python -m enrichment.run

# Ingestion pipeline
python -m pipeline.main
```

All services are **checkpoint-resumable** — just re-run the same command after a crash. Enrichment saves to `.enrichment_progress.json`; the pipeline saves to `data/pipeline_checkpoints/{hash}.json`.

## Architecture

### LangGraph Pipeline Flow

```
START → extract → enrich → validate → [route_validation]
                                        ├─ valid   → write_db → qdrant_embed → qdrant_upsert → manage_loop
                                        ├─ retry   → enrich (max 3 times)
                                        └─ invalid → write_dlq → manage_loop
                                                          ↓
                                              [should_continue]
                                              ├─ continue → enrich (next batch)
                                              └─ end → END
```

- **`enrich` and `validate` are currently stubs** — enrich resets retry_count, validate always approves. Real enrichment happens in the standalone `enrichment/` service post-ingestion.
- **`write_db`** (`pipeline/write.py`) does `INSERT ... ON CONFLICT DO NOTHING` — idempotent by `id`.
- **`qdrant_upsert`** upserts to collection `questions1` (384-d, cosine). Embedding backend is selected via env: local SentenceTransformer (default), `EMBED_API_URL` (HTTP to the embedding API), or HF Inference.

### Key Data Model

`QuestionDTO` / `PipelineState` live in `ingestion/models.py`. The state carries: `source`, `batches`, `current_batch_idx`, `is_batch_valid`, `retry_count`, `enriched`, `failed`, `vectors`, `dim`.

### Embedding API

- Model: `all-MiniLM-L6-v2` (384-d), auto-detects MPS → CUDA → CPU.
- Request: `{"text": str}` or `{"texts": [str]}` → response includes `embeddings`, `model`, `dim`.
- HF model cache is at `$HF_HOME` / `/app/.cache` in Docker.

## Environment Variables

| Variable | Purpose |
|---|---|
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Database access |
| `QUESTIONS_TABLE` | Target table (default: `questions`) |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Local LLM for enrichment |
| `VECTOR_STORE_URL`, `VECTOR_DB_KEY` | Qdrant endpoint + auth |
| `EMBED_MODEL` | SentenceTransformer model name |
| `EMBEDDING_DIM` | Vector dimension (384) |
| `EMBED_API_URL` | Optional: URL of running embedding API |
| `BATCH_SIZE`, `MAX_WORKERS` | Enrichment parallelism |
| `SKIP_ENRICHED` | Skip questions that already have explanations |

## Planned Work (Cursor Plan)

A `difficulty_tagging_pipeline` plan is in `.cursor/plans/`. The goal is cold-start difficulty tagging via a classifier + weak-rule labels, evolving to weekly Rasch (1PL IRT) recalibration once per-item attempt data is sufficient. All todos are still `pending`.

## Known Issues

- `pipeline/write.py` has a hardcoded database connection string — it should be moved to `SUPABASE_URL` / env.
- PDF extraction in `pipeline/extarct.py` is a stub (returns empty list).
- No test suite exists yet.
