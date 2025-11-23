FROM python:3.11-slim

# Install basic tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependencies and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Huggingface cache inside container, not in ephemeral root
ENV HF_HOME=/app/.cache
ENV TRANSFORMERS_CACHE=/app/.cache
ENV SENTENCE_TRANSFORMERS_HOME=/app/.cache

# Model config (NOT loaded at build)
ENV EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Copy application code
COPY . .

# Cloud Run controls the port via env
EXPOSE 8080

# Optional: one worker for model loading apps
ENV UVICORN_WORKERS=1

# Startup
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers $UVICORN_WORKERS"]

