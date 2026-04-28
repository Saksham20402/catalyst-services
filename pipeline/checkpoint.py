import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CHECKPOINT_DIR = Path("data/pipeline_checkpoints")


def _checkpoint_path(source: str) -> Path:
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return CHECKPOINT_DIR / f"{source_hash}.json"


def load_checkpoint(source: str) -> dict[str, Any] | None:
    path = _checkpoint_path(source)
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_checkpoint(source: str, next_batch_idx: int, total_batches: int) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(source)
    payload = {
        "source": source,
        "next_batch_idx": next_batch_idx,
        "total_batches": total_batches,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_checkpoint(source: str) -> None:
    path = _checkpoint_path(source)
    if path.exists():
        path.unlink()
