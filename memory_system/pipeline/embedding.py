"""
Embedding 生成 — 支持本地模型或HTTP服务
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

_model = None
_LOCK = None
_http_client = None


def _get_lock():
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


async def _get_model():
    global _model
    if _model is not None:
        return _model

    lock = _get_lock()
    async with lock:
        if _model is not None:
            return _model

        model_path = settings.embedding_model_path
        if not Path(model_path).exists():
            logger.error("embedding model not found: %s", model_path)
            return None

        logger.info("loading BGE-M3 from %s ...", model_path)

        loop = asyncio.get_event_loop()
        def _load():
            from sentence_transformers import SentenceTransformer
            return SentenceTransformer(model_path, device=settings.embedding_device)

        _model = await loop.run_in_executor(None, _load)
        logger.info("BGE-M3 loaded")
        return _model


_cache: dict[str, list[float]] = {}
_MAX_CACHE = 10_000


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _cache_put(key: str, vec: list[float]):
    if len(_cache) >= _MAX_CACHE:
        keys = list(_cache.keys())
        for k in keys[:_MAX_CACHE // 2]:
            del _cache[k]
    _cache[key] = vec


async def embed_text(text: str) -> list[float] | None:
    # 统一走缓存，不管是 HTTP 还是本地模式
    key = _cache_key(text)
    if key in _cache:
        return _cache[key]

    vec = None
    if settings.embedding_service_url:
        vec = await _embed_text_http(text)
    else:
        model = await _get_model()
        if model is None:
            return None
        try:
            loop = asyncio.get_event_loop()
            vec = await loop.run_in_executor(
                None,
                lambda: model.encode(text, normalize_embeddings=True).tolist(),
            )
        except Exception as e:
            logger.warning("embedding error: %s", e)
            return None

    if vec is not None:
        _cache_put(key, vec)
    return vec


async def _embed_text_http(text: str) -> list[float] | None:
    """通过HTTP服务生成embedding"""
    global _http_client
    if _http_client is None:
        import httpx
        _http_client = httpx.AsyncClient(timeout=30.0)

    try:
        resp = await _http_client.post(
            f"{settings.embedding_service_url}/v1/embeddings",
            json={"input": [text], "model": settings.embedding_model}
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception as e:
        logger.warning("embedding error: %s", e)
        return None


async def embed_batch(texts: list[str]) -> list[list[float] | None]:
    if not texts:
        return []

    if settings.embedding_service_url:
        return await _embed_batch_http(texts)

    model = await _get_model()
    if model is None:
        return [None] * len(texts)

    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []

    for i, text in enumerate(texts):
        key = _cache_key(text)
        if key in _cache:
            results[i] = _cache[key]
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)

    if not uncached_texts:
        return results

    try:
        loop = asyncio.get_event_loop()
        vecs = await loop.run_in_executor(
            None,
            lambda: model.encode(
                uncached_texts,
                batch_size=256,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist(),
        )
        for idx, vec in zip(uncached_indices, vecs):
            results[idx] = vec
            _cache_put(_cache_key(texts[idx]), vec)
    except Exception as e:
        logger.warning("batch embedding error: %s", e)

    return results


async def _embed_batch_http(texts: list[str]) -> list[list[float] | None]:
    """通过HTTP服务批量生成embedding"""
    global _http_client
    if _http_client is None:
        import httpx
        _http_client = httpx.AsyncClient(timeout=30.0)

    try:
        resp = await _http_client.post(
            f"{settings.embedding_service_url}/v1/embeddings",
            json={"input": texts, "model": settings.embedding_model}
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]
    except Exception as e:
        logger.warning("batch embedding error: %s", e)
        return [None] * len(texts)
