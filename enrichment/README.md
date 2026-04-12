# Enrichment Service

Locally-hosted pipeline that fetches questions from Supabase, enriches them with:
- **Explanation** — step-by-step answer breakdown via a local Ollama LLM
- **Difficulty validation** — flags and corrects mislabelled difficulty levels
- **Relevance check** — verifies the question fits real exam patterns for its subject

Uses DuckDuckGo (free, no API key) to pull web context before each LLM call for better accuracy.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | [python.org](https://python.org) |
| Ollama | latest | [ollama.com](https://ollama.com) |
| Supabase project | — | Already set up |

---

## Setup

### 1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the template and fill in your values:

```bash
cp .env.example .env
```

The minimum required variables are:

```env
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_SERVICE_KEY=<your-service-role-key>
```

All configuration options are described in the [Configuration](#configuration) section below.

### 4. Pull an Ollama model

```bash
ollama pull llama3.2
```

Any model available on [ollama.com/library](https://ollama.com/library) works.  
Larger models (e.g. `llama3.1:70b`) give better explanations but are slower.

### 5. Start Ollama

```bash
ollama serve
```

Leave this running in a separate terminal window.

---

## Running

```bash
python -m enrichment.run
```

The service will:
1. Connect to Supabase and fetch unenriched questions in batches
2. Run DuckDuckGo context search for each question (optional)
3. Call the local Ollama model to generate enrichment
4. Write the explanation and corrected difficulty back to Supabase
5. Save progress after every batch to `.enrichment_progress.json`

---

## Resuming after interruption

If the script is stopped (Ctrl+C, power cut, crash), just re-run it:

```bash
python -m enrichment.run
```

It will detect `.enrichment_progress.json` and resume from the last completed batch.  
Progress is automatically cleared when a run finishes cleanly.

> **Note:** When `SKIP_ENRICHED=true` (the default), the `explanation IS NULL` DB filter
> acts as a second safety net — already-processed questions are never re-sent to the LLM
> even if the offset drifts.

---

## Configuration

All options are set via environment variables (or in `.env`).

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPABASE_URL` | **required** | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | **required** | Supabase service-role key (bypasses RLS) |
| `QUESTIONS_TABLE` | `questions` | Table name to read/write |
| `BATCH_SIZE` | `50` | Rows fetched per DB request |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.2` | Model to use for enrichment |
| `MAX_WORKERS` | `4` | Parallel LLM threads. Set to `2` for CPU-only, `8+` for GPU |
| `USE_SEARCH` | `true` | Enable DuckDuckGo context search before LLM calls |
| `SKIP_ENRICHED` | `true` | Skip questions that already have an explanation |
| `PROGRESS_FILE` | `.enrichment_progress.json` | Path to the progress checkpoint file |

### Tuning for your hardware

| Setup | Recommended `MAX_WORKERS` |
|-------|--------------------------|
| CPU only (e.g. MacBook) | `2` – `4` |
| Single consumer GPU | `4` – `6` |
| Multiple GPUs / high-end workstation | `8` – `16` |

Ollama queues requests internally, so setting workers higher than your hardware can handle will
just add latency per request without improving throughput.

---

## Troubleshooting

**`Ollama HTTP 404` / `model not found`**  
Run `ollama pull <model>` with the model name set in `OLLAMA_MODEL`.

**`Ollama connection error`**  
Make sure Ollama is running: `ollama serve`

**`No questions returned`**  
Check `QUESTIONS_TABLE` is correct and `SUPABASE_SERVICE_KEY` has access.

**Questions not getting updated in Supabase**  
Ensure your service key has `UPDATE` permissions on the questions table (service-role key bypasses RLS by default).

**Script is slow**  
- Increase `MAX_WORKERS` if your machine has headroom
- Set `USE_SEARCH=false` to skip DuckDuckGo lookups (saves ~1s per question)
- Use a smaller/quantized Ollama model (e.g. `llama3.2:1b` instead of `llama3.2`)