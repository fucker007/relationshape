"""
Redis 热路径存储。

设计要点（借鉴两个参考系统）：
- 所有 key 使用 {person_id} hash tag，同人 key 落同一 slot（Redis Cluster 安全）
- 去重通过 Lua 脚本原子化（SISMEMBER + SADD）
- Person profile 更新通过 Lua 脚本实现乐观锁
- MemoryEntry 详情用 Hash 存储，TTL 24h（LRU 冷却后从 PG 回填）
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from redis.asyncio import Redis

from config import settings
from models import MemoryEntry, MemorySearchResult, MemoryType, PersonProfile

# ---------------------------------------------------------------------------
# Lua scripts（原子操作，防止并发竞争）
# ---------------------------------------------------------------------------

# 去重检查 + 原子写入指纹
# KEYS[1] = fingerprints set key
# ARGV[1] = fingerprint
# Returns: 1 = 新记忆（已写入），0 = 重复（跳过）
_LUA_DEDUP_CHECK = """
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then
    return 0
else
    redis.call('SADD', KEYS[1], ARGV[1])
    redis.call('EXPIRE', KEYS[1], ARGV[2])
    return 1
end
"""

# Person profile 乐观锁更新
# KEYS[1] = profile hash key
# ARGV[1] = expected version, ARGV[2] = new version, ARGV[3..] = field/value pairs
# Returns: 1 = 成功，0 = 版本冲突（调用方重试）
_LUA_PROFILE_CAS = """
local current = redis.call('HGET', KEYS[1], 'version')
if current == false or tonumber(current) == tonumber(ARGV[1]) then
    local args = {'HSET', KEYS[1]}
    for i = 3, #ARGV do
        table.insert(args, ARGV[i])
    end
    redis.call(unpack(args))
    redis.call('EXPIRE', KEYS[1], ARGV[2])
    return 1
else
    return 0
end
"""


def _profile_key(person_id: str) -> str:
    return f"{{person:{person_id}}}:profile"


def _memories_key(person_id: str, memory_type: str) -> str:
    return f"{{person:{person_id}}}:memories:{memory_type}"


def _memory_detail_key(memory_id: str) -> str:
    # memory 详情 key 不包含 person_id，跨 slot
    # 但 Sorted Set 只存 memory_id，详情从 PG 读取
    return f"memory:{memory_id}"


def _fingerprints_key(person_id: str) -> str:
    return f"{{person:{person_id}}}:fingerprints"


def _recall_cache_key(person_id: str, context_hash: str) -> str:
    return f"{{person:{person_id}}}:recall:{context_hash}"


def _compute_fingerprint(memory_type: str, content: str, event_type: str = "", participants: list[str] | None = None) -> str:
    """计算记忆指纹，用于去重。

    对于事件类型，包含 event_type 和 participants 以避免相同内容但不同事件的重复。
    """
    normalized = content.strip().lower()

    # 事件类型的记忆需要包含事件类型和参与者
    if memory_type == "event" and event_type:
        participants_str = ",".join(sorted(participants or [])) if participants else ""
        raw = f"{memory_type}:{event_type}:{participants_str}:{normalized}"
    else:
        raw = f"{memory_type}:{normalized}"

    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _sorted_set_score(importance: float, created_at_ms: int) -> float:
    """importance 高的排前，同分按时间降序（越新越靠前）"""
    return importance * 1e12 + created_at_ms


# ---------------------------------------------------------------------------
# RedisStore
# ---------------------------------------------------------------------------

class RedisStore:
    def __init__(self, client: Redis) -> None:
        self._r = client
        self._dedup_script = self._r.register_script(_LUA_DEDUP_CHECK)
        self._cas_script = self._r.register_script(_LUA_PROFILE_CAS)

    @classmethod
    async def create(cls) -> "RedisStore":
        client = aioredis.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
        )
        return cls(client)

    async def close(self) -> None:
        await self._r.aclose()

    # ------------------------------------------------------------------
    # Person profile
    # ------------------------------------------------------------------

    async def get_profile(self, person_id: str) -> dict[str, Any] | None:
        data = await self._r.hgetall(_profile_key(person_id))
        if not data:
            return None
        if "memory_counts" in data:
            data["memory_counts"] = json.loads(data["memory_counts"])
        return data

    async def set_profile(self, profile: PersonProfile) -> None:
        key = _profile_key(str(profile.person_id))
        counts = json.dumps(profile.memory_counts)
        await self._r.hset(key, mapping={
            "person_id":    str(profile.person_id),
            "external_id":  profile.external_id,
            "display_name": profile.display_name or "",
            "summary":      profile.summary or "",
            "memory_counts": counts,
            "total_interactions": profile.total_interactions,
            "last_active_at": profile.last_active_at.isoformat(),
            "version":      profile.version,
        })
        await self._r.expire(key, settings.redis_profile_ttl)

    async def update_profile_cas(
        self,
        person_id: str,
        expected_version: int,
        updates: dict[str, Any],
    ) -> bool:
        """乐观锁更新 profile，返回 True 表示成功，False 表示版本冲突"""
        key = _profile_key(person_id)
        new_version = expected_version + 1
        flat: list[str] = ["version", str(new_version)]
        for k, v in updates.items():
            flat.extend([k, str(v)])
        result = await self._cas_script(
            keys=[key],
            args=[str(expected_version), str(settings.redis_profile_ttl)] + flat,
        )
        return bool(result)

    # ------------------------------------------------------------------
    # Memory entries（写入）
    # ------------------------------------------------------------------

    async def write_memory(self, entry: MemoryEntry) -> bool:
        """
        写入记忆条目。
        1. 去重检查（原子 Lua）
        2. 写入 Sorted Set（按重要性排序）
        3. 写入 Hash 详情（TTL 24h）
        返回 True = 新写入，False = 重复跳过
        """
        person_id = str(entry.person_id)
        memory_id = str(entry.memory_id)

        # 提取事件类型和参与者用于更精确的去重（数据在 structured_data，MemoryEntry 无 metadata 字段）
        event_type = ""
        participants = []
        meta = entry.structured_data or {}
        if entry.memory_type == "event" and meta:
            event_type = meta.get("event_type", "")
            participants = meta.get("participants", [])

        fingerprint = _compute_fingerprint(entry.memory_type, entry.content, event_type, participants)

        # 去重
        is_new = await self._dedup_script(
            keys=[_fingerprints_key(person_id)],
            args=[fingerprint, str(settings.redis_memories_ttl)],
        )
        if not is_new:
            return False

        created_ms = int(entry.created_at.timestamp() * 1000)
        score = _sorted_set_score(entry.importance_score, created_ms)

        pipe = self._r.pipeline(transaction=False)

        # Sorted Set：按重要性存 memory_id
        pipe.zadd(
            _memories_key(person_id, entry.memory_type),
            {memory_id: score},
        )
        pipe.expire(
            _memories_key(person_id, entry.memory_type),
            settings.redis_memories_ttl,
        )

        # Hash 详情（不存 embedding，节省内存）
        detail_key = _memory_detail_key(memory_id)
        pipe.hset(detail_key, mapping={
            "memory_id":         memory_id,
            "person_id":         person_id,
            "memory_type":       entry.memory_type,
            "content":           entry.content,
            "structured_data":   json.dumps(entry.structured_data),
            "importance_score":  entry.importance_score,
            "confidence_score":  entry.confidence_score,
            "emotional_valence": entry.emotional_valence,
            "emotional_intensity": entry.emotional_intensity,
            "source_message":    entry.source_message,
            "created_at":        entry.created_at.isoformat(),
        })
        pipe.expire(detail_key, settings.redis_memory_detail_ttl)

        await pipe.execute()
        return True

    # ------------------------------------------------------------------
    # Memory entries（读取）
    # ------------------------------------------------------------------

    async def get_top_memories_by_type(
        self,
        person_id: str,
        memory_type: MemoryType,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        从 Sorted Set 取 Top-N 条（按综合分数倒序）。
        仅返回存储在 Redis 中的字段，不触发 PG 查询。
        """
        key = _memories_key(person_id, memory_type)
        # ZREVRANGEBYSCORE 取最高分
        memory_ids = await self._r.zrevrange(key, 0, limit - 1)
        if not memory_ids:
            return []

        pipe = self._r.pipeline(transaction=False)
        for mid in memory_ids:
            pipe.hgetall(_memory_detail_key(mid))
        results = await pipe.execute()

        out = []
        for data in results:
            if data:
                if "structured_data" in data:
                    data["structured_data"] = json.loads(data["structured_data"])
                out.append(data)
        return out

    async def get_all_type_top_memories(
        self,
        person_id: str,
        per_type: int = 3,
    ) -> list[dict[str, Any]]:
        """路径 B：各类型 Top-N，作为高权重兜底"""
        results: list[dict[str, Any]] = []
        for mt in MemoryType:
            items = await self.get_top_memories_by_type(person_id, mt, per_type)
            results.extend(items)
        return results

    # ------------------------------------------------------------------
    # Recall cache（5 分钟，新记忆写入时主动失效）
    # ------------------------------------------------------------------

    async def get_recall_cache(self, person_id: str, context_hash: str) -> str | None:
        return await self._r.get(_recall_cache_key(person_id, context_hash))

    async def set_recall_cache(
        self, person_id: str, context_hash: str, summary: str
    ) -> None:
        key = _recall_cache_key(person_id, context_hash)
        await self._r.setex(key, settings.redis_recall_cache_ttl, summary)

    async def invalidate_recall_cache(self, person_id: str) -> None:
        """新记忆写入后主动删除该 person 的所有 recall 缓存"""
        pattern = f"{{person:{person_id}}}:recall:*"
        keys = await self._r.keys(pattern)
        if keys:
            await self._r.delete(*keys)

    # ------------------------------------------------------------------
    # 关键词反向索引（路径 C：精确匹配）
    # ------------------------------------------------------------------

    async def add_keyword_index(
        self, person_id: str, keywords: list[str], memory_id: str
    ) -> None:
        pipe = self._r.pipeline(transaction=False)
        for kw in keywords:
            kw_key = f"{{person:{person_id}}}:kw:{kw.lower()}"
            pipe.sadd(kw_key, memory_id)
            pipe.expire(kw_key, settings.redis_memories_ttl)
        await pipe.execute()

    async def keyword_search(
        self, person_id: str, keywords: list[str]
    ) -> set[str]:
        """返回匹配任意关键词的 memory_id 集合"""
        if not keywords:
            return set()
        keys = [f"{{person:{person_id}}}:kw:{kw.lower()}" for kw in keywords]
        # SUNION：取并集
        result = await self._r.sunion(*keys)
        return set(result)

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        try:
            return await self._r.ping()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Session buffer（滑动窗口，用于记忆提取）
    # ------------------------------------------------------------------

    async def append_session(self, session_id: str, user_msg: str, assistant_msg: str) -> int:
        """追加一轮对话到 session buffer，返回当前用户消息数"""
        key = f"session:{session_id}"
        pipe = self._r.pipeline(transaction=False)
        pipe.rpush(key, json.dumps({"role": "user", "content": user_msg}, ensure_ascii=False))
        pipe.rpush(key, json.dumps({"role": "assistant", "content": assistant_msg}, ensure_ascii=False))
        pipe.ltrim(key, -20, -1)   # 保留最近20条(10轮)
        pipe.expire(key, 1800)     # 30分钟过期
        pipe.lrange(key, 0, -1)    # 读取当前 buffer
        results = await pipe.execute()
        buffer = [json.loads(item) for item in results[4]]
        return len([t for t in buffer if t["role"] == "user"])

    async def get_session_buffer(self, session_id: str) -> list[dict]:
        """读取 session buffer"""
        key = f"session:{session_id}"
        items = await self._r.lrange(key, 0, -1)
        return [json.loads(item) for item in items]

    async def get_recent_turns(self, session_id: str, n: int = 8) -> list[dict]:
        """读取最近 n 条 turn（L0.5 即时上下文，用于当前会话指代消解）"""
        key = f"session:{session_id}"
        items = await self._r.lrange(key, -n, -1)
        return [json.loads(item) for item in items]

    # ------------------------------------------------------------------
    # Recall decay cache（session 级事件衰减缓存，保持对话连续性）
    # ------------------------------------------------------------------
    _RECALL_DECAY_TTL = 300  # 5 分钟无对话自动过期

    async def get_decay_cache(self, session_id: str) -> list[dict]:
        """读取 session 的召回衰减缓存"""
        key = f"recall_decay:{session_id}"
        raw = await self._r.get(key)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    async def set_decay_cache(self, session_id: str, events: list[dict]) -> None:
        """写入 session 的召回衰减缓存"""
        key = f"recall_decay:{session_id}"
        await self._r.setex(
            key, self._RECALL_DECAY_TTL,
            json.dumps(events, ensure_ascii=False, default=str),
        )
