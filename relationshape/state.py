"""每用户一份的关系状态聚合。

关键设计：所有状态按 user_id 隔离——哥哥晚上骂了它一句，
不能让它第二天带着委屈跟妹妹说话。心境、信任、记忆、适应
都是"这段关系"的属性，不是全局属性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from relationshape.adaptation import AdaptationState
from relationshape.affect import MoodState
from relationshape.memory import MemoryBank
from relationshape.relationship import Ledger, RelationshipCore


@dataclass
class UserRelationState:
    user_id: str
    core: RelationshipCore = field(default_factory=RelationshipCore)
    ledger: Ledger = field(default_factory=Ledger)
    mood: MoodState = field(default_factory=MoodState)
    memory: MemoryBank = field(default_factory=MemoryBank)
    adaptation: AdaptationState = field(default_factory=AdaptationState)

    turn_index: int = 0
    last_humor_turn: int = -99
    last_reward_turn: int = -99
    session_reward_count: int = 0
    recent_reward_keys: list[str] = field(default_factory=list)
    last_hook: Optional[str] = None
    last_user_valence: float = 0.0   # 上一轮用户情绪效价：幽默的情绪惯性门禁用
    traces: list = field(default_factory=list)   # 每轮记忆调用痕迹（最近20轮，可观测性）

    # 本轮 prepare 的中间产物，等 commit 消费；不持久化
    pending: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "core": self.core.to_dict(),
            "ledger": self.ledger.to_dict(),
            "mood": self.mood.to_dict(),
            "memory": self.memory.to_dict(),
            "adaptation": self.adaptation.to_dict(),
            "turn_index": self.turn_index,
            "last_humor_turn": self.last_humor_turn,
            "last_reward_turn": self.last_reward_turn,
            "session_reward_count": self.session_reward_count,
            "recent_reward_keys": self.recent_reward_keys,
            "last_hook": self.last_hook,
            "last_user_valence": self.last_user_valence,
            "traces": self.traces,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UserRelationState":
        return cls(
            user_id=d["user_id"],
            core=RelationshipCore.from_dict(d.get("core", {})),
            ledger=Ledger.from_dict(d.get("ledger", {})),
            mood=MoodState.from_dict(d.get("mood", {})),
            memory=MemoryBank.from_dict(d.get("memory", {})),
            adaptation=AdaptationState.from_dict(d.get("adaptation", {})),
            turn_index=d.get("turn_index", 0),
            last_humor_turn=d.get("last_humor_turn", -99),
            last_reward_turn=d.get("last_reward_turn", -99),
            session_reward_count=d.get("session_reward_count", 0),
            recent_reward_keys=d.get("recent_reward_keys", []),
            last_hook=d.get("last_hook"),
            last_user_valence=d.get("last_user_valence", 0.0),
            traces=d.get("traces", []),
        )
