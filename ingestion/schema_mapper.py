import uuid
from .models import QuestionDTO
def to_schema_dict(q: QuestionDTO) -> dict:
    """QuestionDTO → plain dict matching your Postgres questions table."""
    return {
        "id":                  q.id or str(uuid.uuid4()),
        "text":                q.text,
        "options":             q.options,
        "correct_index":       q.correct_index,
        "topic":               q.topic,
        "subject":             q.subject,
        "source":              q.source,
        "difficulty":          q.difficulty,        # null — filled in enrich_batch
        "has_code_snippet":    q.has_code_snippet,
        "code_snippet":        q.code_snippet,
        "snippet_language":    q.snippet_language,
        "source_explanation":  q.source_explanation,
        "explanation_output":  q.explanation_output,
    }

def batch(items: list, size: int = 50) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]