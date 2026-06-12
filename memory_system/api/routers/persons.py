"""
人物画像路由：create/get person + list memories
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_pg, get_redis
from models import PersonProfile
from storage.pg_store import PgStore
from storage.redis_store import RedisStore

router = APIRouter(prefix="/persons", tags=["persons"])


class CreatePersonRequest(BaseModel):
    external_id: str
    display_name: str | None = None


@router.post("", response_model=PersonProfile)
async def create_or_get_person(
    req: CreatePersonRequest,
    pg: Annotated[PgStore, Depends(get_pg)],
    redis: Annotated[RedisStore, Depends(get_redis)],
) -> PersonProfile:
    person = PersonProfile(
        external_id=req.external_id,
        display_name=req.display_name,
    )
    result = await pg.upsert_person(person)
    await redis.set_profile(result)
    return result


@router.get("/{person_id}/profile")
async def get_person_profile(
    person_id: str,
    pg: Annotated[PgStore, Depends(get_pg)],
    redis: Annotated[RedisStore, Depends(get_redis)],
) -> dict:
    # 先查 Redis 缓存
    cached = await redis.get_profile(person_id)

    # 获取各类型 Top-5 记忆
    from models import MemoryType
    memories_by_type: dict[str, list] = {}
    for mt in MemoryType:
        items = await redis.get_top_memories_by_type(person_id, mt, limit=5)
        if not items:
            # Redis miss，从 PG 查
            pg_items, _ = await pg.list_memories_by_person(
                person_id, memory_type=mt.value, limit=5
            )
            items = pg_items
        memories_by_type[mt.value] = items

    profile_data = cached or {}
    from retrieval.summary import build_summary
    all_memories = [m for items in memories_by_type.values() for m in items]
    summary = build_summary(profile_data.get("display_name"), all_memories)

    return {
        "profile": profile_data,
        "memories_by_type": memories_by_type,
        "summary": summary,
    }


@router.get("/{person_id}/memories")
async def list_person_memories(
    person_id: str,
    memory_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
    pg: Annotated[PgStore, Depends(get_pg)] = None,  # type: ignore
) -> dict:
    items, total = await pg.list_memories_by_person(
        person_id, memory_type=memory_type, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "has_more": offset + len(items) < total}
