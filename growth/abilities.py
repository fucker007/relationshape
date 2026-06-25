"""能力评估：五维能力的诚实度量。

诚实原则（呼应我们"测量诚实"的讨论——别把噪声画成精确的能力增长）：
- 客观能力（逻辑 / 观察 / 专注）：用"在某难度上的正确表现"驱动等级；答错不重罚
  （孩子状态波动大），只是涨得慢。
- 主观能力（表达 / 创造）：没有对错，等级度量的是"练习量 / 活跃度"，靠认真参与缓慢累积。
  家长报告会如实标注这是"练习"而非"打分"。

等级是 0..100 的平滑量；每天给每个能力采一个样进 history，供报告画趋势。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from growth.types import Ability


@dataclass
class AbilityTrack:
    level: float = 12.0
    practiced: int = 0
    objective_seen: int = 0
    objective_correct: int = 0
    history: list = field(default_factory=list)   # [[day_iso, level_int], ...]

    def to_dict(self) -> dict:
        return {
            "level": round(self.level, 2),
            "practiced": self.practiced,
            "objective_seen": self.objective_seen,
            "objective_correct": self.objective_correct,
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AbilityTrack":
        return cls(
            level=d.get("level", 12.0),
            practiced=d.get("practiced", 0),
            objective_seen=d.get("objective_seen", 0),
            objective_correct=d.get("objective_correct", 0),
            history=d.get("history", []),
        )


@dataclass
class AbilityState:
    tracks: dict   # ability.value -> AbilityTrack

    @classmethod
    def fresh(cls) -> "AbilityState":
        return cls(tracks={a.value: AbilityTrack() for a in Ability})

    def track(self, ability: Ability) -> AbilityTrack:
        return self.tracks.setdefault(ability.value, AbilityTrack())

    def register(
        self, ability: Ability, correct: Optional[bool], credit: float, difficulty: int,
    ) -> tuple[float, float]:
        """登记一次作答对能力的影响。返回 (旧等级, 新等级)。"""
        t = self.track(ability)
        old = t.level
        t.practiced += 1

        if correct is None:
            # 表达/创造：等级=练习活跃度，认真参与才涨，越高越慢（边际递减）
            target = min(95.0, 20.0 + t.practiced * 2.6 + difficulty * 4.0)
            if target > t.level:
                t.level += (target - t.level) * 0.25 * max(0.3, credit)
        else:
            t.objective_seen += 1
            if correct:
                t.objective_correct += 1
                target = min(100.0, 18.0 + difficulty * 24.0)
                gain = (target - t.level) * (0.16 if target > t.level else 0.04)
                t.level += max(0.0, gain)
            else:
                # 答错：不重罚，只给一点点"练习到了"的微涨（仍鼓励再来）
                t.level += 0.4 * credit

        t.level = max(0.0, min(100.0, t.level))
        return old, t.level

    def sample_day(self, day_iso: str) -> None:
        """每天给每个能力采一个样（同一天只采一次），作为报告趋势线的采样点。"""
        for a in Ability:
            t = self.track(a)
            if t.history and t.history[-1][0] == day_iso:
                t.history[-1][1] = round(t.level)
            else:
                t.history.append([day_iso, round(t.level)])
                t.history = t.history[-120:]

    def mean_level(self) -> float:
        ts = [self.track(a).level for a in Ability]
        return sum(ts) / len(ts) if ts else 0.0

    def strongest(self) -> Ability:
        return max(Ability, key=lambda a: self.track(a).level)

    def to_dict(self) -> dict:
        return {k: v.to_dict() for k, v in self.tracks.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "AbilityState":
        base = cls.fresh()
        for k, v in (d or {}).items():
            base.tracks[k] = AbilityTrack.from_dict(v)
        return base
