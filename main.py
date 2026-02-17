from fastapi import FastAPI, HTTPException
from sentence_transformers import SentenceTransformer
from pydantic import BaseModel, Field
from typing import List, Optional
import torch
import os
import logging
import sys
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

log = logging.getLogger(__name__)

log.info(">>> Starting container...")

if torch.backends.mps.is_available():
    DEVICE = "mps"
elif torch.cuda.is_available():
    DEVICE = "cuda"
else:
    DEVICE = "cpu"

log.info(f">>> DEVICE selected: {DEVICE}")

torch.set_num_threads(max(1, int(os.getenv("TORCH_NUM_THREADS", "4"))))
log.info(">>> Torch threads configured")

_model: SentenceTransformer | None = None
MODEL_NAME = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
log.info(f">>> Model Name env = {MODEL_NAME}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model

    log.info(">>> lifespan startup triggered")

    try:
        log.info(">>> Loading SentenceTransformer...")
        _model = SentenceTransformer(MODEL_NAME, device=DEVICE, token=os.getenv("HF_TOKEN"))
        log.info(">>> Model loaded successfully")

        log.info(">>> Warmup: encoding dummy text")
        _ = _model.encode(["warmup"], convert_to_numpy=True, normalize_embeddings=True)
        log.info(">>> Warmup complete")

    except Exception as e:
        log.error(f">>> ERROR loading model: {e}")
        raise e

    yield

    log.info(">>> Shutting down — clearing model")
    _model = None


app = FastAPI(
    title="Local Embedding API",
    version="1.0.0",
    lifespan=lifespan
)


class EmbedRequest(BaseModel):
    text: Optional[str] = Field(None)
    texts: Optional[List[str]] = Field(None)

class EmbedResponse(BaseModel):
    embeddings: List[List[float]]
    model: str
    dim: int


@app.get("/health")
def health():
    log.info(">>> /health called")
    return {"status": "ok"}


@app.get("/ready")
def ready():
    log.info(">>> /ready called")
    return {"ready": _model is not None}


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    log.info(">>> /embed called")

    if _model is None:
        log.error(">>> ERROR: model not initialized")
        raise HTTPException(status_code=503, detail="Model not initialized yet")

    inputs = [req.text] if req.text else req.texts or []
    log.info(f">>> encoding {len(inputs)} input(s)")

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
        log.error(f">>> embed ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    embeddings = [e.tolist() for e in embs]
    dim = len(embeddings[0]) if embeddings else 0

    return EmbedResponse(embeddings=embeddings, model=MODEL_NAME, dim=dim)

