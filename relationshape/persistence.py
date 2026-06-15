"""持久化：可插拔后端。

- StateStore：每用户一个 JSON 文件，原子写入（临时文件 + os.replace），零依赖、默认。
- PostgresStateStore（pg_store.py）：复用 memory_system 的 PG 实例。
两者同接口 load(user_id)/save(state)，由 build_store(config) 按 state_backend 选择。
"""

from __future__ import annotations

import json
import os
import re
import tempfile

from relationshape.state import UserRelationState

_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-一-鿿]")


def build_store(config):
    """按 EngineConfig.state_backend 造存储后端。json（默认）｜postgres（复用 memory_system PG）。"""
    backend = (getattr(config, "state_backend", "json") or "json").lower()
    if backend in ("pg", "postgres", "postgresql"):
        from relationshape.pg_store import PostgresStateStore
        dsn = (getattr(config, "state_dsn", "") or os.environ.get("RELATIONSHAPE_PG_DSN")
               or os.environ.get("MEMORY_PG_DSN"))
        if not dsn:
            raise ValueError(
                "state_backend=postgres 需要 state_dsn，或环境变量 RELATIONSHAPE_PG_DSN / MEMORY_PG_DSN"
            )
        return PostgresStateStore(dsn, table=getattr(config, "state_table", "relationship_state"))
    return StateStore(config.state_dir)


def _safe_name(user_id: str) -> str:
    return _SAFE_RE.sub("_", user_id)[:64] or "default"


class StateStore:
    def __init__(self, state_dir: str) -> None:
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)

    def _path(self, user_id: str) -> str:
        return os.path.join(self.state_dir, f"{_safe_name(user_id)}.json")

    def load(self, user_id: str) -> UserRelationState:
        path = self._path(user_id)
        if not os.path.exists(path):
            return UserRelationState(user_id=user_id)
        with open(path, encoding="utf-8") as f:
            return UserRelationState.from_dict(json.load(f))

    def save(self, state: UserRelationState) -> None:
        path = self._path(state.user_id)
        data = json.dumps(state.to_dict(), ensure_ascii=False, indent=1)
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
