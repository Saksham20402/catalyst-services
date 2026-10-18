"""Embedding HTTP API — production service for Catalyst (Cloud Run / AWS EC2).

Auth:
  - Cloud Run: IAM (--no-allow-unauthenticated); app-level secret optional.
  - AWS / Docker Compose: set EMBED_SERVICE_SECRET and call with X-Embed-Secret
    (or Authorization: Bearer <secret>). If unset, no app-level auth (rely on
    private network / IAM).
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

import torch
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

log = logging.getLogger(__name__)

log.info(">>> Starting container...")

if torch.backends.mps.is_available():
    DEVICE = "mps"
elif torch.cuda.is_available():
    DEVICE = "cuda"
else:
    DEVICE = "cpu"

log.info(">>> DEVICE selected: %s", DEVICE)

torch.set_num_threads(max(1, int(os.getenv("TORCH_NUM_THREADS", "4"))))
log.info(">>> Torch threads configured")

_model: SentenceTransformer | None = None
MODEL_NAME = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBED_SERVICE_SECRET = os.getenv("EMBED_SERVICE_SECRET", "").strip()
log.info(">>> Model Name env = %s", MODEL_NAME)
log.info(
    ">>> App-level auth = %s",
    "shared_secret" if EMBED_SERVICE_SECRET else "disabled (IAM/network only)",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model

    log.info(">>> lifespan startup triggered")

    try:
        log.info(">>> Loading SentenceTransformer...")
        _model = SentenceTransformer(
            MODEL_NAME, device=DEVICE, token=os.getenv("HF_TOKEN")
        )
        log.info(">>> Model loaded successfully")

        log.info(">>> Warmup: encoding dummy text")
        _ = _model.encode(
            ["warmup"], convert_to_numpy=True, normalize_embeddings=True
        )
        log.info(">>> Warmup complete")

    except Exception as e:
        log.error(">>> ERROR loading model: %s", e)
        raise

    yield

    log.info(">>> Shutting down — clearing model")
    _model = None


app = FastAPI(
    title="Catalyst Embedding API",
    version="1.0.0",
    lifespan=lifespan,
)


class EmbedRequest(BaseModel):
    text: Optional[str] = Field(None)
    texts: Optional[List[str]] = Field(None)


class EmbedResponse(BaseModel):
    embeddings: List[List[float]]
    model: str
    dim: int


def _authorize(
    authorization: str | None,
    x_embed_secret: str | None,
) -> None:
    if not EMBED_SERVICE_SECRET:
        return

    if x_embed_secret and x_embed_secret == EMBED_SERVICE_SECRET:
        return

    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer ") :].strip()
        if token == EMBED_SERVICE_SECRET:
            return
        # On Cloud Run, Google ID tokens also arrive as Bearer — accept them
        # only when shared-secret mode is off. Here secret is set, so reject
        # non-matching tokens (callers on AWS must use the shared secret).
        raise HTTPException(status_code=401, detail="Unauthorized")

    raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    log.info(">>> /health called")
    return {"status": "ok"}


@app.get("/ready")
def ready():
    log.info(">>> /ready called")
    return {"ready": _model is not None}


@app.post("/embed", response_model=EmbedResponse)
def embed(
    req: EmbedRequest,
    authorization: str | None = Header(default=None),
    x_embed_secret: str | None = Header(default=None, alias="X-Embed-Secret"),
):
    log.info(">>> /embed called")
    _authorize(authorization, x_embed_secret)

    if _model is None:
        log.error(">>> ERROR: model not initialized")
        raise HTTPException(status_code=503, detail="Model not initialized yet")

    inputs = [req.text] if req.text else req.texts or []
    if not inputs:
        raise HTTPException(status_code=400, detail="Provide text or texts")

    log.info(">>> encoding %s input(s)", len(inputs))

    try:
        embs = _model.encode(
            inputs,
            batch_size=min(32, len(inputs)),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        log.info(">>> encoding finished")
    except Exception as e:
        log.error(">>> embed ERROR: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    embeddings = [e.tolist() for e in embs]
    dim = len(embeddings[0]) if embeddings else 0

    return EmbedResponse(embeddings=embeddings, model=MODEL_NAME, dim=dim)
