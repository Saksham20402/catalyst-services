import logging
import re
from typing import Any

from ingestion.models import PipelineState

log = logging.getLogger(__name__)

_VALID_DIFFICULTIES = {"easy", "medium", "hard"}
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_MIN_OPTIONS = 2
_MAX_OPTIONS = 6
_MIN_TEXT_LEN = 10

_REQUIRED_FIELDS = ("id", "text", "options", "correct_index", "subject", "source")


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _validate_question(q: Any) -> tuple[bool, str]:
    """Run every check against one question dict. Returns (is_valid, reason)."""
    if not isinstance(q, dict):
        return False, "not a dict"

    missing = [f for f in _REQUIRED_FIELDS if f not in q]
    if missing:
        return False, f"missing required fields: {missing}"

    qid = q.get("id")
    if not _is_nonempty_str(qid):
        return False, "id missing or not a string"
    if not _UUID_RE.match(qid.strip()):
        return False, f"id '{qid}' is not a valid UUID"

    text = q.get("text")
    if not _is_nonempty_str(text):
        return False, "text missing or empty"
    text_len = len(text.strip())
    if text_len < _MIN_TEXT_LEN:
        return False, f"text too short ({text_len} chars, min {_MIN_TEXT_LEN})"

    options = q.get("options")
    if not isinstance(options, list):
        return False, "options is not a list"
    if len(options) < _MIN_OPTIONS:
        return False, f"too few options ({len(options)}, min {_MIN_OPTIONS})"
    if len(options) > _MAX_OPTIONS:
        return False, f"too many options ({len(options)}, max {_MAX_OPTIONS})"
    for i, opt in enumerate(options):
        if not _is_nonempty_str(opt):
            return False, f"option[{i}] is empty or not a string"
    normalized = [o.strip() for o in options]
    if len(set(normalized)) != len(normalized):
        return False, "duplicate options"

    correct_index = q.get("correct_index")
    if isinstance(correct_index, bool) or not isinstance(correct_index, int):
        return False, f"correct_index must be int, got {type(correct_index).__name__}"
    if correct_index < 0 or correct_index >= len(options):
        return False, f"correct_index {correct_index} out of range for {len(options)} options"

    subject = q.get("subject")
    if not _is_nonempty_str(subject):
        return False, "subject missing or empty"

    source = q.get("source")
    if not _is_nonempty_str(source):
        return False, "source missing or empty"

    topic = q.get("topic")
    if topic is not None and not _is_nonempty_str(topic):
        return False, "topic is present but empty/non-string"

    difficulty = q.get("difficulty")
    if difficulty is not None:
        if not isinstance(difficulty, str):
            return False, "difficulty must be a string when provided"
        if difficulty.strip().lower() not in _VALID_DIFFICULTIES:
            return False, f"difficulty '{difficulty}' not in {sorted(_VALID_DIFFICULTIES)}"

    return True, ""


def validate_batch(state: PipelineState) -> dict[str, Any]:
    """
    Validate every question in the current batch.

    Behaviour:
      * Each question is checked field-by-field by `_validate_question`.
      * Valid questions stay in the batch; invalid ones are pushed to `failed`
        with a `_validation_error` annotation.
      * `is_batch_valid` is True iff at least one question survived — a
        partially-bad batch still moves on to write_db. Only a fully-empty
        result trips the retry/DLQ branch in main_graph.
    """
    idx = state["current_batch_idx"]
    batches = state.get("batches", [])

    if idx >= len(batches):
        log.warning(f"validate: no batch at index {idx} (total={len(batches)})")
        return {"is_batch_valid": False}

    current = batches[idx]
    valid: list[dict] = []
    invalid: list[dict] = []

    for q in current:
        ok, reason = _validate_question(q)
        if ok:
            valid.append(q)
            continue

        qid = q.get("id", "<no-id>") if isinstance(q, dict) else "<not-a-dict>"
        log.warning(f"validate: batch={idx} id={qid} rejected — {reason}")

        if isinstance(q, dict):
            invalid.append({**q, "_validation_error": reason})
        else:
            invalid.append({"raw": q, "_validation_error": reason})

    new_batches = list(batches)
    new_batches[idx] = valid

    log.info(
        f"validate: batch {idx} → {len(valid)}/{len(current)} kept, "
        f"{len(invalid)} rejected"
    )

    return {
        "batches": new_batches,
        "is_batch_valid": len(valid) > 0,
        "failed": invalid,
    }
