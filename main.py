from fastapi import FastAPI, HTTPException
from sentence_transformers import SentenceTransformer
from pydantic import BaseModel, Field
from typing import List, Optional
import torch
import os
from contextlib import asynccontextmanager



if torch.backends.mps.is_available():
    DEVICE = "mps"
elif torch.cuda.is_available():
    DEVICE = "cuda"
else:
    DEVICE = "cpu"

torch.set_num_threads(max(1, int(os.getenv("TORCH_NUM_THREADS", "4"))))

_model: SentenceTransformer | None = None
MODEL_NAME = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    _model = SentenceTransformer(MODEL_NAME, device=DEVICE)

    _ = _model.encode(["warmup"], convert_to_numpy=True, normalize_embeddings=True)

    yield

    _model = None

app = FastAPI(
    title="Local Embedding API",
    version="1.0.0",
    lifespan=lifespan  # <-- Add this parameter
)


class EmbedRequest(BaseModel):
    text: Optional[str] = Field(None, description="Single text to embed")
    texts: Optional[List[str]] = Field(None, description="List of texts to embed")

class EmbedResponse(BaseModel):
    embeddings: List[List[float]]
    model: str
    dim: int





@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not initialized yet.")
    if not req.text and not req.texts:
        raise HTTPException(status_code=400, detail="Provide either 'text' or 'texts'.")
    if req.text and req.texts:
        raise HTTPException(status_code=400, detail="Provide only one of 'text' or 'texts'.")

    inputs = [req.text] if req.text else req.texts or []
    if not inputs:
        raise HTTPException(status_code=400, detail="'texts' must be a non-empty list.")

    try:
        embs = _model.encode(
            inputs,
            batch_size=min(32, len(inputs)),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        embeddings = [e.tolist() for e in embs]
        dim = len(embeddings[0]) if embeddings else 0
        return EmbedResponse(embeddings=embeddings, model=MODEL_NAME, dim=dim)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute embeddings: {e}")


