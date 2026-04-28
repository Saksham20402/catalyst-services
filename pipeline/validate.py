from ingestion.models import PipelineState

# def validate_batch(state: PipelineState):
#     idx = state["current_batch_idx"]
#     current_items = state["batches"][idx]
    
#     # Perform your checks (e.g., schema validation, PII check, etc.)
#     # If successful:
#     return {"is_batch_valid": True}
#     # If failed: return {"is_batch_valid": False}

def validate_batch(state: PipelineState):
    # Automatically approve every batch so it hits the DB node
    return {"is_batch_valid": True}