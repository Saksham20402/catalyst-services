import logging
import uuid
from ingestion.models import PipelineState
from .main_graph import build_graph
import psycopg2
from .checkpoint import load_checkpoint, clear_checkpoint

# Setup logging to see the progress in the console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)



def run_ingestion_pipeline(source_path: str, is_web: bool = False):
    """
    Initializes and executes the LangGraph MCQ pipeline.
    """
    # 1. Compile the Graph
    app = build_graph()
    
    checkpoint = load_checkpoint(source_path) or {}
    resume_batch_idx = int(checkpoint.get("next_batch_idx", 0))

    # 2. Define the Initial State
    # Note: We only need to provide the 'inputs'. 
    # The 'extract' node will populate 'batches' and 'current_batch_idx'.
    initial_state = {
        "source": source_path,
        "batch_size": 20,
        "dlq_path": "data/failed_batches.jsonl",
        "retry_count": 0,
        "enriched": [],
        "failed": [],
        "resume_batch_idx": resume_batch_idx,
    }
    
    # 3. Configure Threading (Optional, useful for checkpoints)
    config = {
        "configurable": {"thread_id": str(uuid.uuid4())},
        "recursion_limit": 200
    }
    
    log.info(f"Starting pipeline for source: {source_path}")
    if resume_batch_idx > 0:
        log.info(f"Resuming from checkpoint at batch index {resume_batch_idx}.")
    
    try:
        # We use .stream() to see updates as each batch completes
        for event in app.stream(initial_state, config):
            for node_name, state_update in event.items():
                log.info(f"--- Finished Node: {node_name} ---")

                if state_update is None:
                    continue

                # Optional: print progress if the update contains batches
                if "current_batch_idx" in state_update:
                    idx = state_update["current_batch_idx"]
                    log.info(f"Transitioning to Batch Index: {idx}")

        log.info("Pipeline Execution Complete.")
        clear_checkpoint(source_path)
        
    except Exception as e:
        log.error(f"Pipeline failed unexpectedly: {e}", exc_info=True)

if __name__ == "__main__":
    # Example 1: Sanfoundry Web Scrape
    url = "https://www.sanfoundry.com/operating-system-questions-answers-basics/"
    run_ingestion_pipeline(url, is_web=True)

    # try:
    #     conn = psycopg2.connect("postgresql://postgres:Catalyst@1234#@db.qbenybbdxqeaciygkxup.supabase.co:5432/postgres")
    #     print("Connection Successful!")
    # except Exception as e:
    #     print(f"Still failing: {e}")
    
    # Example 2: Local PDF (Uncomment to use)
    # pdf = "path/to/operating_systems_textbook.pdf"
    # run_ingestion_pipeline(pdf, is_web=False)