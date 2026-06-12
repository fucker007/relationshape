"""
P0-1 Outbox: 启动时恢复未完成的 graph extract 任务。

场景：进程崩溃/restart 时，已 ACK 给客户端但后台 extract 没完成的 task
      会残留在 extraction_tasks 表里（status=pending/processing/failed）。
启动恢复 hook 扫出它们，重新投递到 asyncio loop。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.client import LLMClient
    from storage.pg_store import GraphStore, PgStore

logger = logging.getLogger(__name__)


async def resume_graph_tasks(
    pg: "PgStore",
    gs: "GraphStore",
    llm: "LLMClient",
    limit: int = 200,
) -> int:
    """扫 pending/processing/failed 的 graph task，重新投递到后台。

    Returns: 恢复的任务数。
    """
    # 延迟 import 避开循环
    from api.routers.memory_chat import _background_extract

    try:
        tasks = await pg.get_pending_graph_tasks(limit=limit)
    except Exception as e:
        logger.error(f"[recovery] get_pending_graph_tasks failed: {e}")
        return 0

    if not tasks:
        logger.info("[recovery] no pending graph tasks")
        return 0

    resumed = 0
    for row in tasks:
        task_id = str(row.get("task_id"))
        payload = row.get("payload") or {}
        # payload 可能是 str（JSONB→str）或 dict
        if isinstance(payload, str):
            import json as _json
            try:
                payload = _json.loads(payload)
            except Exception:
                logger.warning(f"[recovery] task={task_id} payload parse failed, skip")
                continue

        owner_id = payload.get("owner_id") or str(row.get("owner_id") or "")
        person_id = payload.get("person_id") or str(row.get("person_id") or "")
        session_id = payload.get("session_id") or str(row.get("session_id") or "")
        user_name = payload.get("user_name") or ""
        buffer = payload.get("buffer") or []

        if not (owner_id and person_id and session_id and buffer):
            logger.warning(f"[recovery] task={task_id} missing fields, mark failed")
            try:
                await pg.update_task_status(task_id, "failed", error="recovery: missing fields")
            except Exception:
                pass
            continue

        asyncio.create_task(
            _background_extract(
                llm, gs, owner_id, person_id,
                session_id, user_name, buffer,
                task_id=task_id, pg=pg,
            )
        )
        resumed += 1

    logger.info(f"[recovery] resumed {resumed}/{len(tasks)} graph tasks")
    return resumed
