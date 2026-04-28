
from langgraph.graph import StateGraph, START, END
from .extarct import extract_and_batch
from .enrich import enrich_batch
from .validate import validate_batch
from .write import write_to_db, write_to_dlq
from .qdrant_upsert import generate_and_validate_vectors, upsert_vectors_to_qdrant
from ingestion.models import PipelineState


def route_validation(state: PipelineState):
    if state["is_batch_valid"]:
        return "valid"

    # Optional: Add retry logic before going to DLQ
    if state["retry_count"] < 3:
        return "retry"

    return "invalid"
    
def manage_loop(state: PipelineState):
# Calculate the next index
    new_idx = state["current_batch_idx"] + 1
    
    # Return the update
    return {"current_batch_idx": new_idx}

def should_continue(state: PipelineState):
    # Double check if we still have batches left
    if state["current_batch_idx"] < len(state["batches"]):
        return "continue"
    return "end"
        
def build_graph():
    workflow = StateGraph(PipelineState)

    workflow.add_node("extract", extract_and_batch)
    workflow.add_node("enrich", enrich_batch)
    workflow.add_node("validate", validate_batch)
    workflow.add_node("write_db", write_to_db)
    workflow.add_node("qdrant_embed", generate_and_validate_vectors)
    workflow.add_node("qdrant_upsert", upsert_vectors_to_qdrant)
    workflow.add_node("write_dlq", write_to_dlq)
    workflow.add_node("manage_loop", manage_loop)

    workflow.add_edge(START, "extract")
    workflow.add_edge("extract", "enrich")
    workflow.add_edge("enrich", "validate")

    # --- THE ROUTING LOGIC --

# In your build_graph function:
    workflow.add_conditional_edges(
        "validate",
        route_validation,
        {
            "valid": "write_db",
            "retry": "enrich",
            "invalid": "write_dlq"
        }
    )

    workflow.add_edge("write_db", "qdrant_embed")
    workflow.add_edge("qdrant_embed", "qdrant_upsert")
    workflow.add_edge("qdrant_upsert", "manage_loop")
    workflow.add_edge("write_dlq", "manage_loop")
    workflow.add_conditional_edges(
        "manage_loop",
        should_continue,
        {
            "continue": "enrich",
            "end": END
        }
    )
    return workflow.compile()