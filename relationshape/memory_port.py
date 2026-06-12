"""MemoryPort：用户记忆后端的可替换座椅（融合 memory_system 的桥）。

分工（docs/MEMORY_SYSTEM_REVIEW.md 的决策）：
- 引擎侧永久保留：承诺生命周期、信任/阶段账本、角色自述账本、敏感封存
- 可外置：用户情景/语义记忆 → memory_system（人物图谱+向量检索）

设计原则（与感知层换装座椅同源）：
- 默认零依赖：不配 port 时行为与纯引擎完全一致
- fail-open：远端超时/异常 → 静默回退本地召回，绝不阻塞对话
- 隐私边界在引擎：危机轮永不出引擎（线缆级测试保证）；
  只转发用户生活类轮次（钩子防污染同款过滤）
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional, Protocol

from relationshape.types import MemoryRecall


class MemoryPort(Protocol):
    """用户记忆后端协议。实现者：MemorySystemAdapter（HTTP）、测试 mock。"""

    def recall(
        self, user_id: str, query: str, now: datetime, want_profile: bool = False,
    ) -> tuple[Optional[str], list[MemoryRecall]]:
        """返回 (人物档案摘要|None, 长期记忆召回列表)。失败时 (None, [])。"""
        ...

    def observe(
        self, user_id: str, user_text: str, assistant_text: str,
        session_id: str, now: datetime,
    ) -> None:
        """喂入一轮对话供异步抽取。必须吞掉一切异常。"""
        ...


class MemorySystemAdapter:
    """对接 memory_system 服务（/api/v1/graph/recall + /api/v1/memory/chat）。

    集成注记：recall 按 owner_id 解析 primary person（服务端 P0-A 已有该逻辑，
    需在 /graph/recall 同样支持——见融合规格 Phase-2 清单）。
    """

    def __init__(self, base_url: str, timeout: float = 0.4, user_name: str = "用户") -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_name = user_name

    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict, timeout: Optional[float] = None) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            return json.loads(resp.read().decode())

    @staticmethod
    def _days_ago(event_time: Optional[str], now: datetime) -> int:
        if not event_time:
            return 0
        try:
            t = datetime.fromisoformat(event_time.replace("Z", "+00:00")).replace(tzinfo=None)
            return max(0, (now - t).days)
        except ValueError:
            return 0

    # ------------------------------------------------------------------

    def recall(
        self, user_id: str, query: str, now: datetime, want_profile: bool = False,
    ) -> tuple[Optional[str], list[MemoryRecall]]:
        try:
            data = self._post("/api/v1/graph/recall", {
                "owner_id": user_id,
                "user_name": self.user_name,
                "query_text": query,
                "include_profile": want_profile,
            })
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None, []                      # fail-open：回退本地
        recalls: list[MemoryRecall] = []
        for e in (data.get("events") or [])[:6]:
            days = self._days_ago(e.get("event_time"), now)
            when = e.get("event_time_raw") or (f"{days}天前" if days else "最近")
            recalls.append(MemoryRecall(
                text=str(e.get("summary", ""))[:80],
                kind="remote",
                score=float(e.get("weight", e.get("importance", 0.5)) or 0.5),
                days_ago=days,
                hint=f"长期记忆（{when}）——相关就自然带一句，注意先后次序，不硬塞",
            ))
        profile = data.get("profile_summary") if want_profile else None
        return (profile or None), recalls

    def observe(
        self, user_id: str, user_text: str, assistant_text: str,
        session_id: str, now: datetime,
    ) -> None:
        try:
            self._post("/api/v1/memory/chat", {
                "owner_id": user_id,
                "user_name": self.user_name,
                "user_message": user_text,
                "assistant_message": assistant_text,
                "session_id": session_id,
            }, timeout=max(self.timeout, 0.8))
        except Exception:
            pass                                  # 喂入尽力而为，绝不影响对话
