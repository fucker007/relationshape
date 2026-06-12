"""
记忆融合：将相似度高的重复记忆合并。

触发场景：
1. 实时融合：写入时检查向量余弦相似度 > 0.92
2. 定时批量融合（scheduler 调用）：按 person 扫描所有类型
"""
from __future__ import annotations

import logging
from typing import Any

from storage.pg_store import PgStore

logger = logging.getLogger(__name__)


async def merge_similar_memories(
    person_id: str,
    pg: PgStore,
    similarity_threshold: float = 0.92,
) -> int:
    """
    扫描某人的所有记忆，合并向量余弦相似度超过阈值的记忆对。
    返回合并的记忆数量。
    """
    # 目前使用简单策略：按内容指纹去重
    # 真正的向量相似度合并依赖 pgvector 的 <=> 操作符
    merged_count = 0
    try:
        async with pg._pool.acquire() as conn:
            # 找出同一 person + 同一 type 中向量相似度 > threshold 的对
            rows = await conn.fetch(
                """
                SELECT a.memory_id AS id_a, b.memory_id AS id_b,
                       1 - (a.embedding <=> b.embedding) AS similarity
                FROM memory_entries a
                JOIN memory_entries b ON a.person_id = b.person_id
                    AND a.memory_type = b.memory_type
                    AND a.memory_id < b.memory_id
                WHERE a.person_id = $1
                  AND a.is_merged = FALSE
                  AND b.is_merged = FALSE
                  AND a.embedding IS NOT NULL
                  AND b.embedding IS NOT NULL
                  AND 1 - (a.embedding <=> b.embedding) > $2
                ORDER BY similarity DESC
                LIMIT 100
                """,
                person_id, similarity_threshold,
            )

        for row in rows:
            id_a, id_b = str(row["id_a"]), str(row["id_b"])
            # 保留重要性更高的，标记另一个为已合并
            async with pg._pool.acquire() as conn:
                result = await conn.fetchrow(
                    """SELECT memory_id, importance_score FROM memory_entries
                       WHERE memory_id = ANY($1::uuid[])
                       ORDER BY importance_score DESC LIMIT 1""",
                    [id_a, id_b],
                )
            if not result:
                continue

            winner_id = str(result["memory_id"])
            loser_id = id_b if winner_id == id_a else id_a
            await pg.mark_merged([loser_id], winner_id, reason="similarity_merge")
            merged_count += 1

    except Exception as e:
        logger.warning("merge_similar_memories failed: %s", e)

    if merged_count:
        logger.info("merged %d memories for person=%s", merged_count, person_id)
    return merged_count
