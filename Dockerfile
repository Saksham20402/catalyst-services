FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Prefer slim embed deps; fall back path documented in MIGRATION notes.
COPY requirements.embed.txt .
RUN pip install --no-cache-dir -r requirements.embed.txt

ENV HF_HOME=/app/.cache
ENV TRANSFORMERS_CACHE=/app/.cache
ENV SENTENCE_TRANSFORMERS_HOME=/app/.cache
ENV EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
ENV UVICORN_WORKERS=1
ENV TORCH_NUM_THREADS=4

COPY main.py .

# Download the model during build so the instance does not cold-start on first request.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers $UVICORN_WORKERS"]
