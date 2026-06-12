"""
任务状态查询路由
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_pg
from models import TaskStatusResponse
from storage.pg_store import PgStore

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}/status", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    pg: Annotated[PgStore, Depends(get_pg)],
) -> TaskStatusResponse:
    task = await pg.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        error=task.get("error_message"),
        completed_at=task.get("completed_at"),
    )
