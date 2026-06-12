"""
models/meaning.py — Phase 3: 意义层 + 偏好轨迹

MeaningLayer: "这件事对我意味着什么"
PreferenceState / PreferenceTimeline: 偏好随时间的变化轨迹
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# MeaningLayer — 意义层
# ---------------------------------------------------------------------------

@dataclass
class MeaningLayer:
    """
    对一段文字/事件的意义解读。

    emotional_impact  主情绪（最显著的一个）
    emotional_tags    细粒度情绪列表（可以多个）
    belief_update     对信念/信任的影响描述（如"信任下降"）
    belief_type       标准化信念变化类型
    importance        0.0-1.0 重要性评分
    meaning_summary   一句话意义总结
    negated           是否检测到否定（"我不难过"类情况）
    """
    emotional_impact: str                           # 主情绪
    emotional_tags:   list[str] = field(default_factory=list)
    belief_update:    Optional[str] = None          # "信任下降" / "自信提升" etc.
    belief_type:      Optional[str] = None          # 标准化类型
    importance:       float = 0.5
    meaning_summary:  str = ""
    negated:          bool = False                  # 是否存在否定修饰

    # 标准 belief_type 枚举
    BELIEF_TYPES = {
        "trust_decrease",     # 信任降低
        "trust_increase",     # 信任增强
        "self_confidence_up", # 自信提升
        "self_blame",         # 自责
        "loneliness",         # 孤独感
        "belonging",          # 归属感
        "world_unsafe",       # 世界不安全
        "world_fair",         # 世界公平
        "loss",               # 失去感
        "achievement",        # 成就感
        "betrayal",           # 被背叛
        "empathy",            # 共情
        "regret",             # 遗憾
        "none",               # 无明显信念变化
    }

    def to_dict(self) -> dict:
        return {
            "emotional_impact": self.emotional_impact,
            "emotional_tags":   self.emotional_tags,
            "belief_update":    self.belief_update,
            "belief_type":      self.belief_type,
            "importance":       self.importance,
            "meaning_summary":  self.meaning_summary,
            "negated":          self.negated,
        }


# ---------------------------------------------------------------------------
# PreferenceState — 单次偏好快照
# ---------------------------------------------------------------------------

Sentiment = Literal["like", "dislike", "neutral"]

@dataclass
class PreferenceState:
    """某一时刻对某项事物的偏好快照"""
    item:        str                              # 偏好对象（如"草莓"/"画画"）
    category:    str                              # 类别（"food"/"hobby"/"subject"）
    sentiment:   Sentiment                        # like / dislike / neutral
    strength:    float = 0.7                      # 0.0-1.0 强度
    timestamp:   datetime = field(default_factory=datetime.utcnow)
    source_text: str = ""                         # 来源文本
    conditional: Optional[str] = None            # 条件（如"生气的时候"）

    def to_dict(self) -> dict:
        return {
            "item":        self.item,
            "category":    self.category,
            "sentiment":   self.sentiment,
            "strength":    self.strength,
            "timestamp":   self.timestamp.isoformat(),
            "source_text": self.source_text,
            "conditional": self.conditional,
        }


# ---------------------------------------------------------------------------
# PreferenceTimeline — 偏好变化轨迹
# ---------------------------------------------------------------------------

@dataclass
class PreferenceTimeline:
    """
    追踪单个 (item, category) 的偏好变化历史。

    核心约束：
      - 新状态总是覆盖旧状态（新状态优先）
      - 相同 sentiment + 相近 strength 不重复记录（幂等）
      - 条件偏好（conditional）不覆盖无条件偏好
    """
    item:     str
    category: str
    history:  list[PreferenceState] = field(default_factory=list)

    # ── 核心属性 ──────────────────────────────────────────────────────────────

    def current_state(self) -> Optional[PreferenceState]:
        """返回最新（非条件性）偏好状态"""
        for s in reversed(self.history):
            if s.conditional is None:
                return s
        return self.history[-1] if self.history else None

    def current_sentiment(self) -> Optional[Sentiment]:
        s = self.current_state()
        return s.sentiment if s else None

    def trajectory(self) -> list[str]:
        """返回情感轨迹，如 ['like', 'dislike', 'like']"""
        return [s.sentiment for s in self.history]

    # ── 更新 ──────────────────────────────────────────────────────────────────

    def update(self, new_state: PreferenceState) -> bool:
        """
        添加新偏好状态。

        Returns:
            True  = 状态发生了变化（sentiment 或 strength 变化明显）
            False = 状态未变（幂等，忽略）
        """
        current = self.current_state()

        # 条件偏好不覆盖无条件偏好，直接追加
        if new_state.conditional is not None:
            self.history.append(new_state)
            return True  # 条件偏好始终记录

        # 第一条状态
        if current is None:
            self.history.append(new_state)
            return True

        # Sentiment 变化 → 覆盖
        if new_state.sentiment != current.sentiment:
            self.history.append(new_state)
            return True

        # 相同 sentiment 但 strength 变化超过 0.15 → 记录强度变化
        if abs(new_state.strength - current.strength) >= 0.15:
            self.history.append(new_state)
            return True

        # 无实质变化
        return False

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def changed_from(self, old_sentiment: Sentiment) -> bool:
        """当前状态是否已从 old_sentiment 改变"""
        cur = self.current_sentiment()
        return cur is not None and cur != old_sentiment

    def to_dict(self) -> dict:
        return {
            "item":     self.item,
            "category": self.category,
            "history":  [s.to_dict() for s in self.history],
            "current":  self.current_state().to_dict() if self.current_state() else None,
            "trajectory": self.trajectory(),
        }


# ---------------------------------------------------------------------------
# PreferenceManager — 管理多个 item 的偏好轨迹
# ---------------------------------------------------------------------------

class PreferenceManager:
    """管理一个人的所有偏好轨迹"""

    def __init__(self) -> None:
        # (item, category) → PreferenceTimeline
        self._timelines: dict[tuple[str, str], PreferenceTimeline] = {}

    def _key(self, item: str, category: str) -> tuple[str, str]:
        return (item.strip(), category.strip())

    def update(self, state: PreferenceState) -> bool:
        """更新偏好，返回是否发生了变化"""
        key = self._key(state.item, state.category)
        if key not in self._timelines:
            self._timelines[key] = PreferenceTimeline(
                item=state.item, category=state.category
            )
        return self._timelines[key].update(state)

    def get_timeline(self, item: str, category: str) -> Optional[PreferenceTimeline]:
        return self._timelines.get(self._key(item, category))

    def current_sentiment(self, item: str, category: str) -> Optional[Sentiment]:
        tl = self.get_timeline(item, category)
        return tl.current_sentiment() if tl else None

    def all_items(self) -> list[tuple[str, str]]:
        return list(self._timelines.keys())
