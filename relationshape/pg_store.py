"""PostgresStateStore：每用户关系状态落 PostgreSQL（复用 memory_system 的 PG 实例）。

为什么整存 JSONB 而不拆多表：UserRelationState 是一个自洽的状态文档（to_dict），含嵌套的
情景记忆/信任账本/心境/适应层，且随版本演进。整存 JSONB → 事务/并发安全、可按 JSON 路径查询、
schema 演进零迁移；人物图谱/向量那套规范化由 memory_system 自己负责，引擎内部状态作为一列文档最稳。

同步 psycopg3（引擎是同步的，改 async 会逼所有调用方重写）。连接池可注入以复用。
与 StateStore(JSON) 同接口：load(user_id) / save(state)，可直接替换。
"""

from __future__ import annotations

import re
from typing import Optional

from relationshape.state import UserRelationState

# 表名只允许标识符字符——下面用 f-string 拼进带引号的 SQL，校验后无注入面
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ddl(table: str = "relationship_state") -> str:
    """建表 DDL（迁移脚本与自动建表共用同一份）。"""
    if not _IDENT_RE.match(table):
        raise ValueError(f"非法表名: {table!r}")
    return (
        f'CREATE TABLE IF NOT EXISTS "{table}" ('
        " user_id TEXT PRIMARY KEY,"
        " data JSONB NOT NULL,"
        " updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ");"
    )


class PostgresStateStore:
    def __init__(
        self,
        dsn: str,
        table: str = "relationship_state",
        *,
        pool=None,
        autocreate: bool = True,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        if not _IDENT_RE.match(table):
            raise ValueError(f"非法表名: {table!r}")
        self.table = table
        try:
            from psycopg.types.json import Jsonb
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - 仅缺依赖时
            raise ImportError(
                "PostgresStateStore 需要 psycopg[binary] 与 psycopg_pool："
                "pip install 'psycopg[binary]' psycopg_pool"
            ) from exc
        self._Jsonb = Jsonb
        self._owns_pool = pool is None
        self._pool = pool or ConnectionPool(
            dsn, min_size=min_size, max_size=max_size,
            kwargs={"autocommit": True}, open=True,
        )
        if autocreate:
            with self._pool.connection() as conn:
                conn.execute(ddl(self.table))

    def load(self, user_id: str) -> UserRelationState:
        with self._pool.connection() as conn:
            row = conn.execute(
                f'SELECT data FROM "{self.table}" WHERE user_id = %s', (user_id,)
            ).fetchone()
        if not row:
            return UserRelationState(user_id=user_id)
        return UserRelationState.from_dict(row[0])   # jsonb → dict 由 psycopg3 自动完成

    def save(self, state: UserRelationState) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f'INSERT INTO "{self.table}" (user_id, data) VALUES (%s, %s) '
                f"ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
                (state.user_id, self._Jsonb(state.to_dict())),
            )

    def delete(self, user_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(f'DELETE FROM "{self.table}" WHERE user_id = %s', (user_id,))

    def close(self) -> None:
        if self._owns_pool:
            self._pool.close()
