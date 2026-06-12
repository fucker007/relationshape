from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import MemoryType


# ---------------------------------------------------------------------------
# structured_data schemas per memory type
# ---------------------------------------------------------------------------

class IdentityData(BaseModel):
    name: str | None = None
    age: int | None = None
    age_range: str | None = None          # "25-30" 当无法确定精确年龄
    occupation: str | None = None
    industry: str | None = None
    location_country: str | None = None
    location_city: str | None = None
    gender: str | None = None
    relationship_status: str | None = None
    education_level: str | None = None


class PersonalityData(BaseModel):
    dimension: str                         # "introversion", "emotional_style"
    trait: str                             # "内向"
    evidence: str                          # 支撑这个判断的原始文本
    intensity: float = 0.5                 # 0.0-1.0，特征强度


class BehaviorData(BaseModel):
    pattern_name: str                      # "sleep_schedule", "communication_style"
    description: str                       # "习惯在 23:00 后睡觉"
    frequency: str | None = None           # "daily", "weekly", "occasional"
    context: str | None = None             # 在什么情境下发生


class PreferenceData(BaseModel):
    category: str                          # "food", "activity", "topic", "style"
    item: str                              # "辣食"
    strength: float = 0.7                  # 0.0-1.0，喜好强度
    reason: str | None = None
    context: str | None = None


class AversionData(BaseModel):
    category: str                          # "topic", "behavior", "food"
    item: str                              # "被催促"
    strength: float = 0.7                  # 0.0-1.0，厌恶强度
    reason: str | None = None
    is_sensitive: bool = False             # 是否是敏感/禁忌话题


class ExperienceData(BaseModel):
    event_summary: str
    time_reference: str | None = None      # "去年"、"大学时期"
    location: str | None = None
    people_involved: list[str] = Field(default_factory=list)
    outcome: str | None = None             # 结果/影响
    is_significant: bool = False           # 是否重大经历


class EmotionalEventData(BaseModel):
    trigger: str                           # 触发情绪的事/人/情境
    emotional_response: str                # 情绪描述
    intensity: float = 0.5                 # 0.0-1.0
    is_recurring: bool = False             # 是否反复出现
    related_memory_ids: list[str] = Field(default_factory=list)


STRUCTURED_DATA_SCHEMAS: dict[MemoryType, type[BaseModel]] = {
    MemoryType.IDENTITY:    IdentityData,
    MemoryType.PERSONALITY: PersonalityData,
    MemoryType.BEHAVIOR:    BehaviorData,
    MemoryType.PREFERENCE:  PreferenceData,
    MemoryType.AVERSION:    AversionData,
    MemoryType.EXPERIENCE:  ExperienceData,
    MemoryType.JOY:         EmotionalEventData,
    MemoryType.PAIN:        EmotionalEventData,
}


# ---------------------------------------------------------------------------
# Core memory entry
# ---------------------------------------------------------------------------

class MemoryEntry(BaseModel):
    memory_id: UUID = Field(default_factory=uuid4)
    person_id: UUID
    session_id: UUID

    memory_type: MemoryType
    content: str                           # 自然语言描述（第三人称）
    structured_data: dict[str, Any] = Field(default_factory=dict)

    # 质量评估
    importance_score: float = 0.5          # 0.0-1.0
    confidence_score: float = 0.5          # 0.0-1.0
    emotional_valence: float = 0.0         # -1.0(负面) ~ +1.0(正面)
    emotional_intensity: float = 0.0       # 0.0-1.0

    # 向量（写入 PG 时填充，Redis 中不存储）
    embedding: list[float] | None = None   # 1024-dim (BGE-M3)

    # 溯源
    source_message: str = ""
    source_turn_index: int = 0
    extracted_by_model: str = ""

    # 生命周期
    is_merged: bool = False
    merged_from: list[UUID] = Field(default_factory=list)
    version: int = 1

    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    last_accessed_at: datetime | None = None  # Reset on each recall hit (decay clock)

    class Config:
        json_encoders = {UUID: str, datetime: lambda v: v.isoformat()}


class MemoryEntryCreate(BaseModel):
    """API 写入时使用（无 embedding，由系统生成）"""
    person_id: UUID
    session_id: UUID
    memory_type: MemoryType
    content: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    importance_score: float = 0.5
    confidence_score: float = 0.5
    emotional_valence: float = 0.0
    emotional_intensity: float = 0.0
    source_message: str = ""
    source_turn_index: int = 0


# ---------------------------------------------------------------------------
# 三层搜索结果（借鉴 claude-mem 的渐进式披露设计）
# ---------------------------------------------------------------------------

class MemorySearchResult(BaseModel):
    """Layer 1：紧凑索引结果（~50 tokens/条）"""
    memory_id: UUID
    person_id: UUID
    memory_type: MemoryType
    content: str
    importance_score: float
    emotional_valence: float
    created_at: datetime
    final_score: float                     # 综合评分


class MemoryDetail(MemoryEntry):
    """Layer 3：完整详情（~300 tokens/条）"""
    pass


# ---------------------------------------------------------------------------
# Person profile
# ---------------------------------------------------------------------------

class PersonProfile(BaseModel):
    person_id: UUID = Field(default_factory=uuid4)
    external_id: str                       # 业务系统中的用户 ID

    display_name: str | None = None
    summary: str | None = None             # 100 字以内的人物摘要
    summary_updated_at: datetime | None = None

    memory_counts: dict[str, int] = Field(default_factory=dict)  # type → count
    total_interactions: int = 0
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_active_at: datetime = Field(default_factory=datetime.utcnow)

    version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {UUID: str, datetime: lambda v: v.isoformat()}


# ---------------------------------------------------------------------------
# Extraction pipeline models
# ---------------------------------------------------------------------------

class ExtractionTask(BaseModel):
    task_id: UUID = Field(default_factory=uuid4)
    person_id: UUID
    session_id: UUID
    message_content: str
    message_role: str                      # "user" | "assistant"
    turn_index: int
    context_turns: list[dict[str, str]] = Field(default_factory=list)

    status: str = "pending"               # pending/processing/done/failed
    retry_count: int = 0
    max_retries: int = 3
    error_message: str | None = None

    scheduled_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


class ExtractionResult(BaseModel):
    """Claude API 返回的结构化提取结果"""
    memories: list[MemoryEntryCreate] = Field(default_factory=list)
    no_memory_found: bool = False
    raw_response: str = ""


# ---------------------------------------------------------------------------
# API request/response models
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    person_id: UUID
    session_id: UUID
    memories: list[MemoryEntryCreate]


class IngestResponse(BaseModel):
    accepted: int
    deduplicated: int
    memory_ids: list[str]


class ExtractAndIngestRequest(BaseModel):
    person_id: UUID
    session_id: UUID
    message: str
    role: str = "user"
    turn_index: int = 0
    context_turns: list[dict[str, str]] = Field(default_factory=list)
    bypass_gate: bool = False  # Skip ExtractionGate for windowed extraction


class ExtractAndIngestResponse(BaseModel):
    task_id: str
    status: str = "queued"


class RecallRequest(BaseModel):
    person_id: UUID
    context: str                           # 当前对话上下文（最近 3 轮）
    limit: int = 15
    types: list[MemoryType] | None = None
    min_importance: float = 0.3
    format: str = "summary"               # "raw" | "summary"


class RecallResponse(BaseModel):
    memories: list[MemorySearchResult] | None = None
    summary: str | None = None
    search_latency_ms: int = 0
    sources: dict[str, int] = Field(default_factory=dict)
    confidence: Literal["high", "uncertain", "low", "empty"] = "high"
    top_score: float = 0.0


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    extracted_count: int | None = None
    error: str | None = None
    completed_at: datetime | None = None
