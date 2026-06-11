"""持久化：每用户一个 JSON 文件，原子写入。

原子写（临时文件 + os.replace）保证进程被杀时状态文件不会写坏一半。
"""

from __future__ import annotations

import json
import os
import re
import tempfile

from relationshape.state import UserRelationState

_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-一-鿿]")


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
