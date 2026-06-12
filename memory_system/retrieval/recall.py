"""
三路并行召回（借鉴 claude-mem 的三层搜索 + OpenClaw 的混合搜索）。

路径 A：pgvector HNSW 向量搜索（语义相关）
路径 B：Redis Sorted Set Top-N（高权重兜底）
路径 C：Redis 关键词反向索引（精确匹配）

融合评分：
  final_score = 0.5 × semantic_similarity
              + 0.3 × importance_score
              + 0.2 × recency_factor

后处理：Top 15 条，每类最多 3 条（均衡性）
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from config import settings
from llm.skills.usage_gate import UsageGate, UsageGateInput
from models import MemorySearchResult, MemoryType
from pipeline.embedding import embed_text
from storage.pg_store import PgStore
from storage.redis_store import RedisStore

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    selected: list[dict]   # memories selected by UsageGate (max 3)
    used_gate: bool        # True if UsageGate was actually called
    reason: str = ""       # UsageGate reasoning (debug)
    confidence: str = ""   # upstream recall() confidence level


def _context_hash(context: str) -> str:
    return hashlib.sha256(context.encode()).hexdigest()[:16]


def _recency_factor(created_at_str: str) -> float:
    """越新的记忆分数越高：1 / (1 + days_ago × decay)"""
    try:
        if isinstance(created_at_str, datetime):
            dt = created_at_str.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        days_ago = (datetime.now(timezone.utc) - dt).days
        return 1.0 / (1.0 + days_ago * settings.recall_recency_decay)
    except Exception:
        return 0.5


def _extract_keywords(context: str) -> list[str]:
    words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', context)
    return list(set(w.lower() for w in words))[:15]


def _compute_confidence(scores: list[float]) -> Literal["high", "uncertain", "low", "empty"]:
    """
    基于召回分数分布计算置信度，防止 LLM 误用无关记忆。
    方案 A（绝对阈值） + 方案 B（相对突出度）组合。
    """
    if not scores:
        return "empty"
    top_score = scores[0]
    if top_score < 0.25:
        return "low"
    mean_score = sum(scores) / len(scores)
    relative_prominence = (top_score - mean_score) / top_score
    return "high" if relative_prominence >= 0.15 else "uncertain"



def _compute_final_score(
    semantic_sim: float,
    importance: float,
    recency: float,
) -> float:
    # 废弃：改用内联计算
    return 0.7 * semantic_sim + 0.2 * importance + 0.1 * recency


def _deduplicate_and_rank(
    combined: list[dict[str, Any]],
    limit: int,
    max_per_type: int,
) -> list[dict[str, Any]]:
    # 废弃：events 表无需去重
    return combined[:limit]


# ---------------------------------------------------------------------------
# Main recall function
# ---------------------------------------------------------------------------

async def recall(
    person_id: str,
    context: str,
    redis: RedisStore,
    pg: PgStore,
    limit: int | None = None,
    types: list[str] | None = None,
    min_importance: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], Literal["high", "uncertain", "low", "empty"]]:
    """
    记忆召回（从 memory_entries 表查询）。
    types: memory_type 列表 (identity/preference/behavior/aversion/experience等)
    返回 (records, sources_count, confidence)
    """
    limit = limit or settings.recall_default_limit
    min_importance = min_importance or settings.recall_min_importance

    # 向量搜索 memory_entries 表
    query_embedding = await embed_text(context)
    if not query_embedding:
        logger.warning(f"recall: empty embedding for context={context}")
        return [], {"vector": 0}, "empty"

    logger.info(f"recall: person_id={person_id}, context={context}, limit={limit}, min_importance={min_importance}")

    vector_results = await pg.vector_search(
        person_id=person_id,
        query_embedding=query_embedding,
        limit=limit * 3,
    )

    logger.info(f"recall: vector_search returned {len(vector_results)} results")

    # 过滤 + 评分
    combined: list[dict[str, Any]] = []
    for item in vector_results:
        # memory_type 过滤
        if types and item.get("memory_type") not in types:
            continue
        # importance 过滤
        importance = float(item.get("importance_score", 0.5))
        if importance < min_importance:
            logger.debug(f"recall: filtered out {item.get('content')[:30]} (importance={importance} < {min_importance})")
            continue

        # 使用相似度作为主要评分
        similarity = float(item.get("similarity", 0.0))
        recency = _recency_factor(item.get("created_at", ""))
        item["final_score"] = 0.7 * similarity + 0.2 * importance + 0.1 * recency
        combined.append(item)

    logger.info(f"recall: after filtering, {len(combined)} results remain")

    # 按 final_score 排序
    combined.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)
    results = combined[:limit]

    sources: dict[str, int] = {"vector": len(results)}
    scores = [r.get("final_score", 0.0) for r in results]
    confidence = _compute_confidence(scores)
    return results, sources, confidence


async def _keyword_search(
    redis: RedisStore, person_id: str, context: str
) -> set[str]:
    # 废弃：events 表不需要 Redis 关键词索引
    return set()


async def recall_with_gate(
    query: str,
    person_id: str,
    redis,
    pg,
    *,
    use_usage_gate: bool = True,
    limit: int | None = None,
    types: list[str] | None = None,
) -> tuple[list[dict], GateResult]:
    """
    Full recall pipeline: three-path search → Phase 1 → UsageGate LLM.

    Returns:
        (selected_memories, gate_result)
        selected_memories: list of memory dicts to inject into LLM context (max 3)
        gate_result: metadata about gate decision
    """
    entries, _sources, confidence = await recall(
        person_id=person_id,
        context=query,
        redis=redis,
        pg=pg,
        limit=limit,
        types=types,
    )

    # Skip gate when no results or confidence is too low
    if confidence in ("empty", "low") or not entries:
        return [], GateResult(selected=[], used_gate=False, confidence=confidence)

    if not use_usage_gate:
        return entries, GateResult(selected=entries, used_gate=False, confidence=confidence)

    gate = UsageGate()
    gate_output = await gate.decide(UsageGateInput(
        query=query,
        recalled_memories=entries[:5],
    ))

    # Resolve selected IDs back to full memory dicts
    id_to_entry = {e.get("memory_id", ""): e for e in entries}
    selected = [
        id_to_entry[mid] for mid in gate_output.selected[:3]
        if mid in id_to_entry
    ]

    return selected, GateResult(
        selected=selected,
        used_gate=True,
        reason=gate_output.reason,
        confidence=confidence,
    )
