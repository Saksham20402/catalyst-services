# Catalyst Enrichment Service

Fetches questions from Supabase, enriches them via the Groq API, and writes
the enrichment back. Safe to run in parallel across multiple machines with
different Groq keys — each worker atomically claims its own batch.

Each enriched question gains:

- **explanation** — principle-first text shown after a student answers
- **distractor_explanations** — diagnostic note when wrong options are trappy (nullable)
- **bloom_level** — integer 1–6 (Bloom's taxonomy)
- **difficulty** — integer 1–5
- **is_relevant / relevance_reason** — quality flag for content review

---

## How parallel-safe claiming works

Multiple workers can run simultaneously without re-enriching the same question.
The mechanism is database-level, not file-based:

1. `claim_batch` selects candidate row IDs (`status='raw'` OR stuck in
   `'enriching'` for too long).
2. It immediately runs a bulk `UPDATE ... SET status='enriching'` filtered by
   the original status. Rows already claimed by another worker fail the
   filter and are silently excluded.
3. Only successfully-updated rows are returned. The current worker then
   processes them; other workers never see them.

Crashed workers are recovered automatically — `STUCK_CLAIM_MINUTES` (default 15)
controls how long a row can sit in `'enriching'` before another worker
reclaims it.

Failed rows hit a per-row attempts cap (`MAX_ENRICHMENT_ATTEMPTS`, default 3),
so a permanently-bad row doesn't loop forever.

---

## Rate-limit handling

If Groq returns 429, the Groq client sleeps and retries inside the same call —
the rest of the pipeline doesn't see the rate limit. Default schedule:
**5s → 15s → 45s** with jitter. After 3 retries the question is marked failed.

If Groq's response includes a `Retry-After` header, we honour it instead of
the exponential schedule.

5xx errors get a shorter backoff (5s → 10s → 15s); 4xx errors fail immediately.

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Supabase project | configured with `questions` table |
| Groq API key | from [console.groq.com](https://console.groq.com) |

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your keys
```

Required env vars:

```env
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>
GROQ_API_KEY=<your-groq-key>
```

---

## Running

Single worker:

```bash
python -m enrichment.run
```

Multiple parallel workers (different machines or terminals — give each its
own Groq key and a unique `WORKER_ID`):

```bash
# Machine A
WORKER_ID=worker-A GROQ_API_KEY=gsk_xxx python -m enrichment.run

# Machine B (different key)
WORKER_ID=worker-B GROQ_API_KEY=gsk_yyy python -m enrichment.run
```

Both workers run until the queue drains. Stopping one is safe — its
in-flight rows will be reclaimed by another worker after
`STUCK_CLAIM_MINUTES`.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SUPABASE_URL` | **required** | Project URL |
| `SUPABASE_SERVICE_KEY` | **required** | Service-role key (bypasses RLS) |
| `GROQ_API_KEY` | **required** | Groq API key |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Any Groq-supported model |
| `QUESTIONS_TABLE` | `questions` | Table to read/write |
| `BATCH_SIZE` | `50` | Rows claimed per batch |
| `MAX_WORKERS` | `8` | Parallel Groq threads per worker process |
| `WORKER_ID` | `<host>-<pid>` | Identifier written to `enrichment_error` on claim |
| `STUCK_CLAIM_MINUTES` | `15` | Reclaim window for crashed workers |
| `MAX_ENRICHMENT_ATTEMPTS` | `3` | Hard cap on retries per row |
| `RATE_LIMIT_MAX_RETRIES` | `3` | Per-call retry attempts on 429 |
| `RATE_LIMIT_BACKOFF_BASE_SECONDS` | `5` | First backoff sleep (then ×3 each attempt) |

---

## Database requirement

The `questions` table must have an `updated_at` column that auto-updates on
write (standard Supabase pattern). The stuck-claim recovery uses this column
to detect crashed workers. If it doesn't exist yet, add it:

```sql
ALTER TABLE questions
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

CREATE OR REPLACE FUNCTION set_updated_at()
  RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER questions_updated_at
  BEFORE UPDATE ON questions
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

---

## Troubleshooting

**`Rate limited on id=…`** — Normal under load. The Groq client sleeps and
retries. If you see it constantly, reduce `MAX_WORKERS`.

**`Reclaiming N stuck rows from crashed workers`** — A previous run died
mid-batch. The current worker is picking those up. Normal.

**Two workers, very low throughput** — Check that both have valid Groq keys
and aren't hitting 429 constantly. Reduce `MAX_WORKERS` per worker if so.

**Questions not updating in Supabase** — Confirm the service-role key has
`UPDATE` permission on the questions table.