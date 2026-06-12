"""
定时任务：摘要生成 + 记忆融合 + 清理过期记忆。

借鉴 claude-mem 的设计：定期更新 person summary 保持摘要新鲜。
"""
from __future__ import annotations

import asyncio as _asyncio
import datetime as _datetime
import logging
import math
from datetime import datetime, timezone

import anthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from retrieval.merger import merge_similar_memories
from retrieval.summary import build_summary
from storage.pg_store import PgStore
from storage.redis_store import RedisStore

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

_forget_task_registry: set = set()


async def refresh_active_summaries(pg: PgStore, redis: RedisStore) -> None:
    """
    为最近活跃的用户重新生成 person summary。
    每 10 分钟运行一次。
    """
    async with pg._pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT person_id, display_name FROM persons
               WHERE last_active_at > NOW() - INTERVAL '1 hour'
               ORDER BY last_active_at DESC
               LIMIT 100"""
        )

    for row in rows:
        person_id = str(row["person_id"])
        display_name = row.get("display_name")
        try:
            items, _ = await pg.list_memories_by_person(person_id, limit=30)
            if not items:
                continue
            summary = build_summary(display_name, items)
            if summary:
                # 截取前 200 字作为摘要
                short = summary.replace("## ", "").replace("**", "").replace("\n", " ")[:200]
                await pg.update_person_summary(person_id, short)
                # 更新 Redis
                profile_data = await redis.get_profile(person_id)
                if profile_data:
                    await redis.update_profile_cas(
                        person_id,
                        int(profile_data.get("version", 1)),
                        {"summary": short},
                    )
        except Exception as e:
            logger.warning("summary refresh failed for %s: %s", person_id, e)


async def run_merge_scan(pg: PgStore) -> None:
    """
    扫描活跃用户的相似记忆并合并。
    每 1 小时运行一次。
    """
    async with pg._pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT person_id FROM memory_entries
               WHERE created_at > NOW() - INTERVAL '24 hours'
               AND is_merged = FALSE
               LIMIT 200"""
        )
    for row in rows:
        person_id = str(row["person_id"])
        try:
            count = await merge_similar_memories(person_id, pg)
            if count > 0:
                logger.info("merged %d memories for %s", count, person_id)
        except Exception as e:
            logger.warning("merge scan failed for %s: %s", person_id, e)


async def cleanup_expired_memories(pg: PgStore) -> None:
    """
    清理 expires_at 已过期的记忆。
    每 24 小时运行一次。
    """
    async with pg._pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM memory_entries WHERE expires_at < NOW() AND expires_at IS NOT NULL"
        )
    logger.info("cleaned up expired memories: %s", result)


async def cleanup_old_tasks(pg: PgStore) -> None:
    """清理 7 天前的已完成任务"""
    async with pg._pool.acquire() as conn:
        result = await conn.execute(
            """DELETE FROM extraction_tasks
               WHERE status = 'done' AND completed_at < NOW() - INTERVAL '7 days'"""
        )
    logger.info("cleaned up old tasks: %s", result)


def compute_forget_score(importance: float, lam: float, days: float) -> float:
    """forget_score = importance × e^(−λ × days)"""
    return importance * math.exp(-lam * days)


_HARD_PROTECT_KEYWORDS = {"姓名", "年龄", "性别", "名字", "叫做", "出生"}


def apply_hard_protection(candidates: list[dict]) -> list[dict]:
    """
    Remove candidates that must never be deleted:
    - identity type + contains name/age/gender keywords
    - importance_score >= settings.forget_importance_protect
    """
    result = []
    for c in candidates:
        mem_type = c.get("memory_type", "")
        importance = c.get("importance_score", 0.0)
        content = c.get("content", "")

        if importance >= settings.forget_importance_protect:
            continue  # protected

        if mem_type == "identity" and any(kw in content for kw in _HARD_PROTECT_KEYWORDS):
            continue  # protected

        result.append(c)
    return result


async def forget_scan_job(pg, redis) -> None:
    """
    Daily job: dual-track candidate selection → hard protection → ForgetJudge → delete.

    Track 1: decay score < threshold
    Track 2: per-person per-type quota overflow
    """
    from llm.skills.forget_judge import ForgetJudge, ForgetJudgeInput

    log = logging.getLogger(__name__)
    judge = ForgetJudge()

    async def process_person(person_id: str) -> None:
        rows = await pg.get_candidates_for_forget(
            person_id,
            forget_score_threshold=settings.forget_score_threshold * 5,
            limit=2000,
        )

        now = _datetime.datetime.now(_datetime.timezone.utc)
        candidates = []
        for row in rows:
            last = row.get("last_accessed_at") or row.get("created_at")
            days = (now - last).days if last else 0
            mem_type = row.get("memory_type", "preference")
            lam = settings.forget_lambda.get(mem_type, 0.010)
            score = compute_forget_score(row["importance_score"], lam, days)
            if score < settings.forget_score_threshold:
                candidates.append({**row, "days_old": days, "forget_score": score})

        if not candidates:
            return

        candidates = apply_hard_protection(candidates)

        sem = _asyncio.Semaphore(settings.forget_judge_concurrency)
        chunks = [
            candidates[i:i + settings.forget_batch_size]
            for i in range(0, len(candidates), settings.forget_batch_size)
        ]

        async def judge_chunk(chunk: list[dict]) -> None:
            async with sem:
                try:
                    result = await judge.decide(ForgetJudgeInput(candidates=chunk))
                    if result.delete_ids:
                        await pg.soft_delete(result.delete_ids)
                        if hasattr(redis, "remove_from_sorted_sets"):
                            await redis.remove_from_sorted_sets(result.delete_ids)
                        task = _asyncio.create_task(
                            pg.hard_delete_later(
                                result.delete_ids,
                                delay=settings.forget_hard_delete_delay,
                            )
                        )
                        _forget_task_registry.add(task)
                        task.add_done_callback(_forget_task_registry.discard)
                except Exception:
                    log.exception("forget_scan_job chunk failed, skipping")

        await _asyncio.gather(*[judge_chunk(chunk) for chunk in chunks])

    # NOTE: In production, iterate over all active persons
    # For MVP, pg.get_all_person_ids() would provide the list
    log.info("forget_scan_job: completed (no person iteration in MVP skeleton)")


def create_scheduler(pg: PgStore, redis: RedisStore) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        refresh_active_summaries, "interval", minutes=10,
        kwargs={"pg": pg, "redis": redis},
        id="refresh_summaries",
    )
    scheduler.add_job(
        run_merge_scan, "interval", hours=1,
        kwargs={"pg": pg},
        id="merge_scan",
    )
    scheduler.add_job(
        cleanup_expired_memories, "interval", hours=24,
        kwargs={"pg": pg},
        id="cleanup_expired",
    )
    scheduler.add_job(
        cleanup_old_tasks, "interval", hours=24,
        kwargs={"pg": pg},
        id="cleanup_tasks",
    )
    scheduler.add_job(
        forget_scan_job, "interval", hours=24,
        kwargs={"pg": pg, "redis": redis},
        id="forget_scan",
    )

    return scheduler
