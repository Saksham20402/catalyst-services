"""
Upsert batch question vectors into Qdrant: embed text + topic + subject (384-d cosine),
payload { question_id, difficulty }.

Embedding backends (first match wins):
  1. EMBED_API_URL — your own HTTP /embed (one round-trip per batch).
  2. EMBED_BACKEND=hf or USE_HF_INFERENCE=true — Hugging Face Inference API (one HTTP
     call per row; fine for sparse use, not ideal for large batches).
  3. Default — local SentenceTransformer: one model in memory, batched encode on
     GPU/MPS/CPU (best throughput for dense batches; no embedding network hops).
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from ingestion.models import PipelineState

log = logging.getLogger(__name__)

EXPECTED_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "questions1")
QDRANT_URL = os.getenv("VECTOR_DB_URL")
QDRANT_API_KEY = os.getenv("VECTOR_DB_KEY", "").strip() or None

EMBED_API_URL = os.getenv("EMBED_API_URL", "").strip().rstrip("/")
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
# Local ST: internal mini-batch size for model.encode (raise on GPU for throughput).
EMBED_LOCAL_BATCH_SIZE = max(1, int(os.getenv("EMBED_LOCAL_BATCH_SIZE", "64")))
# Dedicated Inference Endpoint URL (optional). If unset, uses HF Router + model id.
HF_INFERENCE_URL = os.getenv("HF_INFERENCE_URL", "").strip()
HF_INFERENCE_MODEL = os.getenv("HF_INFERENCE_MODEL", EMBED_MODEL).strip()
HF_INFERENCE_CONCURRENCY = max(1, int(os.getenv("HF_INFERENCE_CONCURRENCY", "4")))


def _use_hf_inference() -> bool:
    """HF inference is opt-in; default is local batched SentenceTransformer."""
    if EMBED_API_URL:
        return False
    backend = os.getenv("EMBED_BACKEND", "").strip().lower()
    if backend == "hf":
        return True
    if backend in ("local", "sentence_transformers", "st"):
        return False
    return os.getenv("USE_HF_INFERENCE", "").lower() in ("1", "true", "yes")


_embed_model = None
_hf_client = None
_embed_lock = threading.Lock()
_qdrant_client = None
_qdrant_lock = threading.Lock()


def _device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _get_sentence_transformer():
    global _embed_model
    with _embed_lock:
        if _embed_model is None:
            from sentence_transformers import SentenceTransformer

            log.info(
                "Loading local embedding model %r (batched encode; tune EMBED_LOCAL_BATCH_SIZE).",
                EMBED_MODEL,
            )
            _embed_model = SentenceTransformer(
                EMBED_MODEL,
                device=_device(),
                token=os.getenv("HF_TOKEN"),
            )
            if os.getenv("EMBED_LOCAL_WARMUP", "true").lower() in ("1", "true", "yes"):
                _embed_model.encode(
                    ["warmup"],
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                log.info("Local embedding model warmup encode done.")
        return _embed_model


def _to_sentence_vector(feat: np.ndarray) -> np.ndarray:
    """HF feature_extraction may return (dim,) or (seq, dim); pool to a single vector."""
    if feat.ndim == 1:
        return feat.astype(np.float32, copy=False)
    if feat.ndim == 2:
        return feat.mean(axis=0).astype(np.float32, copy=False)
    raise ValueError(f"Unexpected feature_extraction shape: {feat.shape}")


def _l2_normalize_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return (vectors / norms).astype(np.float32, copy=False)


def _get_hf_inference_client():
    """Single InferenceClient for the process (keeps HTTP session / routing warm)."""
    global _hf_client
    with _embed_lock:
        if _hf_client is None:
            from huggingface_hub import InferenceClient

            token = os.getenv("HF_TOKEN")
            model_arg = HF_INFERENCE_URL or HF_INFERENCE_MODEL
            _hf_client = InferenceClient(model=model_arg, token=token)
            if os.getenv("HF_INFERENCE_WARMUP", "true").lower() in ("1", "true", "yes"):
                try:
                    _hf_client.feature_extraction("warmup", normalize=True)
                    log.info("HF InferenceClient warmup completed.")
                except Exception as e:
                    log.warning("HF inference warmup failed (first real call may be slower): %s", e)
        return _hf_client


def _encode_one_hf(client, text: str) -> np.ndarray:
    """One text → L2-normalized sentence vector (cosine-ready)."""
    raw = client.feature_extraction(text, normalize=True)
    vec = _to_sentence_vector(np.asarray(raw, dtype=np.float32))
    return _l2_normalize_rows(vec.reshape(1, -1))[0]


def _encode_hf_batch(texts: list[str]) -> np.ndarray:
    client = _get_hf_inference_client()
    workers = min(HF_INFERENCE_CONCURRENCY, len(texts))
    if workers <= 1:
        rows = [_encode_one_hf(client, t) for t in texts]
    else:

        def encode(t: str) -> np.ndarray:
            return _encode_one_hf(client, t)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(encode, texts))
    return np.stack(rows, axis=0)


def _embedding_lines(questions: list[dict]) -> list[str]:
    """One line per question: question text + topic + subject (same order as batch)."""
    out: list[str] = []
    for q in questions:
        text = (q.get("text") or "").strip()
        topic = (q.get("topic") or "").strip()
        subject = (q.get("subject") or "").strip()
        line = " ".join([text, topic, subject]).strip()
        out.append(line if line else " ")
    return out


def _encode_texts(texts: list[str]) -> np.ndarray:
    if EMBED_API_URL:
        with httpx.Client(timeout=300.0) as client:
            r = client.post(f"{EMBED_API_URL}/embed", json={"texts": texts})
            r.raise_for_status()
            data = r.json()
            embs = data.get("embeddings") or []
            return np.asarray(embs, dtype=np.float32)
    if _use_hf_inference():
        return _encode_hf_batch(texts)
    model = _get_sentence_transformer()
    chunk = min(EMBED_LOCAL_BATCH_SIZE, max(1, len(texts)))
    return model.encode(
        texts,
        batch_size=chunk,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )


def _ensure_collection(client: QdrantClient, name: str, vector_size: int) -> None:
    names = {c.name for c in client.get_collections().collections}
    if name in names:
        return
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    log.info("Created Qdrant collection %r (%s-d cosine)", name, vector_size)


def _get_qdrant_client() -> QdrantClient:
    """Single Qdrant client reused across node invocations in this process."""
    global _qdrant_client
    with _qdrant_lock:
        if _qdrant_client is None:
            _qdrant_client = QdrantClient(
                url=QDRANT_URL,
                api_key=QDRANT_API_KEY,
                prefer_grpc=False,
            )
        return _qdrant_client


def generate_and_validate_vectors(state: PipelineState) -> dict[str, Any]:
    """
    Embed the current batch (question text + topic + subject) and upsert into Qdrant.
    Skipped when QDRANT_SKIP is truthy or batch index is out of range.
    """
    if os.getenv("QDRANT_SKIP", "").lower() in ("1", "true", "yes"):
        log.info("QDRANT_SKIP set — skipping vector upsert for this batch.")
        return {}

    idx = state["current_batch_idx"]
    batches = state.get("batches") or []
    if idx >= len(batches):
        log.warning("qdrant_upsert: no batch at index %s; skipping.", idx)
        return {}

    questions = batches[idx]
    if not questions:
        return {}

    lines = _embedding_lines(questions)
    if not any(lines):
        log.warning("qdrant_upsert: batch %s has no embeddable text; skipping.", idx)
        return {}

    try:
        vectors = _encode_texts(lines)
    except Exception as e:
        log.error("qdrant_upsert: embedding failed for batch %s: %s", idx, e)
        raise

    if vectors.ndim != 2 or vectors.shape[0] != len(questions):
        raise RuntimeError(
            f"Unexpected embedding shape {vectors.shape} for {len(questions)} questions"
        )

    dim = int(vectors.shape[1])
    if dim != EXPECTED_DIM:
        log.warning(
            "Embedding dimension is %s (expected %s from EMBEDDING_DIM). "
            "Update EMBEDDING_DIM / Qdrant collection or EMBED_MODEL.",
            dim,
            EXPECTED_DIM,
        )
    return {"vectors": vectors.tolist(), "dim": dim}


def upsert_vectors_to_qdrant(state: PipelineState) -> dict[str, Any]:
    idx = state["current_batch_idx"]
    batches = state.get("batches") or []
    if idx >= len(batches):
        log.warning("qdrant_upsert: no batch at index %s; skipping upsert.", idx)
        return {}

    questions = batches[idx]
    vectors = state.get("vectors")
    dim = state.get("dim")
    if not questions or not vectors or not dim:
        log.warning("qdrant_upsert: missing questions/vectors/dim for batch %s.", idx)
        return {}

    client = _get_qdrant_client()
    _ensure_collection(client, QDRANT_COLLECTION, int(dim))

    points: list[PointStruct] = []
    for q, row in zip(questions, vectors):
        qid = q.get("id")
        if not qid:
            log.warning("qdrant_upsert: skipping row without id")
            continue
        vec = row.tolist() if hasattr(row, "tolist") else list(row)
        payload = {
            "question_id": str(qid),
            "difficulty": q.get("difficulty"),
            "schema": {
                "id": qid,
                "text": q.get("text"),
                "options": q.get("options"),
                "correct_index": q.get("correct_index"),
                "topic": q.get("topic"),
                "subject": q.get("subject"),
                "source": q.get("source"),
                "difficulty": q.get("difficulty"),
            },
        }
        points.append(PointStruct(id=str(qid), vector=vec, payload=payload))

    if not points:
        return {}

    client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)
    log.info(
        "Upserted %s vectors into Qdrant collection %r (batch index %s).",
        len(points),
        QDRANT_COLLECTION,
        idx,
    )
    return {}