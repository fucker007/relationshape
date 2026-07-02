"""
PostgreSQL 持久化存储（asyncpg + pgvector）。

表设计见 migrations.py。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from asyncpg import Pool

from config import settings
from models import MemoryEntry, MemorySearchResult, MemoryType, PersonProfile


# ---------------------------------------------------------------------------
# Pool factory
# ---------------------------------------------------------------------------

async def create_pool() -> Pool:
    return await asyncpg.create_pool(
        settings.pg_dsn,
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
        max_queries=50_000,
        max_inactive_connection_lifetime=300,
        command_timeout=30,
        init=_init_connection,
    )


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.execute("SET TIME ZONE 'UTC'")
    # 注册 pgvector 类型
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


# ---------------------------------------------------------------------------
# PgStore
# ---------------------------------------------------------------------------

class PgStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Schema migrations（启动时调用）
    # ------------------------------------------------------------------

    async def migrate(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
            # 补丁：为已存在但缺少列的旧表添加新列
            await conn.execute("""
                ALTER TABLE events
                    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW(),
                    ADD COLUMN IF NOT EXISTS event_time_raw VARCHAR(50),
                    ADD COLUMN IF NOT EXISTS source_message TEXT DEFAULT '',
                    ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS title VARCHAR(30) DEFAULT '',
                    ADD COLUMN IF NOT EXISTS raw_turns JSONB DEFAULT '[]'
            """)

            # P0-1 Outbox: extraction_tasks 扩展为图记忆持久化任务表
            await conn.execute("""
                ALTER TABLE extraction_tasks
                    ADD COLUMN IF NOT EXISTS owner_id UUID,
                    ADD COLUMN IF NOT EXISTS task_type VARCHAR(20) DEFAULT 'legacy',
                    ADD COLUMN IF NOT EXISTS payload JSONB
            """)
            for col in ("message_content", "message_role", "turn_index", "person_id", "session_id"):
                try:
                    await conn.execute(f"ALTER TABLE extraction_tasks ALTER COLUMN {col} DROP NOT NULL")
                except Exception:
                    pass
            # session_id 放宽为 VARCHAR(128)（对话层传短 hash 非 UUID）
            try:
                await conn.execute(
                    "ALTER TABLE extraction_tasks ALTER COLUMN session_id TYPE VARCHAR(128) USING session_id::text"
                )
            except Exception:
                pass
            # payload 改为 TEXT: DB 是 SQL_ASCII, jsonb 会拒 UTF8 client_encoding
            try:
                await conn.execute(
                    "ALTER TABLE extraction_tasks ALTER COLUMN payload TYPE TEXT USING payload::text"
                )
            except Exception:
                pass
            try:
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_extraction_tasks_graph_pending
                        ON extraction_tasks(scheduled_at)
                        WHERE task_type='graph' AND status IN ('pending','processing','failed')
                """)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Person profile
    # ------------------------------------------------------------------

    async def upsert_person(self, person: PersonProfile) -> PersonProfile:
        sql = """
        INSERT INTO persons (person_id, external_id, display_name, summary,
                             summary_updated_at, total_interactions,
                             first_seen_at, last_active_at, version, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (external_id) DO UPDATE SET
            display_name = COALESCE(EXCLUDED.display_name, persons.display_name),
            last_active_at = GREATEST(persons.last_active_at, EXCLUDED.last_active_at),
            total_interactions = persons.total_interactions + 1,
            version = persons.version + 1
        RETURNING *
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                str(person.person_id),
                person.external_id,
                person.display_name,
                person.summary,
                person.summary_updated_at,
                person.total_interactions,
                person.first_seen_at,
                person.last_active_at,
                person.version,
                person.created_at,
            )
        return _row_to_profile(dict(row))

    async def get_person_by_external_id(self, external_id: str) -> PersonProfile | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM persons WHERE external_id = $1", external_id
            )
        return _row_to_profile(dict(row)) if row else None

    async def get_person_by_id(self, person_id: str) -> PersonProfile | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM persons WHERE person_id = $1", person_id
            )
        return _row_to_profile(dict(row)) if row else None

    async def update_person_summary(
        self, person_id: str, summary: str
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE persons
                   SET summary = $1, summary_updated_at = NOW(), version = version + 1
                   WHERE person_id = $2""",
                summary, person_id,
            )

    # ------------------------------------------------------------------
    # Memory entries（写入）
    # ------------------------------------------------------------------

    async def insert_memory(self, entry: MemoryEntry) -> None:
        embedding_str = (
            "[" + ",".join(str(x) for x in entry.embedding) + "]"
            if entry.embedding else None
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_entries (
                    memory_id, person_id, session_id, memory_type, content,
                    structured_data, importance_score, confidence_score,
                    emotional_valence, emotional_intensity, embedding,
                    source_message, source_turn_index, extracted_by_model,
                    is_merged, merged_from, version, expires_at, created_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19
                )
                ON CONFLICT (memory_id) DO NOTHING
                """,
                str(entry.memory_id),
                str(entry.person_id),
                str(entry.session_id),
                entry.memory_type,
                entry.content,
                json.dumps(entry.structured_data),
                entry.importance_score,
                entry.confidence_score,
                entry.emotional_valence,
                entry.emotional_intensity,
                embedding_str,
                entry.source_message,
                entry.source_turn_index,
                entry.extracted_by_model,
                entry.is_merged,
                [str(m) for m in entry.merged_from],
                entry.version,
                entry.expires_at,
                entry.created_at,
            )

    async def batch_insert_memories(self, entries: list[MemoryEntry]) -> None:
        if not entries:
            return
        records = []
        for e in entries:
            embedding_str = (
                "[" + ",".join(str(x) for x in e.embedding) + "]"
                if e.embedding else None
            )
            records.append((
                str(e.memory_id), str(e.person_id), str(e.session_id),
                e.memory_type, e.content, json.dumps(e.structured_data),
                e.importance_score, e.confidence_score,
                e.emotional_valence, e.emotional_intensity,
                embedding_str, e.source_message, e.source_turn_index,
                e.extracted_by_model, e.is_merged,
                [str(m) for m in e.merged_from],
                e.version, e.expires_at, e.created_at,
            ))
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO memory_entries (
                    memory_id, person_id, session_id, memory_type, content,
                    structured_data, importance_score, confidence_score,
                    emotional_valence, emotional_intensity, embedding,
                    source_message, source_turn_index, extracted_by_model,
                    is_merged, merged_from, version, expires_at, created_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19
                )
                ON CONFLICT (memory_id) DO NOTHING
                """,
                records,
            )

    # ------------------------------------------------------------------
    # Memory entries（向量搜索，路径 A）
    # ------------------------------------------------------------------

    async def vector_search(
        self,
        person_id: str,
        query_embedding: list[float],
        limit: int = 20,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        type_filter = ""
        params: list[Any] = [person_id, embedding_str, limit]
        if types:
            type_filter = f"AND memory_type = ANY($4)"
            params.append(types)

        sql = f"""
        SELECT memory_id, person_id, memory_type, content, source_message,
               structured_data,
               importance_score, confidence_score, emotional_valence,
               emotional_intensity, created_at,
               1 - (embedding <=> $2::vector) AS similarity
        FROM memory_entries
        WHERE person_id = $1
          AND is_merged = FALSE
          AND (expires_at IS NULL OR expires_at > NOW())
          AND embedding IS NOT NULL
          {type_filter}
        ORDER BY embedding <=> $2::vector
        LIMIT $3
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_memory_dict(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Memory entries（按 ID 批量读取，Layer 3 完整详情）
    # ------------------------------------------------------------------

    async def get_memories_by_ids(self, memory_ids: list[str]) -> list[dict[str, Any]]:
        if not memory_ids:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM memory_entries WHERE memory_id = ANY($1::uuid[])",
                memory_ids,
            )
        return [_row_to_memory_dict(dict(r)) for r in rows]

    async def list_memories_by_person(
        self,
        person_id: str,
        memory_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        type_filter = "AND memory_type = $3" if memory_type else ""
        params: list[Any] = [person_id, limit, offset]
        if memory_type:
            params[2] = offset
            params.append(memory_type) if memory_type else None
            params = [person_id, memory_type, limit, offset]
            sql = f"""
            SELECT * FROM memory_entries
            WHERE person_id = $1 AND memory_type = $2 AND is_merged = FALSE
            ORDER BY importance_score DESC, created_at DESC
            LIMIT $3 OFFSET $4
            """
            count_sql = """
            SELECT COUNT(*) FROM memory_entries
            WHERE person_id = $1 AND memory_type = $2 AND is_merged = FALSE
            """
        else:
            sql = """
            SELECT * FROM memory_entries
            WHERE person_id = $1 AND is_merged = FALSE
            ORDER BY importance_score DESC, created_at DESC
            LIMIT $2 OFFSET $3
            """
            count_sql = """
            SELECT COUNT(*) FROM memory_entries
            WHERE person_id = $1 AND is_merged = FALSE
            """
            params = [person_id, limit, offset]

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            total = await conn.fetchval(
                count_sql, person_id, *([memory_type] if memory_type else [])
            )
        return [_row_to_memory_dict(dict(r)) for r in rows], total or 0

    # ------------------------------------------------------------------
    # 记忆融合（Merger 使用）
    # ------------------------------------------------------------------

    async def mark_merged(
        self, source_ids: list[str], result_id: str, reason: str = ""
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory_entries SET is_merged = TRUE WHERE memory_id = ANY($1::uuid[])",
                source_ids,
            )
            await conn.execute(
                """INSERT INTO memory_merge_log (person_id, source_ids, result_id, merge_reason)
                   SELECT person_id, $1::uuid[], $2, $3
                   FROM memory_entries WHERE memory_id = $2""",
                source_ids, result_id, reason,
            )

    # ------------------------------------------------------------------
    # Extraction tasks
    # ------------------------------------------------------------------

    async def create_task(self, task_id: str, person_id: str, session_id: str,
                          message: str, role: str, turn_index: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO extraction_tasks
                   (task_id, person_id, session_id, message_content,
                    message_role, turn_index)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                task_id, person_id, session_id, message, role, turn_index,
            )

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM extraction_tasks WHERE task_id = $1", task_id
            )
        return dict(row) if row else None

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        error: str | None = None,
        extracted_count: int | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            if status == "done":
                await conn.execute(
                    """UPDATE extraction_tasks
                       SET status=$1, completed_at=NOW(), retry_count=retry_count+1
                       WHERE task_id=$2""",
                    status, task_id,
                )
            elif status == "failed":
                await conn.execute(
                    """UPDATE extraction_tasks
                       SET status=$1, error_message=$2, retry_count=retry_count+1
                       WHERE task_id=$3""",
                    status, error, task_id,
                )
            else:
                await conn.execute(
                    "UPDATE extraction_tasks SET status=$1 WHERE task_id=$2",
                    status, task_id,
                )

    async def get_pending_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM extraction_tasks
                   WHERE status IN ('pending', 'failed')
                     AND retry_count < max_retries
                   ORDER BY scheduled_at
                   LIMIT $1""",
                limit,
            )
        return [dict(r) for r in rows]

    # --- P0-1 Outbox: graph extract tasks ---
    async def create_graph_task(
        self,
        task_id: str,
        owner_id: str,
        person_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        """为图记忆 extract 创建持久化 task (Outbox 模式).

        注意: DB 是 SQL_ASCII encoding, JSONB 拒绝直接 UTF-8 字节;
        用 ensure_ascii=True 把中文转 \\uXXXX 逃逸, 读取时 json.loads 自动还原.
        """
        import json as _json
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO extraction_tasks
                   (task_id, owner_id, person_id, session_id,
                    message_content, message_role, turn_index,
                    task_type, payload, status)
                   VALUES ($1,$2,$3,$4,'','',0,'graph',$5,'pending')""",
                task_id, owner_id, person_id, session_id,
                _json.dumps(payload, ensure_ascii=True),
            )

    async def get_pending_graph_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取需要恢复的 graph 任务 (pending/processing/failed 且未超重试)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM extraction_tasks
                   WHERE task_type='graph'
                     AND status IN ('pending','processing','failed')
                     AND retry_count < max_retries
                   ORDER BY scheduled_at
                   LIMIT $1""",
                limit,
            )
        return [dict(r) for r in rows]

    async def claim_pending_graph_tasks(
        self, limit: int = 20, stale_seconds: int = 300
    ) -> list[dict[str, Any]]:
        """A1 常驻 poller 用：原子领取一批待执行 graph task。

        策略：
          - 取 status='pending' 或 (status='processing' 且 scheduled_at 已过 stale_seconds)
          - SKIP LOCKED 防止多 poller 实例抢同一行（未来横向扩展用）
          - 原子置为 'processing' 并刷 scheduled_at 作为 lease 续期

        Returns: 已被本调用领取的 task 列表（dict）。
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH cte AS (
                  SELECT task_id FROM extraction_tasks
                   WHERE task_type='graph'
                     AND retry_count < max_retries
                     AND (
                          status='pending'
                       OR (status='processing'
                           AND scheduled_at < NOW() - ($2 || ' seconds')::interval)
                     )
                   ORDER BY scheduled_at
                   FOR UPDATE SKIP LOCKED
                   LIMIT $1
                )
                UPDATE extraction_tasks t
                   SET status='processing', scheduled_at=NOW()
                  FROM cte
                 WHERE t.task_id = cte.task_id
                RETURNING t.*
                """,
                limit, str(stale_seconds),
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 遗忘子系统（软删除 / 硬删除）
    # ------------------------------------------------------------------

    async def soft_delete(self, memory_ids: list[str]) -> None:
        """Mark memories as deleted (immediately removed from search)."""
        if not memory_ids:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE memory_entries SET is_deleted = TRUE WHERE memory_id = ANY($1::uuid[])",
                memory_ids,
            )

    async def hard_delete_later(self, memory_ids: list[str], delay: int = 3600) -> None:
        """Physically delete memories after `delay` seconds (gives window for undo)."""
        if not memory_ids:
            return
        import asyncio as _asyncio
        await _asyncio.sleep(delay)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memory_entries WHERE memory_id = ANY($1::uuid[]) AND is_deleted = TRUE",
                memory_ids,
            )

    async def get_candidates_for_forget(
        self,
        person_id: str,
        forget_score_threshold: float,
        limit: int = 1000,
    ) -> list[dict]:
        """Return memories with low importance for a person.
        forget_score = importance_score * exp(-lambda * days_since_accessed)
        Calculated in Python after retrieval (no stored column needed).
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT memory_id, memory_type, content, importance_score,
                       last_accessed_at, created_at
                FROM memory_entries
                WHERE person_id = $1
                  AND is_deleted = FALSE
                  AND is_merged = FALSE
                ORDER BY importance_score ASC
                LIMIT $2
                """,
                person_id,
                limit,
            )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_profile(row: dict[str, Any]) -> PersonProfile:
    return PersonProfile(
        person_id=row["person_id"],
        external_id=row["external_id"],
        display_name=row.get("display_name"),
        summary=row.get("summary"),
        summary_updated_at=row.get("summary_updated_at"),
        total_interactions=row.get("total_interactions", 0),
        first_seen_at=row.get("first_seen_at") or datetime.utcnow(),
        last_active_at=row.get("last_active_at") or datetime.utcnow(),
        version=row.get("version", 1),
        created_at=row.get("created_at") or datetime.utcnow(),
    )


def _row_to_memory_dict(row: dict[str, Any]) -> dict[str, Any]:
    if "structured_data" in row and isinstance(row["structured_data"], str):
        row["structured_data"] = json.loads(row["structured_data"])
    if "embedding" in row:
        row.pop("embedding", None)  # 不返回 embedding 给上层
    return row


# ---------------------------------------------------------------------------
# Schema SQL
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS persons (
    person_id       UUID PRIMARY KEY,
    external_id     VARCHAR(255) UNIQUE NOT NULL,
    display_name    VARCHAR(500),
    summary         TEXT,
    summary_updated_at TIMESTAMPTZ,
    total_interactions INTEGER DEFAULT 0,
    first_seen_at   TIMESTAMPTZ DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ DEFAULT NOW(),
    version         INTEGER DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memory_entries (
    memory_id           UUID PRIMARY KEY,
    person_id           UUID NOT NULL REFERENCES persons(person_id),
    session_id          UUID NOT NULL,
    memory_type         VARCHAR(50) NOT NULL,
    content             TEXT NOT NULL,
    structured_data     JSONB NOT NULL DEFAULT '{}',
    importance_score    FLOAT NOT NULL DEFAULT 0.5,
    confidence_score    FLOAT NOT NULL DEFAULT 0.5,
    emotional_valence   FLOAT NOT NULL DEFAULT 0.0,
    emotional_intensity FLOAT NOT NULL DEFAULT 0.0,
    embedding           VECTOR(1024),
    source_message      TEXT DEFAULT '',
    source_turn_index   INTEGER DEFAULT 0,
    extracted_by_model  VARCHAR(100) DEFAULT '',
    is_merged           BOOLEAN DEFAULT FALSE,
    merged_from         UUID[] DEFAULT '{}',
    version             INTEGER DEFAULT 1,
    expires_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    is_deleted          BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_memory_person_type_importance
    ON memory_entries(person_id, memory_type, importance_score DESC)
    WHERE is_merged = FALSE;

CREATE INDEX IF NOT EXISTS idx_memory_person_created
    ON memory_entries(person_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_embedding_hnsw
    ON memory_entries USING hnsw(embedding vector_cosine_ops)
    WITH (m=16, ef_construction=128);

CREATE INDEX IF NOT EXISTS idx_memory_structured_gin
    ON memory_entries USING gin(structured_data);

CREATE INDEX IF NOT EXISTS idx_memory_is_deleted
    ON memory_entries(is_deleted);

CREATE TABLE IF NOT EXISTS memory_merge_log (
    merge_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id   UUID NOT NULL,
    source_ids  UUID[] NOT NULL,
    result_id   UUID NOT NULL,
    merge_reason TEXT DEFAULT '',
    merged_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS extraction_tasks (
    task_id         UUID PRIMARY KEY,
    person_id       UUID NOT NULL,
    session_id      UUID NOT NULL,
    message_content TEXT NOT NULL,
    message_role    VARCHAR(20) NOT NULL,
    turn_index      INTEGER NOT NULL,
    status          VARCHAR(20) DEFAULT 'pending',
    retry_count     INTEGER DEFAULT 0,
    max_retries     INTEGER DEFAULT 3,
    error_message   TEXT,
    scheduled_at    TIMESTAMPTZ DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_extraction_tasks_pending
    ON extraction_tasks(status, scheduled_at)
    WHERE status IN ('pending', 'failed');
"""


# ---------------------------------------------------------------------------
# Person Graph Schema（新增）
# ---------------------------------------------------------------------------

_GRAPH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS person_nodes (
    person_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,
    name            VARCHAR(100) NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'secondary',
    identity        JSONB DEFAULT '{}',
    personality     JSONB DEFAULT '[]',
    preferences     JSONB DEFAULT '[]',
    aversions       JSONB DEFAULT '[]',
    behaviors       JSONB DEFAULT '[]',
    current_focus   JSONB DEFAULT '[]',
    total_events    INTEGER DEFAULT 0,
    last_active_at  TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_person_owner ON person_nodes(owner_id);
CREATE INDEX IF NOT EXISTS idx_person_name ON person_nodes(owner_id, name);

CREATE TABLE IF NOT EXISTS events (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,
    session_id      UUID NOT NULL,
    event_time      TIMESTAMPTZ NOT NULL,
    event_time_raw  VARCHAR(50),
    event_type      VARCHAR(30) NOT NULL,
    action          VARCHAR(50),
    summary         TEXT NOT NULL,
    participant_ids UUID[] NOT NULL DEFAULT '{}',
    participant_names TEXT[] NOT NULL DEFAULT '{}',
    scene           VARCHAR(100),
    emotions        JSONB DEFAULT '{}',
    emotion_summary VARCHAR(50),
    importance      FLOAT NOT NULL DEFAULT 0.5,
    belief_impact   VARCHAR(50),
    impact          TEXT[] DEFAULT '{}',
    caused_by       UUID,
    embedding       VECTOR(1024),
    raw_turns       JSONB DEFAULT '[]',
    source_message  TEXT DEFAULT '',
    extracted_by    VARCHAR(100) DEFAULT '',
    trauma          BOOLEAN DEFAULT FALSE,
    priority        VARCHAR(10),
    is_deleted      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_event_owner_time
    ON events(owner_id, event_time DESC) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS idx_event_participants
    ON events USING GIN(participant_ids) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS idx_event_type
    ON events(owner_id, event_type) WHERE NOT is_deleted;
CREATE INDEX IF NOT EXISTS idx_event_embedding
    ON events USING hnsw(embedding vector_cosine_ops)
    WITH (m=16, ef_construction=128);

CREATE TABLE IF NOT EXISTS relationships (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,
    from_person_id  UUID NOT NULL,
    to_person_id    UUID NOT NULL,
    relation_type   VARCHAR(30),
    sentiment       FLOAT DEFAULT 0.0,
    intensity       FLOAT DEFAULT 0.0,
    last_event_id   UUID,
    last_event_time TIMESTAMPTZ,
    state           JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(from_person_id, to_person_id)
);
CREATE INDEX IF NOT EXISTS idx_rel_owner ON relationships(owner_id);

CREATE TABLE IF NOT EXISTS daily_emotions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,
    person_id       UUID NOT NULL,
    date            DATE NOT NULL,
    emotion_distribution JSONB NOT NULL DEFAULT '{}',
    dominant_emotion VARCHAR(30),
    event_count     INTEGER DEFAULT 0,
    UNIQUE(person_id, date)
);
"""


# ---------------------------------------------------------------------------
# GraphStore — Person Graph CRUD（扩展 PgStore）
# ---------------------------------------------------------------------------

class GraphStore:
    """Person Graph 的数据库操作，与 PgStore 共享连接池。"""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def migrate(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_GRAPH_SCHEMA_SQL)

    # ── PersonNode ────────────────────────────────────────────────────────

    async def upsert_person_node(
        self, owner_id: str, name: str, role: str = "secondary",
        identity: dict | None = None,
    ) -> dict:
        """创建或获取 PersonNode，返回 row dict"""
        async with self._pool.acquire() as conn:
            # 先查是否已存在
            row = await conn.fetchrow(
                "SELECT * FROM person_nodes WHERE owner_id = $1 AND name = $2",
                owner_id, name[:100],
            )
            if row:
                return dict(row)
            # 新建
            row = await conn.fetchrow(
                """INSERT INTO person_nodes (owner_id, name, role, identity)
                   VALUES ($1, $2, $3, $4::jsonb)
                   RETURNING *""",
                owner_id, name[:100], role[:20],
                json.dumps(identity or {}, ensure_ascii=False),
            )
            return dict(row)

    async def get_person_node(self, person_id: str) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM person_nodes WHERE person_id = $1", person_id,
            )
        return dict(row) if row else None

    async def get_person_by_name(self, owner_id: str, name: str) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM person_nodes WHERE owner_id = $1 AND name = $2",
                owner_id, name,
            )
        return dict(row) if row else None

    async def rename_person(self, person_id: str, new_name: str) -> None:
        """更新 person_nodes.name（用户自报真实姓名时调用）。"""
        import uuid as _uuid
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE person_nodes SET name = $1, updated_at = NOW() WHERE person_id = $2",
                new_name[:100], _uuid.UUID(str(person_id)),
            )

    async def get_primary_person(self, owner_id: str) -> dict | None:
        """查找 owner 下的 primary 节点（用于改名后仍能找回同一个人）。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM person_nodes WHERE owner_id = $1 AND role = 'primary' ORDER BY created_at LIMIT 1",
                owner_id,
            )
        return dict(row) if row else None

    async def list_person_nodes(self, owner_id: str) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM person_nodes WHERE owner_id = $1 ORDER BY total_events DESC",
                owner_id,
            )
        return [dict(r) for r in rows]

    async def update_person_field(
        self, person_id: str, field: str, value: Any
    ) -> None:
        """更新 PersonNode 的一个 JSONB 字段（identity/preferences/...）"""
        allowed = {"identity", "personality", "preferences", "aversions",
                   "behaviors", "current_focus"}
        if field not in allowed:
            raise ValueError(f"field {field!r} not allowed")
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE person_nodes SET {field} = $1::jsonb, last_active_at = NOW() "
                f"WHERE person_id = $2",
                json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value,
                person_id,
            )

    async def increment_events(self, person_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE person_nodes SET total_events = total_events + 1, "
                "last_active_at = NOW() WHERE person_id = $1",
                person_id,
            )

    async def update_relationship_field(
        self, owner_id: str, from_person_id: str, to_person_id: str,
        field: str, value: Any
    ) -> None:
        """更新关系的单个字段"""
        allowed = {"state", "sentiment", "relation_type"}
        if field not in allowed:
            raise ValueError(f"field {field!r} not allowed")
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE relationships SET {field} = $1, updated_at = NOW() "
                f"WHERE owner_id = $2 AND from_person_id = $3 AND to_person_id = $4",
                value, owner_id, from_person_id, to_person_id,
            )

    # ── Event ─────────────────────────────────────────────────────────────

    async def update_event_field(self, event_id, field: str, value: Any) -> None:
        """更新事件的单个字段（allowlist 限定；供创伤标记等使用）。"""
        allowed = {"trauma", "priority", "importance", "belief_impact"}
        if field not in allowed:
            raise ValueError(f"field {field!r} not allowed")
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE events SET {field} = $1, updated_at = NOW() WHERE event_id = $2",
                value, event_id,
            )

    async def insert_event(self, event: dict) -> None:
        """写入一个事件"""
        embedding_str = (
            "[" + ",".join(str(x) for x in event["embedding"]) + "]"
            if event.get("embedding") else None
        )
        # session_id 兼容：非 UUID 格式用 uuid5 转换
        import uuid as _uuid
        _sid = str(event.get("session_id") or "")
        try:
            _uuid.UUID(_sid)
        except (ValueError, AttributeError):
            _sid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"session.{_sid}"))
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO events (
                    event_id, owner_id, session_id,
                    event_time, event_time_raw, event_type, action, summary, title,
                    participant_ids, participant_names, scene,
                    emotions, emotion_summary, importance, belief_impact, impact,
                    caused_by, embedding, raw_turns, source_message, extracted_by
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22
                ) ON CONFLICT (event_id) DO NOTHING""",
                str(event["event_id"]), str(event["owner_id"]), _sid,
                event["event_time"], str(event.get("event_time_raw") or "")[:50] or None,
                str(event["event_type"] or "daily")[:30],
                str(event.get("action") or "")[:50] or None,
                event["summary"], str(event.get("title") or "")[:30],
                [str(p) for p in event.get("participant_ids", [])],
                event.get("participant_names", []),
                str(event.get("scene") or "")[:100] or None,
                json.dumps(event.get("emotions", {}), ensure_ascii=False),
                str(event.get("emotion_summary") or "")[:50] or None,
                event.get("importance", 0.5),
                str(event.get("belief_impact") or "")[:50] or None,
                event.get("impact", []),
                str(event["caused_by"]) if event.get("caused_by") else None,
                embedding_str,
                json.dumps(event.get("raw_turns", []), ensure_ascii=False),
                event.get("source_message", ""),
                str(event.get("extracted_by") or "")[:100],
            )

    async def query_events(
        self, owner_id: str, *,
        participant_id: str | None = None,
        event_type: str | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """多维事件检索"""
        conditions = ["owner_id = $1", "NOT is_deleted"]
        params: list[Any] = [owner_id]
        idx = 2

        if participant_id:
            conditions.append(f"participant_ids @> ARRAY[${idx}::uuid]")
            params.append(participant_id)
            idx += 1
        if event_type:
            conditions.append(f"event_type = ${idx}")
            params.append(event_type)
            idx += 1
        if time_from:
            conditions.append(f"event_time >= ${idx}")
            params.append(time_from)
            idx += 1
        if time_to:
            conditions.append(f"event_time <= ${idx}")
            params.append(time_to)
            idx += 1

        where = " AND ".join(conditions)
        params.append(limit)

        sql = f"""
        SELECT event_id, owner_id, session_id, event_time, event_time_raw,
               event_type, summary, participant_ids, participant_names, scene,
               emotions, emotion_summary, importance, belief_impact, impact,
               caused_by, raw_turns, source_message, created_at
        FROM events
        WHERE {where}
        ORDER BY event_time DESC
        LIMIT ${idx}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("emotions"), str):
                d["emotions"] = json.loads(d["emotions"])
            if isinstance(d.get("raw_turns"), str):
                d["raw_turns"] = json.loads(d["raw_turns"])
            result.append(d)
        return result

    async def vector_search_events(
        self, owner_id: str, query_embedding: list[float],
        limit: int = 20,
    ) -> list[dict]:
        """事件向量搜索 - 按相似度排序"""
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        sql = """
        SELECT event_id, owner_id, event_time, event_type, summary,
               source_message, raw_turns,
               participant_names, scene, emotion_summary, importance,
               1 - (embedding <=> $2::vector) AS similarity
        FROM events
        WHERE owner_id = $1 AND NOT is_deleted AND embedding IS NOT NULL
        ORDER BY similarity DESC
        LIMIT $3
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, owner_id, embedding_str, limit)
        return [dict(r) for r in rows]

    async def list_events(self, owner_id: str, limit: int = 50) -> list[dict]:
        return await self.query_events(owner_id, limit=limit)

    async def soft_delete_event(self, event_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE events SET is_deleted = TRUE WHERE event_id = $1",
                event_id,
            )

    # ── Relationship ──────────────────────────────────────────────────────

    async def upsert_relationship(
        self, owner_id: str,
        from_person_id: str, to_person_id: str,
        relation_type: str = "other",
        sentiment_delta: float = 0.0,
        event_id: str | None = None,
        event_time: datetime | None = None,
        last_emotion: str | None = None,
        last_summary: str | None = None,
    ) -> dict:
        """创建或更新关系，返回最新状态

        T2 修复:
          - sentiment 改 MAX_ABS 策略: 仅在 |new| > |old| 时覆盖,
            避免激烈情绪被反复 *0.9 稀释到 0
          - state 字段写入 last_emotion/last_summary/updated_at,
            供"上次跟阿明闹矛盾那事"等召回使用
        """
        # state JSONB: 仅在传入了 last_emotion 或 last_summary 时更新
        state_payload = None
        if last_emotion is not None or last_summary is not None:
            import json as _json
            state_payload = _json.dumps({
                "last_emotion": last_emotion,
                "last_summary": (last_summary or "")[:500],
                "updated_at": (event_time.isoformat() if event_time else None),
            }, ensure_ascii=False)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO relationships
                   (owner_id, from_person_id, to_person_id, relation_type,
                    sentiment, intensity, last_event_id, last_event_time, state)
                   VALUES ($1, $2, $3, $4, $5, 1.0, $6, $7,
                           COALESCE($8::jsonb, '{}'::jsonb))
                   ON CONFLICT (from_person_id, to_person_id)
                   DO UPDATE SET
                     sentiment = CASE
                       WHEN ABS($5) > ABS(relationships.sentiment)
                         THEN GREATEST(-1.0, LEAST(1.0, $5))
                       ELSE relationships.sentiment
                     END,
                     intensity = LEAST(1.0, relationships.intensity + 0.05),
                     -- 不降级：明确类型(friend/family…)绝不被后续的 'other' 覆盖回去
                     relation_type = CASE
                       WHEN EXCLUDED.relation_type IS NOT NULL AND EXCLUDED.relation_type <> 'other'
                         THEN EXCLUDED.relation_type
                       WHEN relationships.relation_type IS NOT NULL AND relationships.relation_type <> 'other'
                         THEN relationships.relation_type
                       ELSE COALESCE(EXCLUDED.relation_type, relationships.relation_type)
                     END,
                     last_event_id = COALESCE($6, relationships.last_event_id),
                     last_event_time = COALESCE($7, relationships.last_event_time),
                     state = CASE
                       WHEN $8::jsonb IS NOT NULL THEN $8::jsonb
                       ELSE relationships.state
                     END,
                     updated_at = NOW()
                   RETURNING *""",
                owner_id, from_person_id, to_person_id, (relation_type or "other")[:30],
                sentiment_delta,
                event_id, event_time, state_payload,
            )
        return dict(row)

    async def get_relationships(self, owner_id: str) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT r.*, p1.name as from_name, p2.name as to_name
                   FROM relationships r
                   JOIN person_nodes p1 ON r.from_person_id = p1.person_id
                   JOIN person_nodes p2 ON r.to_person_id = p2.person_id
                   WHERE r.owner_id = $1
                   ORDER BY r.intensity DESC""",
                owner_id,
            )
        return [dict(r) for r in rows]

    async def get_relationship(
        self, owner_id: str, from_person_id: str, to_person_id: str
    ) -> dict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM relationships WHERE owner_id = $1 AND from_person_id = $2 AND to_person_id = $3",
                owner_id, from_person_id, to_person_id,
            )
        return dict(row) if row else None

    # ── DailyEmotion ──────────────────────────────────────────────────────

    async def upsert_daily_emotion(
        self, owner_id: str, person_id: str,
        date_val: Any, emotions: dict[str, float],
        dominant: str, event_count: int = 1,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO daily_emotions
                   (owner_id, person_id, date, emotion_distribution,
                    dominant_emotion, event_count)
                   VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                   ON CONFLICT (person_id, date)
                   DO UPDATE SET
                     emotion_distribution = $4::jsonb,
                     dominant_emotion = $5,
                     event_count = daily_emotions.event_count + $6""",
                owner_id, person_id, date_val,
                json.dumps(emotions, ensure_ascii=False),
                dominant[:30] if dominant else "", event_count,
            )

    async def get_recent_emotions(
        self, person_id: str, days: int = 7
    ) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM daily_emotions
                   WHERE person_id = $1 AND date >= CURRENT_DATE - $2::int
                   ORDER BY date DESC""",
                person_id, days,
            )
        return [dict(r) for r in rows]

    async def get_events_by_date(
        self, owner_id: str, person_id: str, date_obj,
        action: str = None, participant_ids: list = None
    ) -> list[dict]:
        """查询某天的 events，可选按 action 过滤"""
        async with self._pool.acquire() as conn:
            query = """
                SELECT * FROM events
                WHERE owner_id = $1 AND $2 = ANY(participant_ids)
                  AND DATE(event_time) = $3
            """
            params = [owner_id, person_id, date_obj]

            if action:
                query += " AND action = $4"
                params.append(action)

            rows = await conn.fetch(query + " ORDER BY event_time DESC", *params)
            return [dict(row) for row in rows]

    async def get_recent_events(
        self, owner_id: str, person_id: str, days: int = 7
    ) -> list[dict]:
        """查询最近 N 天的 events（用于合并判断）"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM events
                WHERE owner_id = $1 AND $2 = ANY(participant_ids)
                  AND event_time >= NOW() - ($3 || ' days')::interval
                ORDER BY event_time DESC
                LIMIT 20
                """,
                owner_id, person_id, str(days),
            )
            return [dict(row) for row in rows]

    async def update_event_summary(self, event_id, new_summary: str, embedding: list = None):
        """更新 event 的 summary 和 embedding"""
        async with self._pool.acquire() as conn:
            if embedding:
                embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
                await conn.execute(
                    "UPDATE events SET summary = $1, embedding = $2, updated_at = NOW() WHERE event_id = $3",
                    new_summary, embedding_str, event_id
                )
            else:
                await conn.execute(
                    "UPDATE events SET summary = $1, updated_at = NOW() WHERE event_id = $2",
                    new_summary, event_id
                )

    async def update_event_title(self, event_id, title: str):
        """更新 event 的短标题"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE events SET title = $1, updated_at = NOW() WHERE event_id = $2",
                title[:30], event_id
            )
