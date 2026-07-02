"""
Schema Guard — 启动时校验 DB schema 和代码期望一致，缺字段立即 fail-fast。

背景（T3 阻塞修复）：
之前 extract 写入依赖 person_nodes.updated_at 等字段；当 DDL 漂移或人工 ALTER DROP
时，服务启动依然返 200，但 extract try/except 吞错误，API 对客户端无感，数据永久丢失。

解决：
- lifespan 在 migrate() 之后立刻调用 verify_schema()
- 逐表逐列查 information_schema，发现缺失 → raise SchemaMismatchError
- FastAPI 在 lifespan startup 阶段抛异常会导致进程非零退出 → supervisor / k8s 会重启
  并暴露故障，而不是静默降级

范围（P0 阻塞路径）：
- events: owner_id, session_id, event_time, event_type, summary, participant_ids,
  participant_names, importance, embedding, raw_turns, created_at, updated_at, is_deleted
- person_nodes: person_id, owner_id, name, role, identity, preferences, aversions,
  behaviors, total_events, last_active_at, created_at
- relationships: id, owner_id, from_person_id, to_person_id, relation_type,
  sentiment, intensity, last_event_id, updated_at
- extraction_tasks: 由 PgStore.migrate 建，P0-1 outbox 依赖
  （task_id, owner_id, session_id, payload, status, retry_count, created_at）

不覆盖：dashboard / memory_entries / daily_emotions 等非核心链路（放宽容忍）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Set

from asyncpg import Pool

logger = logging.getLogger(__name__)


class SchemaMismatchError(RuntimeError):
    """DB schema 与代码期望不一致，启动必须中止。"""


# 表 → 必需列集合（仅 P0 数据链路依赖的列）
REQUIRED_COLUMNS: Dict[str, Set[str]] = {
    "events": {
        "event_id", "owner_id", "session_id", "event_time", "event_type",
        "summary", "participant_ids", "participant_names", "importance",
        "embedding", "raw_turns", "created_at", "updated_at", "is_deleted",
    },
    "person_nodes": {
        "person_id", "owner_id", "name", "role", "identity", "preferences",
        "aversions", "behaviors", "total_events", "last_active_at", "created_at",
    },
    "relationships": {
        "id", "owner_id", "from_person_id", "to_person_id", "relation_type",
        "sentiment", "intensity", "last_event_id", "state", "updated_at",
    },
    "extraction_tasks": {
        "task_id", "owner_id", "session_id", "payload", "status",
        "retry_count", "scheduled_at", "task_type",
    },
}


async def _fetch_table_columns(pool: Pool, table: str) -> Set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = $1
            """,
            table,
        )
    return {r["column_name"] for r in rows}


async def verify_schema(pool: Pool) -> None:
    """校验所有关键表/列。缺失任何一项 → raise SchemaMismatchError。

    调用时机：lifespan startup，在 pg.migrate() 和 gs.migrate() 之后。
    """
    errors: List[str] = []

    for table, required in REQUIRED_COLUMNS.items():
        actual = await _fetch_table_columns(pool, table)
        if not actual:
            errors.append(f"table '{table}' missing entirely")
            continue
        missing = required - actual
        if missing:
            errors.append(
                f"table '{table}' missing columns: {sorted(missing)}"
            )

    if errors:
        msg = "Schema verification failed:\n  - " + "\n  - ".join(errors)
        logger.error(msg)
        raise SchemaMismatchError(msg)

    logger.info(
        "schema verified: %d tables, %d columns total",
        len(REQUIRED_COLUMNS),
        sum(len(v) for v in REQUIRED_COLUMNS.values()),
    )
