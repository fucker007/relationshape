"""
记忆核心路由：ingest / extract-and-ingest / recall
"""
from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_pg, get_redis
from models import (
    ExtractAndIngestRequest,
    ExtractAndIngestResponse,
    IngestRequest,
    IngestResponse,
    MemoryEntry,
    MemorySearchResult,
    RecallRequest,
    RecallResponse,
)
from pipeline.embedding import embed_batch
from retrieval.recall import recall
from retrieval.summary import build_summary
from storage.pg_store import PgStore
from storage.redis_store import RedisStore

router = APIRouter(prefix="/memories", tags=["memories"])


# ---------------------------------------------------------------------------
# POST /memories/ingest  —  直接写入已结构化记忆（P99 < 20ms）
# ---------------------------------------------------------------------------

@router.post("/ingest", response_model=IngestResponse)
async def ingest_memories(
    req: IngestRequest,
    redis: Annotated[RedisStore, Depends(get_redis)],
    pg: Annotated[PgStore, Depends(get_pg)],
) -> IngestResponse:
    accepted = 0
    deduplicated = 0
    memory_ids: list[str] = []

    # 批量生成 embedding
    texts = [m.content for m in req.memories]
    embeddings = await embed_batch(texts)

    for create_obj, embedding in zip(req.memories, embeddings):
        entry = MemoryEntry(
            person_id=create_obj.person_id,
            session_id=create_obj.session_id,
            memory_type=create_obj.memory_type,
            content=create_obj.content,
            structured_data=create_obj.structured_data,
            importance_score=create_obj.importance_score,
            confidence_score=create_obj.confidence_score,
            emotional_valence=create_obj.emotional_valence,
            emotional_intensity=create_obj.emotional_intensity,
            embedding=embedding,
            source_message=create_obj.source_message,
            source_turn_index=create_obj.source_turn_index,
        )
        is_new = await redis.write_memory(entry)
        if is_new:
            await pg.insert_memory(entry)
            await redis.invalidate_recall_cache(str(req.person_id))
            memory_ids.append(str(entry.memory_id))
            accepted += 1
        else:
            deduplicated += 1

    return IngestResponse(
        accepted=accepted,
        deduplicated=deduplicated,
        memory_ids=memory_ids,
    )


# ---------------------------------------------------------------------------
# POST /memories/extract-and-ingest  —  已永久弃用（410 Gone）
# ---------------------------------------------------------------------------
# 历史路径：消息 → Kafka conversation.messages → consumer → memory_entries 表（vector）
# 现路径：  消息 → POST /memory/chat → Outbox(extraction_tasks) → graph 表（person/event/relationship）
# memory_entries 表已退出生产，本端点保留路由仅返回 410 让旧调用方明确报错。

@router.post("/extract-and-ingest", response_model=ExtractAndIngestResponse)
async def extract_and_ingest(req: ExtractAndIngestRequest) -> ExtractAndIngestResponse:
    raise HTTPException(
        status_code=410,
        detail=(
            "/memories/extract-and-ingest is permanently removed. "
            "Use POST /api/v1/memory/chat which writes to the graph memory store "
            "(person / event / relationship) via Outbox + GraphTaskPoller."
        ),
    )


# ---------------------------------------------------------------------------
# GET /memories/recall  —  召回最相关记忆（P99 < 100ms）
# ---------------------------------------------------------------------------

@router.post("/recall", response_model=RecallResponse)
async def recall_memories(
    req: RecallRequest,
    redis: Annotated[RedisStore, Depends(get_redis)],
    pg: Annotated[PgStore, Depends(get_pg)],
) -> RecallResponse:
    t0 = time.monotonic()
    person_id = str(req.person_id)

    # 检查 summary 缓存
    if req.format == "summary":
        import hashlib
        ctx_hash = hashlib.sha256(
            (req.context + str(req.types or "") + str(req.limit)).encode()
        ).hexdigest()[:16]
        cached = await redis.get_recall_cache(person_id, ctx_hash)
        if cached:
            latency = int((time.monotonic() - t0) * 1000)
            return RecallResponse(summary=cached, search_latency_ms=latency)

    types = [t.value for t in req.types] if req.types else None
    records, sources, confidence = await recall(
        person_id=person_id,
        context=req.context,
        redis=redis,
        pg=pg,
        limit=req.limit,
        types=types,
        min_importance=req.min_importance,
    )

    latency = int((time.monotonic() - t0) * 1000)

    if req.format == "summary":
        # 获取人物名字
        profile = await redis.get_profile(person_id)
        display_name = profile.get("display_name") if profile else None

        summary_text = build_summary(display_name, records)

        # 缓存 summary
        if summary_text:
            await redis.set_recall_cache(person_id, ctx_hash, summary_text)  # type: ignore

        return RecallResponse(
            summary=summary_text,
            search_latency_ms=latency,
            sources=sources,
            confidence=confidence,
        )
    else:
        # detailed format：返回记忆列表（memory_entries 表结构）
        from datetime import datetime
        results = [
            MemorySearchResult(
                memory_id=r.get("memory_id", ""),
                person_id=person_id,
                memory_type=r.get("memory_type", "IDENTITY"),
                content=r.get("content", ""),
                importance_score=float(r.get("importance_score", 0.5)),
                emotional_valence=float(r.get("emotional_valence", 0.0)),
                created_at=r.get("created_at") or datetime.utcnow(),
                final_score=float(r.get("final_score", 0.0)),
            )
            for r in records
        ]
        return RecallResponse(
            memories=results,
            search_latency_ms=latency,
            sources=sources,
            confidence=confidence,
        )
