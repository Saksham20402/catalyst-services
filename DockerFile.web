FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
RUN python3 - <<EOF
from sentence_transformers import SentenceTransformer
print("Preloading model:", "${EMBED_MODEL}")
SentenceTransformer("${EMBED_MODEL}")
print("Model downloaded successfully.")
EOF
COPY . .
EXPOSE 8080
ENV UVICORN_WORKERS=1
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8080 --workers $UVICORN_WORKERS"]
