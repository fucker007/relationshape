"""
models/person_graph.py — Person Graph 核心数据模型

三层架构：
  PersonNode  — 人物节点（属性层，可更新覆盖）
  Event       — 事件边（只追加的历史流）
  Relationship — 关系状态（由事件自动驱动更新）
  DailyEmotion — 日级情绪聚合
  CurrentFocus — 近期关注（快变，时间衰减）
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 近期关注
# ---------------------------------------------------------------------------

class FocusItem(BaseModel):
    """current_focus 中的一条关注项"""
    topic: str
    frequency: int = 1
    sentiment: str = "neutral"          # positive / negative / neutral
    first_seen: str = ""                # YYYY-MM-DD
    last_seen: str = ""                 # YYYY-MM-DD
    related_people: list[str] = Field(default_factory=list)
    weight: float = 1.0                 # 时间衰减后权重
    days_since_last_seen: int = 0       # 距离最后一次提及的天数


# ---------------------------------------------------------------------------
# PersonNode — 人物节点
# ---------------------------------------------------------------------------

class PersonNode(BaseModel):
    person_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID                       # 主用户 ID（对话主角）
    name: str
    role: Literal["primary", "secondary"] = "secondary"

    # 属性层（JSONB，可更新覆盖）
    identity: dict[str, Any] = Field(default_factory=dict)
    # {name, age, birthday, school, grade, gender, ...}

    # 自我认知（evolving self-concept）
    self_perception: dict[str, Any] = Field(default_factory=dict)
    # {traits: [{trait, confidence, evidence_count}], values: [{value, importance}], aspirations: [...]}

    personality: list[dict[str, Any]] = Field(default_factory=list)
    # [{trait, evidence, intensity}]

    preferences: list[dict[str, Any]] = Field(default_factory=list)
    # [{item, category, strength, since_event_id, stated, revealed, confidence}]
    # stated: bool - 用户明确说过喜欢
    # revealed: float - 在事件中出现的频率 (0.0-1.0)
    # confidence: float - 对这个偏好的信心 (0.0-1.0)

    aversions: list[dict[str, Any]] = Field(default_factory=list)
    # [{item, category, strength, since_event_id}]

    behaviors: list[dict[str, Any]] = Field(default_factory=list)
    # [{pattern, frequency, context}]

    # 近期关注层
    current_focus: list[FocusItem] = Field(default_factory=list)

    # 元信息
    total_events: int = 0
    last_active_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {UUID: str, datetime: lambda v: v.isoformat()}


# ---------------------------------------------------------------------------
# Event — 事件（替代 MemoryEntry 的动态部分）
# ---------------------------------------------------------------------------

EVENT_TYPES = {"conflict", "social", "achievement", "emotional", "change", "daily"}


class Event(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID                       # 主用户
    session_id: UUID

    # 事件核心
    event_time: datetime                 # 绝对时间（强制）
    event_time_raw: str | None = None    # 原始时间表达（"昨天"）
    event_type: str                      # conflict|social|achievement|emotional|change|daily
    summary: str                         # "和小华在球场打篮球"

    # 参与者
    participant_ids: list[UUID] = Field(default_factory=list)
    participant_names: list[str] = Field(default_factory=list)

    # 场景
    scene: str | None = None

    # 情绪
    emotions: dict[str, list[str]] = Field(default_factory=dict)
    # {person_id_str: ["开心", "兴奋"]}
    emotion_summary: str | None = None   # 主情绪

    # 意义层
    importance: float = 0.5
    belief_impact: str | None = None     # trust_decrease / self_confidence_up / ...
    impact: list[str] = Field(default_factory=list)
    # ["不想打球", "回避社交"]

    # 因果
    caused_by: UUID | None = None

    # 向量
    embedding: list[float] | None = None  # 1024-dim BGE-M3

    # 溯源
    source_message: str = ""
    extracted_by: str = ""

    # 生命周期
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {UUID: str, datetime: lambda v: v.isoformat()}


# ---------------------------------------------------------------------------
# Relationship — 关系状态（由事件自动驱动更新）
# ---------------------------------------------------------------------------

RELATION_TYPES = {"friend", "family", "classmate", "teacher", "rival", "crush", "other"}


class Relationship(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    from_person_id: UUID
    to_person_id: UUID
    relation_type: str = "other"         # friend|family|classmate|teacher|...
    sentiment: float = 0.0               # -1.0 ~ +1.0
    intensity: float = 0.0               # 0.0 ~ 1.0（互动频率）
    last_event_id: UUID | None = None
    last_event_time: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# DailyEmotion — 日级情绪聚合
# ---------------------------------------------------------------------------

class DailyEmotion(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    person_id: UUID
    date: date
    emotion_distribution: dict[str, float] = Field(default_factory=dict)
    # {"sad": 0.6, "angry": 0.3, "neutral": 0.1}
    dominant_emotion: str | None = None
    event_count: int = 0


# ---------------------------------------------------------------------------
# Narrative — 因果叙事链
# ---------------------------------------------------------------------------

class Narrative(BaseModel):
    """连接事件的因果叙事"""
    narrative_id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    title: str                          # "为什么我很害羞"
    theme: str                          # "social_anxiety", "self_confidence"

    events: list[UUID] = Field(default_factory=list)  # 有序事件链
    causal_chain: list[dict[str, Any]] = Field(default_factory=list)
    # [{from_event_id, to_event_id, relation_type, explanation}]
    # relation_type: "caused", "triggered", "reinforced", "resolved"

    resolution: str | None = None       # 如何解决的
    is_active: bool = True              # 是否仍在进行

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {UUID: str, datetime: lambda v: v.isoformat()}
