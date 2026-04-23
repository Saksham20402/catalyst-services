import operator
from dataclasses import dataclass, field
from typing import Optional, Any, List, Annotated  # Added List and Annotated
from typing_extensions import TypedDict

@dataclass
class QuestionDTO:
    id: str
    text: str
    options: list[str]
    correct_index: int
    topic: Optional[str]
    subject: str
    source: str
    difficulty: Optional[str] = None

class PipelineState(TypedDict):
    # Inputs
    source: str
    batch_size: int
    dlq_path: str
    
    # State Control
    batches: List[List[dict]]
    current_batch_idx: int
    resume_batch_idx: int
    is_batch_valid: bool  # Moved into state
    retry_count: int      # To handle "Try again" logic
    
    # Accumulated results
    enriched: Annotated[List[dict], operator.add]
    failed: Annotated[List[dict], operator.add]
