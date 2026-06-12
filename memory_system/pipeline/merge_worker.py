"""
pipeline/merge_worker.py — Async bounded worker for write-time memory merging.

Architecture:
    ExtractionWorker (existing)
        └─ enqueue() → MergeTaskQueue (bounded asyncio.Queue)
                └─ MergeWorker (semaphore-bounded coroutines)
                        └─ vector search → MergeJudge → write/update/delete

Resource guarantees:
    - Semaphore ensures at most `merge_concurrency` concurrent LLM calls
    - async with semaphore releases on exception too (no leaked slots)
    - Queue backpressure: overflow drops silently with a warning log
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Awaitable

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class MergeTask:
    person_id: str
    memory_entry: dict        # {type, content, importance, confidence, ...}
    session_id: str
    turn_index: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    priority: int = 0         # reserved, unused


class MergeWorker:
    """
    Async bounded worker. Call start() before use, stop() when done.

    Args:
        merge_fn: Async callable that does the actual merge logic.
                  Injected for testability.
        concurrency: Max simultaneous merge_fn calls.
        queue_maxsize: Max queued tasks before dropping.
    """

    def __init__(
        self,
        merge_fn: Callable[[MergeTask], Awaitable[None]] | None = None,
        concurrency: int | None = None,
        queue_maxsize: int | None = None,
    ) -> None:
        self._merge_fn = merge_fn or self._default_merge
        self._concurrency = concurrency or settings.merge_concurrency
        self._queue_maxsize = queue_maxsize or settings.merge_queue_maxsize
        self._queue: asyncio.Queue[MergeTask] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(self._concurrency)
        self._running = False
        self._consumer_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    async def drain(self) -> None:
        """Wait until queue is empty and all tasks complete."""
        await self._queue.join()

    async def enqueue(self, task: MergeTask, timeout: float = 0.1) -> bool:
        """
        Enqueue a merge task. Returns True if accepted, False if dropped.
        On queue full: drops silently with a warning log.
        """
        try:
            self._queue.put_nowait(task)
            return True
        except asyncio.QueueFull:
            logger.warning(
                "merge_worker queue full (size=%d), dropping task person=%s",
                self._queue_maxsize,
                task.person_id,
            )
            return False

    async def _consume(self) -> None:
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                asyncio.create_task(self._process(task))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _process(self, task: MergeTask) -> None:
        async with self._semaphore:
            try:
                await self._merge_fn(task)
            except Exception:
                logger.exception("merge_worker error for person=%s", task.person_id)
            finally:
                self._queue.task_done()

    async def _default_merge(self, task: MergeTask) -> None:
        """
        Production merge logic with actual storage.
        """
        from llm.skills.merge_judge import MergeJudge, MergeJudgeInput
        from storage.pg_store import PgStore, create_pool
        from storage.redis_store import RedisStore
        from models import MemoryEntry
        import uuid

        # 初始化存储（如果未初始化）
        if not hasattr(self, '_pg_store'):
            self._pg_store = PgStore(await create_pool())
        if not hasattr(self, '_redis_store'):
            self._redis_store = await RedisStore.create()

        new_mem = task.memory_entry

        # 构造MemoryEntry对象
        entry = MemoryEntry(
            memory_id=uuid.UUID(new_mem["memory_id"]),
            person_id=task.person_id,
            session_id=task.session_id,
            memory_type=new_mem["type"],
            content=new_mem["content"],
            importance_score=new_mem["importance"],
            confidence_score=new_mem["confidence"],
            emotional_valence=new_mem.get("emotional_valence"),
            emotional_intensity=new_mem.get("emotional_intensity"),
            embedding=new_mem.get("embedding"),
            source_message=new_mem.get("source_message"),
            source_turn_index=new_mem.get("source_turn_index"),
        )

        # 写入存储
        is_new = await self._redis_store.write_memory(entry)
        if is_new:
            await self._pg_store.insert_memory(entry)
            # 提取关键词并建立索引
            import re
            words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', entry.content)
            keywords = list(set(w.lower() for w in words))[:10]
            if keywords:
                await self._redis_store.add_keyword_index(
                    task.person_id, keywords, str(entry.memory_id)
                )
            await self._redis_store.invalidate_recall_cache(task.person_id)

        logger.debug(
            "merge_worker stored person=%s type=%s content=%.40s",
            task.person_id, entry.memory_type, entry.content,
        )


# Module-level singleton for use by pipeline/worker.py
_worker_instance: MergeWorker | None = None


def get_merge_worker() -> MergeWorker:
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = MergeWorker()
    return _worker_instance
