import psycopg2
from psycopg2.extras import execute_values
import logging
from ingestion.models import PipelineState
from enrichment import config
from .checkpoint import save_checkpoint


log = logging.getLogger(__name__)

# Connection details for your Supabase Instance
DB_CONFIG = {
    "dbname": config.DB_NAME,
    "user": config.SUPABASE_USER,
    "password": config.SUPABASE_PASSWORD,
    "host": config.SUPABASE_HOST,
    "port": config.SUPABASE_PORT
}

def write_to_db(state: PipelineState):
    """
    Takes the current batch from state and performs a bulk insert into Supabase.
    """
    idx = state["current_batch_idx"]
    batches = state.get("batches", [])
    if idx >= len(batches):
        log.warning(
            "No batch available at index %s (total batches: %s). Skipping DB write.",
            idx,
            len(batches),
        )
        return {}

    current_items = batches[idx]

    db_params = {
        "dbname": "postgres",
        "user": "postgres.qbenybbdxqeaciygkxup",
        "password": "Catalyst@1234#", 
        "host": "aws-1-us-east-1.pooler.supabase.com",
        "port": "6543",
        "sslmode": "require"
    }

    try:
        conn = psycopg2.connect(**db_params)
        cur = conn.cursor()
        
        query = """
            INSERT INTO questions (
                id, text, options, correct_index, topic, subject, source, difficulty,
                enrichment_attempts, enrichment_status,
                code_snippet, snippet_language, answer_verified,
                source_explanation, explanation_output
            )
            VALUES %s
            ON CONFLICT (id) DO NOTHING
        """

        # Prepare data tuples
        data = [(
            q['id'],
            q['text'],
            q['options'],
            q['correct_index'],
            q['topic'],
            q['subject'],
            q['source'],
            q.get('difficulty'),
            0,
            q.get('enrichment_status', 'raw'),
            q.get('code_snippet'),
            q.get('snippet_language'),
            q.get('answer_verified'),
            q.get('source_explanation'),
            q.get('explanation_output'),
        ) for q in current_items]
        
        execute_values(cur, query, data)
        conn.commit()
        save_checkpoint(
            source=state["source"],
            next_batch_idx=idx + 1,
            total_batches=len(batches),
        )
        
        log.info(f"✅ Successfully wrote {len(data)} questions from batch {idx} to Supabase.")
        cur.close()
        
    except Exception as e:
        log.error(f"❌ Database error on batch {idx}: {e}")
        # In a real scenario, you might want to set is_batch_valid to False here
        raise e 
    finally:
        if conn:
            conn.close()

    return {}

def write_to_dlq(state: PipelineState):
    """Placeholder for failed batches."""
    idx = state.get("current_batch_idx", 0)
    if idx >= len(state.get("batches", [])):
        log.warning("No batch available at index %s to send to DLQ.", idx)
        return {}

    save_checkpoint(
        source=state["source"],
        next_batch_idx=idx + 1,
        total_batches=len(state.get("batches", [])),
    )
    log.error(f"Batch {idx} sent to DLQ.")
    return {}

