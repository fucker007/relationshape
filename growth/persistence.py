"""持久化：每个孩子一个 JSON 文件，原子写入（临时文件 + os.replace），零依赖。

照搬 relationshape/persistence.py 的稳妥写法。load 不存在的孩子返回 None——
孩子需要显式创建（要带名字/年龄/年级），不像关系状态可以凭空 fresh 出来。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Optional

from growth.state import ChildState

_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-一-鿿]")


def _safe_name(child_id: str) -> str:
    return _SAFE_RE.sub("_", child_id)[:64] or "default"


class ChildStore:
    def __init__(self, state_dir: str = "runtime/growth") -> None:
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)

    def _path(self, child_id: str) -> str:
        return os.path.join(self.state_dir, f"{_safe_name(child_id)}.json")

    def exists(self, child_id: str) -> bool:
        return os.path.exists(self._path(child_id))

    def load(self, child_id: str) -> Optional[ChildState]:
        path = self._path(child_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return ChildState.from_dict(json.load(f))

    def save(self, child: ChildState) -> None:
        path = self._path(child.child_id)
        data = json.dumps(child.to_dict(), ensure_ascii=False, indent=1)
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def list_ids(self) -> list[str]:
        out = []
        for fn in sorted(os.listdir(self.state_dir)):
            if fn.endswith(".json"):
                out.append(fn[:-5])
        return out
