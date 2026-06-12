FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    FlagEmbedding \
    torch --index-url https://download.pytorch.org/whl/cu121

COPY embed.py /app/embed.py

ENV EMBEDDING_MODEL_ID=BAAI/bge-m3
ENV EMBEDDING_MODEL_LOAD_PATH=/app/models/BAAI/bge-m3
ENV HOST=0.0.0.0
ENV PORT=8002

EXPOSE 8002

CMD ["uvicorn", "embed:app", "--host", "0.0.0.0", "--port", "8002"]
