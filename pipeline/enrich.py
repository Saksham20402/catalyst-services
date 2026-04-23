from ingestion.models import PipelineState

# def enrich_batch(state: PipelineState):
#     idx = state["current_batch_idx"]
#     batch = state["batches"][idx]
    
#     # Logic for LLM enrichment goes here...
#     # We reset is_batch_valid and retry_count for each new batch attempt
#     return {
#         "enriched": [], # Add actual results here
#         "is_batch_valid": False, 
#         "retry_count": state.get("retry_count", 0) 
#     }

def enrich_batch(state: PipelineState):
    # We do nothing here for the test. 
    # Just ensure we don't accidentally wipe out the state.
    return {
        "retry_count": 0
    }