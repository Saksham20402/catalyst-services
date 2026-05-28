"""
Test runner for the enrich → validate → write_db → qdrant_embed → qdrant_upsert chain.

Bypasses the web scraper entirely by injecting synthetic questions directly into
the graph state (batches pre-populated, current_batch_idx=0).  Only 1 batch is
processed (max_batches=1) so the run completes in seconds.

Usage:
    python -m pipeline.test_pipeline

Env notes:
  - Requires GROQ_API_KEY (live Groq call is made).
  - Set QDRANT_SKIP=1 to skip the Qdrant upsert if you only want to test DB writes.
  - Set QDRANT_SKIP=0 (or unset) to test the full chain including Qdrant.
"""

import logging
import uuid

from .main_graph import build_graph

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
# Silence noisy third-party wire-level loggers
for _noisy in (
    "httpcore", "httpx", "groq._base_client", "groq", "hpack",
    "urllib3", "filelock", "sentence_transformers", "transformers",
    "huggingface_hub", "fsspec",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Synthetic questions — deliberately valid so they pass the validator cleanly.
# Using fixed UUIDs so re-runs hit ON CONFLICT DO NOTHING in Supabase.
# ---------------------------------------------------------------------------
_SYNTHETIC_QUESTIONS = [
    {
        "id": "11111111-aaaa-4aaa-aaaa-111111111111",
        "text": "Which scheduling algorithm gives the minimum average waiting time for a given set of processes?",
        "options": [
            "First Come First Served",
            "Shortest Job First",
            "Round Robin",
            "Priority Scheduling",
        ],
        "correct_index": 1,
        "topic": "CPU Scheduling",
        "subject": "Operating Systems",
        "source": "https://www.sanfoundry.com/operating-system-questions-answers-basics/",
        "difficulty": "medium",
    },
    {
        "id": "22222222-bbbb-4bbb-bbbb-222222222222",
        "text": "What is the primary purpose of a page table in virtual memory management?",
        "options": [
            "To store the contents of RAM",
            "To map virtual addresses to physical addresses",
            "To allocate disk space for processes",
            "To schedule processes for CPU execution",
        ],
        "correct_index": 1,
        "topic": "Memory Management",
        "subject": "Operating Systems",
        "source": "https://www.sanfoundry.com/operating-system-questions-answers-basics/",
        "difficulty": "easy",
    },
    {
        "id": "33333333-cccc-4ccc-cccc-333333333333",
        "text": "Which of the following conditions is NOT required for a deadlock to occur?",
        "options": [
            "Mutual Exclusion",
            "Hold and Wait",
            "Preemption",
            "Circular Wait",
        ],
        "correct_index": 2,
        "topic": "Deadlocks",
        "subject": "Operating Systems",
        "source": "https://www.sanfoundry.com/operating-system-questions-answers-basics/",
        "difficulty": "medium",
    },
    {
        "id": "44444444-dddd-4ddd-dddd-444444444444",
        "text": "In a semaphore-based solution to the critical section problem, which operation decrements the semaphore value?",
        "options": [
            "signal()",
            "wait()",
            "lock()",
            "acquire()",
        ],
        "correct_index": 1,
        "topic": "Process Synchronization",
        "subject": "Operating Systems",
        "source": "https://www.sanfoundry.com/operating-system-questions-answers-basics/",
        "difficulty": "easy",
    },
    {
        "id": "55555555-eeee-4eee-eeee-555555555555",
        "text": "Which page replacement algorithm suffers from Belady's anomaly?",
        "options": [
            "Optimal Page Replacement",
            "Least Recently Used",
            "First In First Out",
            "Second Chance",
        ],
        "correct_index": 2,
        "topic": "Memory Management",
        "subject": "Operating Systems",
        "source": "https://www.sanfoundry.com/operating-system-questions-answers-basics/",
        "difficulty": "hard",
    },
]


def run_test():
    app = build_graph()

    initial_state = {
        # Source is only used for checkpointing; no scraping happens.
        "source": "TEST_SYNTHETIC",
        "batch_size": 20,
        "dlq_path": "data/failed_batches.jsonl",
        "retry_count": 0,
        "resume_batch_idx": 0,
        "failed": [],
        # Pre-populate batches so extract is bypassed at the graph level.
        # The extract node will still run but its output is ignored because
        # we override batches + current_batch_idx in the initial state.
        "batches": [_SYNTHETIC_QUESTIONS],
        "current_batch_idx": 0,
        "is_batch_valid": False,
        "max_batches": 1,
    }

    config = {
        "configurable": {"thread_id": str(uuid.uuid4())},
        "recursion_limit": 50,
    }

    log.info("=" * 70)
    log.info("PIPELINE TEST — %d synthetic questions, 1 batch", len(_SYNTHETIC_QUESTIONS))
    log.info("Flow: enrich → validate → write_db → qdrant_embed → qdrant_upsert")
    log.info("=" * 70)

    for event in app.stream(initial_state, config):
        for node_name, update in event.items():
            if update is None:
                continue
            log.info("─── NODE DONE: %s ───", node_name)

            if node_name == "enrich":
                batches = update.get("batches", [])
                enriched_batch = batches[0] if batches else []
                failed = update.get("failed", [])
                log.info(
                    "[enrich] enriched=%d  failed=%d  is_valid=%s",
                    len(enriched_batch), len(failed), update.get("is_batch_valid"),
                )
                for q in enriched_batch[:2]:
                    log.debug(
                        "[enrich]  id=%s  difficulty=%s  expl=%.100s",
                        q.get("id"), q.get("difficulty"), q.get("explanation", ""),
                    )

            elif node_name == "validate":
                batches = update.get("batches") or []
                remaining = batches[0] if batches else []
                failed = update.get("failed", [])
                log.info(
                    "[validate] kept=%d  rejected=%d  is_valid=%s",
                    len(remaining), len(failed), update.get("is_batch_valid"),
                )
                for q in failed:
                    log.warning("[validate] rejected id=%s — %s", q.get("id"), q.get("_validation_error"))

            elif node_name == "write_db":
                log.info("[write_db] DB write completed — check row count in log above")

            elif node_name == "qdrant_embed":
                dim = update.get("dim")
                vecs = update.get("vectors") or []
                log.info("[qdrant_embed] generated %d vectors, dim=%s", len(vecs), dim)

            elif node_name == "qdrant_upsert":
                log.info("[qdrant_upsert] upsert complete")

            elif node_name == "write_dlq":
                log.warning("[write_dlq] batch sent to DLQ — check enrich/validate logs above")

    log.info("=" * 70)
    log.info("TEST COMPLETE")
    log.info("=" * 70)


if __name__ == "__main__":
    run_test()
