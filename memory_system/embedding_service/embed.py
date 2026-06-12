"""
embed_optimized.py — 优化版 embedding 服务

优化点：
1. encode 放入线程池，不阻塞事件循环
2. 请求合批（batching）：短时间内的多个请求合并为一次 GPU 推理
"""
import os
import asyncio
import time
from typing import List, Optional, Union, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from FlagEmbedding import FlagModel
import torch
from concurrent.futures import ThreadPoolExecutor

EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "BAAI/bge-m3")
EMBEDDING_MODEL_LOAD_PATH = os.getenv("EMBEDDING_MODEL_LOAD_PATH", "/app/models/BAAI/bge-m3")
PORT = int(os.getenv("PORT", 8002))
HOST = os.getenv("HOST", "0.0.0.0")

# 批处理参数
BATCH_MAX_SIZE = 32       # 最大批大小
BATCH_WAIT_MS = 5         # 最长等待时间(ms)


class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]
    model: str
    encoding_format: Optional[str] = "float"
    dimensions: Optional[int] = None
    user: Optional[str] = None


class EmbeddingDataItem(BaseModel):
    object: str = "embedding"
    embedding: List[float]
    index: int


class EmbeddingResponseUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: List[EmbeddingDataItem]
    model: str
    usage: EmbeddingResponseUsage


# ── 批处理引擎 ─────────────────────────────────────────────────────

class BatchEncoder:
    """将多个并发请求合并为一次 GPU 推理"""

    def __init__(self, model: FlagModel, max_batch: int = BATCH_MAX_SIZE, wait_ms: float = BATCH_WAIT_MS):
        self._model = model
        self._max_batch = max_batch
        self._wait_ms = wait_ms
        self._queue: list[tuple[str, asyncio.Future]] = []
        self._lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._processing = False

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """提交文本，等待批处理结果"""
        futures = []
        loop = asyncio.get_event_loop()

        async with self._lock:
            for text in texts:
                fut = loop.create_future()
                self._queue.append((text, fut))
                futures.append(fut)

            # 触发处理
            if not self._processing:
                self._processing = True
                asyncio.create_task(self._process_batch())

        return await asyncio.gather(*futures)

    async def _process_batch(self):
        """等待短暂时间收集请求，然后批量处理"""
        await asyncio.sleep(self._wait_ms / 1000)

        async with self._lock:
            batch = self._queue[:self._max_batch]
            self._queue = self._queue[self._max_batch:]
            self._processing = bool(self._queue)
            if self._queue:
                asyncio.create_task(self._process_batch())

        if not batch:
            return

        texts = [t for t, _ in batch]
        futures = [f for _, f in batch]

        try:
            loop = asyncio.get_event_loop()
            embeddings = await loop.run_in_executor(
                self._executor,
                lambda: self._model.encode(texts).tolist(),
            )
            for fut, emb in zip(futures, embeddings):
                if not fut.done():
                    fut.set_result(emb)
        except Exception as e:
            for fut in futures:
                if not fut.done():
                    fut.set_exception(e)


# ── App ────────────────────────────────────────────────────────────

embedding_model: Optional[FlagModel] = None
batch_encoder: Optional[BatchEncoder] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global embedding_model, batch_encoder
    print("INFO:   Starting application lifespan...")

    use_fp16 = torch.cuda.is_available()
    if use_fp16:
        print("INFO:   CUDA is available. Using FP16.")
    else:
        print("INFO:   CUDA not available. Using CPU.")

    try:
        print(f"INFO:   Loading embedding model: {EMBEDDING_MODEL_ID}...")
        embedding_model = FlagModel(
            EMBEDDING_MODEL_LOAD_PATH,
            query_instruction_for_retrieval=None,
            use_fp16=use_fp16,
            normalize_embeddings=True,
        )
        batch_encoder = BatchEncoder(embedding_model)
        print(f"INFO:   Model loaded. Batch encoding enabled (max={BATCH_MAX_SIZE}, wait={BATCH_WAIT_MS}ms)")
    except Exception as e:
        print(f"ERROR:  Failed to load model: {e}")
        embedding_model = None

    yield

    print("INFO:   Shutting down...")
    del embedding_model


app = FastAPI(title="BGE Embedding API (Optimized)", lifespan=lifespan)


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(request: EmbeddingRequest):
    if embedding_model is None or batch_encoder is None:
        raise HTTPException(status_code=503, detail="Model not available")

    if request.model != EMBEDDING_MODEL_ID:
        raise HTTPException(status_code=400, detail=f"Unsupported model: {request.model}")

    texts = [request.input] if isinstance(request.input, str) else request.input

    t0 = time.perf_counter()
    embeddings = await batch_encoder.encode(texts)
    ms = (time.perf_counter() - t0) * 1000
    print(f"INFO:   Encoded {len(texts)} texts in {ms:.1f}ms")

    tokenizer = getattr(embedding_model, "tokenizer", None)
    prompt_tokens = sum(
        len(tokenizer.encode(t, add_special_tokens=True)) if tokenizer else len(t.split())
        for t in texts
    )

    return EmbeddingResponse(
        data=[
            EmbeddingDataItem(embedding=emb, index=i)
            for i, emb in enumerate(embeddings)
        ],
        model=EMBEDDING_MODEL_ID,
        usage=EmbeddingResponseUsage(prompt_tokens=prompt_tokens, total_tokens=prompt_tokens),
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model_loaded": embedding_model is not None,
        "model_id": EMBEDDING_MODEL_ID,
        "batch_enabled": True,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
