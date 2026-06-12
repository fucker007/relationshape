"""
scheduler/summary_jobs.py — 定时聚合任务

1. daily_emotion_aggregate: 每天聚合当日事件情绪 → daily_emotions 表
2. relationship_decay: 长期无互动的关系 intensity 衰减
3. focus_decay: current_focus 每日权重衰减 + 淘汰
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from storage.pg_store import GraphStore

logger = logging.getLogger(__name__)


async def daily_emotion_aggregate(gs: GraphStore, owner_id: str, person_id: str) -> dict | None:
    """
    聚合当日所有事件的情绪 → 写入 daily_emotions。
    返回 {emotion: count} 或 None（无事件）。
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    events = await gs.query_events(
        owner_id, time_from=today_start, time_to=today_end, limit=200)

    if not events:
        return None

    emotion_counter: Counter = Counter()
    for e in events:
        em = e.get("emotion_summary")
        if em and em != "平静":
            emotion_counter[em] += 1

    if not emotion_counter:
        return None

    total = sum(emotion_counter.values())
    distribution = {k: round(v / total, 2) for k, v in emotion_counter.items()}
    dominant = emotion_counter.most_common(1)[0][0]

    await gs.upsert_daily_emotion(
        owner_id, person_id, date.today(),
        distribution, dominant, event_count=len(events),
    )

    logger.info("daily_emotion_aggregate: %s → %s (dominant=%s, events=%d)",
                person_id[:8], distribution, dominant, len(events))
    return distribution


async def relationship_decay(gs: GraphStore, owner_id: str, decay_days: int = 30) -> int:
    """
    长期无互动关系 intensity 衰减。
    超过 decay_days 未互动 → intensity *= 0.95
    返回衰减的关系数。
    """
    rels = await gs.get_relationships(owner_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=decay_days)
    decayed = 0

    for r in rels:
        last_time = r.get("last_event_time")
        if last_time and last_time < cutoff:
            # intensity 衰减（通过 upsert 加负 delta 模拟）
            current_intensity = float(r.get("intensity", 0))
            if current_intensity > 0.1:
                # 直接 SQL 更新 intensity（upsert_relationship 没有 intensity 减少接口）
                async with gs._pool.acquire() as conn:
                    await conn.execute(
                        """UPDATE relationships
                           SET intensity = GREATEST(0, intensity * 0.95),
                               updated_at = NOW()
                           WHERE id = $1""",
                        str(r["id"]),
                    )
                decayed += 1

    if decayed:
        logger.info("relationship_decay: %d relationships decayed for owner %s", decayed, owner_id[:8])
    return decayed


async def focus_decay(gs: GraphStore, person_id: str, decay_rate: float = 0.85) -> int:
    """
    current_focus 每日权重衰减 + 淘汰 weight < 0.2 的条目。
    返回淘汰的 focus 数。
    """
    node = await gs.get_person_node(person_id)
    if not node:
        return 0

    focus = node["current_focus"]
    if isinstance(focus, str):
        focus = json.loads(focus or "[]")

    today = date.today().isoformat()
    removed = 0
    new_focus = []

    for f in focus:
        # 只对非当天的 focus 衰减
        if f.get("last_seen") != today:
            f["weight"] = round(f.get("weight", 1.0) * decay_rate, 3)

        if f.get("weight", 0) >= 0.2:
            new_focus.append(f)
        else:
            removed += 1

    if removed or len(new_focus) != len(focus):
        await gs.update_person_field(person_id, "current_focus", new_focus)

    if removed:
        logger.info("focus_decay: removed %d stale focus items for %s", removed, person_id[:8])
    return removed
