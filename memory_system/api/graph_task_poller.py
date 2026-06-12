"""A1 常驻 graph task poller。

与 P0-1 Outbox 配套：
  - extraction_tasks 里残留的 pending（或 lease 超时的 processing）任务，
    由本 poller 持续拉取并重投到 _background_extract。
  - SELECT ... FOR UPDATE SKIP LOCKED + UPDATE → processing，保证多实例安全。
  - 领取后的 task 走和在线 POST 同一条 _background_extract 路径，
    由 EXTRACT_MAX_CONCURRENT 信号量限速，LLM 并发不会被 poller 撑爆。

启动在 api.main.lifespan，停止在 lifespan 退出时。
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.client import LLMClient
    from storage.pg_store import GraphStore, PgStore

logger = logging.getLogger(__name__)

# 轮询间隔：pending 非空时 IDLE_FAST，空时 IDLE_SLOW
_IDLE_FAST = float(os.environ.get("GRAPH_POLLER_FAST_SEC", "0.5"))
_IDLE_SLOW = float(os.environ.get("GRAPH_POLLER_SLOW_SEC", "3.0"))
# 单轮 claim 上限：控制 backlog 释放速度，别一次抓太多撑爆 EXTRACT_SEM
_BATCH = int(os.environ.get("GRAPH_POLLER_BATCH", "20"))
# processing 状态的 lease 超时：超过则视为僵死，可重认领
_LEASE_SEC = int(os.environ.get("GRAPH_POLLER_LEASE_SEC", "300"))


class GraphTaskPoller:
    def __init__(self, pg: "PgStore", gs: "GraphStore", llm: "LLMClient"):
        self.pg = pg
        self.gs = gs
        self.llm = llm
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        # 启动时 reset：把本实例 owned 的"processing"→pending
        # 正确性：memory-allinone 是单实例部署，重启后不会有其他活进程在跑这些 task。
        # 未来多实例化时需要改为按 worker_id 过滤或纯靠 lease 超时。
        try:
            async with self.pg._pool.acquire() as conn:
                r = await conn.execute(
                    """UPDATE extraction_tasks
                          SET status='pending'
                        WHERE task_type='graph' AND status='processing'"""
                )
                logger.info(f"[poller] startup reset processing→pending: {r}")
        except Exception as e:
            logger.warning(f"[poller] startup reset failed: {e}")
        self._task = asyncio.create_task(self._run(), name="graph-task-poller")
        logger.info(
            f"[poller] started batch={_BATCH} fast={_IDLE_FAST}s "
            f"slow={_IDLE_SLOW}s lease={_LEASE_SEC}s"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except asyncio.TimeoutError:
            self._task.cancel()
        except Exception:
            pass
        self._task = None
        logger.info("[poller] stopped")

    async def _run(self) -> None:
        # 延迟 import 避开循环
        from api.routers.memory_chat import _background_extract, _get_extract_semaphore

        sem = _get_extract_semaphore()

        while not self._stop.is_set():
            picked = 0
            try:
                # 只抢"能立刻跑"的量：sem 还剩多少就抢多少（上限 _BATCH）
                # 避免把上千行先置为 processing 但实际堵在内存队列。
                free = getattr(sem, "_value", _BATCH)  # asyncio.Semaphore 内部计数
                claim_n = max(0, min(_BATCH, int(free)))
                if claim_n == 0:
                    # sem 满，等一点再轮询
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=_IDLE_FAST)
                    except asyncio.TimeoutError:
                        pass
                    continue

                rows = await self.pg.claim_pending_graph_tasks(
                    limit=claim_n, stale_seconds=_LEASE_SEC
                )
                picked = len(rows)
                for row in rows:
                    task_id = str(row.get("task_id"))
                    payload = row.get("payload") or {}
                    if isinstance(payload, str):
                        try:
                            payload = _json.loads(payload)
                        except Exception:
                            logger.warning(
                                f"[poller] task={task_id} payload parse failed, mark failed"
                            )
                            try:
                                await self.pg.update_task_status(
                                    task_id, "failed", error="payload parse failed"
                                )
                            except Exception:
                                pass
                            continue

                    owner_id = payload.get("owner_id") or str(row.get("owner_id") or "")
                    person_id = payload.get("person_id") or str(row.get("person_id") or "")
                    session_id = payload.get("session_id") or str(row.get("session_id") or "")
                    user_name = payload.get("user_name") or ""
                    buffer = payload.get("buffer") or []

                    if not (owner_id and person_id and session_id and buffer):
                        logger.warning(
                            f"[poller] task={task_id} missing fields, mark failed"
                        )
                        try:
                            await self.pg.update_task_status(
                                task_id, "failed", error="poller: missing fields"
                            )
                        except Exception:
                            pass
                        continue

                    # fire-and-forget：sem 自行限速，poller 不阻塞
                    asyncio.create_task(
                        _background_extract(
                            self.llm, self.gs, owner_id, person_id,
                            session_id, user_name, buffer,
                            task_id=task_id, pg=self.pg,
                        ),
                        name=f"extract-{task_id[:8]}",
                    )
            except Exception as e:
                logger.error(f"[poller] loop error: {e}")

            # 有活就快轮询、没活慢轮询
            delay = _IDLE_FAST if picked > 0 else _IDLE_SLOW
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


_poller: GraphTaskPoller | None = None


def get_poller() -> GraphTaskPoller | None:
    return _poller


def set_poller(p: GraphTaskPoller | None) -> None:
    global _poller
    _poller = p
