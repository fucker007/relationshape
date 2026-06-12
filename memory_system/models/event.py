"""
models/event.py — 事件模型

Phase 2 原始 EventModel 保留（pipeline/event_extractor.py 依赖它）。
新增 Event 模型在 models/person_graph.py 中定义（Person Graph 架构核心）。

EventModel: 规则提取器的中间产物（who/when/where/what）
Event:      最终持久化的结构化事件（person_graph.py 中定义）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EventModel:
    """
    Phase 2 事件模型（规则提取器输出）。

    这是 pipeline/event_extractor.py 的中间产物，
    最终会被转换为 models.person_graph.Event 写入数据库。

    who          主体（人物名，如"小明"；若为用户本人则记"用户"）
    when         时间（标准化字符串，如"2026-03-01"或"过去"）
    where        地点（如"公园"；无法提取时为 None）
    what         事件描述（一句话核心动作，如"和明明一起玩"）
    participants 其他参与者列表（除 who 之外的人物名）
    emotion      情绪标签（如"开心"/"难过"/"平静"；无法提取时为 None）
    when_raw     原始时间表达（如"昨天"/"上周五"）
    when_normalized 是否完成了时间标准化
    """
    who: str
    what: str
    when: Optional[str] = None
    where: Optional[str] = None
    participants: list[str] = field(default_factory=list)
    emotion: Optional[str] = None
    when_raw: Optional[str] = None
    when_normalized: bool = False

    def is_complete(self) -> bool:
        """是否满足最低完整性要求（who + what + when 三要素）"""
        return bool(self.who and self.what and self.when)

    def to_dict(self) -> dict:
        return {
            "who":             self.who,
            "when":            self.when,
            "where":           self.where,
            "what":            self.what,
            "participants":    self.participants,
            "emotion":         self.emotion,
            "when_raw":        self.when_raw,
            "when_normalized": self.when_normalized,
        }
