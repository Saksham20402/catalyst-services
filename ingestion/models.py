import operator
from dataclasses import dataclass, field
from typing import Optional, Any, List, Annotated  # Added List and Annotated
from typing_extensions import NotRequired, TypedDict

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
    has_code_snippet: bool = False
    code_snippet: Optional[str] = None
    snippet_language: Optional[str] = None
    answer_verified: Optional[bool] = None
    source_explanation: Optional[str] = None
    explanation_output: Optional[str] = None

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
    vectors: NotRequired[List[List[float]]]
    dim: NotRequired[int]
    max_batches: NotRequired[int]  # Test mode: stop after N batches (0 or absent = process all)
