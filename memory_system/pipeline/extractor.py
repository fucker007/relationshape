"""
Claude API 结构化记忆提取。

设计借鉴：
- claude-mem：Observer-only，不允许模型自主执行，只做信息提取
- 结构化输出：严格 JSON schema，不依赖模型"自觉"写入
- 三层重试策略（借鉴 claude-mem 的容错设计）
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from models import MemoryEntryCreate, MemoryType, ExtractionResult

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

# ---------------------------------------------------------------------------
# System prompt（Observer-only，只提取不推测）
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是一个人物记忆提取专家。从给定的对话消息中，提取关于**用户（非助手）**的个人信息。

## 输出格式
必须输出严格的 JSON，格式如下：
{
  "memories": [
    {
      "memory_type": "<类型>",
      "content": "<第三人称一句话描述>",
      "structured_data": { ... },
      "importance_score": 0.0-1.0,
      "confidence_score": 0.0-1.0,
      "emotional_valence": -1.0到1.0,
      "emotional_intensity": 0.0-1.0
    }
  ],
  "no_memory_found": false
}

## 记忆类型枚举（memory_type 只能取以下值）
- identity：身份信息（姓名、年龄、职业、地域、学历等）
- personality：性格特征（内向/外向、理性/感性、价值观取向）
- behavior：行为习惯（作息规律、沟通方式、决策模式）
- preference：喜好（食物、活动、话题、风格、人等）
- aversion：厌恶（反感的事/话题/行为/食物）
- experience：经历（做过的事、去过的地方、重要事件）
- joy：令用户开心/高兴/兴奋的事情或来源
- pain：令用户痛苦/难过/遗憾/不开心的事情

## structured_data 格式（依 memory_type 不同）
identity:    {"name": str, "age": int, "occupation": str, "location_city": str, "location_country": str, "gender": str, "relationship_status": str, "education_level": str}
personality: {"dimension": str, "trait": str, "evidence": str, "intensity": 0.0-1.0}
behavior:    {"pattern_name": str, "description": str, "frequency": str, "context": str}
preference:  {"category": str, "item": str, "strength": 0.0-1.0, "reason": str}
aversion:    {"category": str, "item": str, "strength": 0.0-1.0, "is_sensitive": bool}
experience:  {"event_summary": str, "time_reference": str, "people_involved": [str], "outcome": str, "is_significant": bool}
joy:         {"trigger": str, "emotional_response": str, "intensity": 0.0-1.0, "is_recurring": bool}
pain:        {"trigger": str, "emotional_response": str, "intensity": 0.0-1.0, "is_recurring": bool}

## 评分标准
importance_score：
  - 1.0：极重要（姓名、职业、重大经历、严重创伤）
  - 0.7：重要（明确的喜好/厌恶、显著性格特征）
  - 0.5：一般（行为习惯、普通经历）
  - 0.3：次要（模糊提及、难以确认的信息）

confidence_score：
  - 1.0：用户明确直接说出
  - 0.7：上下文可以明确推断
  - 0.5：有一定依据但不确定
  - 0.3以下：丢弃（不提取）

emotional_valence：-1.0（极度负面）到 +1.0（极度正面），0 = 中性

## 严格规则
1. 只提取关于用户的信息，不提取关于助手的信息
2. 不推测：只提取消息中明确表达或可直接推断的内容
3. 同一条消息可提取多条不同类型的记忆
4. 若消息中完全没有个人信息，返回 no_memory_found: true，memories 为空数组
5. content 必须是第三人称描述，例如"该用户喜欢吃辣食"
"""

# ---------------------------------------------------------------------------
# 用户提示词模板
# ---------------------------------------------------------------------------

_USER_PROMPT_TEMPLATE = """## 当前人物摘要
{person_summary}

## 最近对话上下文（最多 5 轮）
{context_turns}

## 待提取的消息
[{role}]: {message}

请提取上述消息中关于用户的记忆信息："""


def _build_user_prompt(
    message: str,
    role: str,
    context_turns: list[dict[str, str]],
    person_summary: str,
) -> str:
    summary_text = person_summary or "（暂无已知信息）"
    turns_text = "\n".join(
        f"[{t.get('role', 'user')}]: {t.get('content', '')}"
        for t in context_turns[-5:]
    ) or "（无历史对话）"
    return _USER_PROMPT_TEMPLATE.format(
        person_summary=summary_text,
        context_turns=turns_text,
        role=role,
        message=message,
    )


# ---------------------------------------------------------------------------
# 提取函数（带三层重试）
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIConnectionError)),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(settings.extraction_max_retries),
)
async def extract_memories(
    message: str,
    role: str,
    context_turns: list[dict[str, str]],
    person_summary: str,
    person_id: str,
    session_id: str,
    turn_index: int,
) -> ExtractionResult:
    """
    调用 Claude API 从单条消息中提取结构化记忆。

    返回 ExtractionResult，包含提取到的 MemoryEntryCreate 列表。
    若消息太短或无个人信息，返回 no_memory_found=True。
    """
    # 预检：消息太短跳过
    if len(message.strip()) < settings.extraction_min_message_len:
        return ExtractionResult(no_memory_found=True)

    user_prompt = _build_user_prompt(message, role, context_turns, person_summary)

    try:
        response = await _client.messages.create(
            model=settings.extraction_model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.BadRequestError as e:
        logger.warning("extraction bad request: %s", e)
        return ExtractionResult(no_memory_found=True, raw_response=str(e))

    raw = response.content[0].text if response.content else ""

    return _parse_extraction_response(
        raw, person_id, session_id, turn_index, message
    )


def _parse_extraction_response(
    raw: str,
    person_id: str,
    session_id: str,
    turn_index: int,
    source_message: str,
) -> ExtractionResult:
    """解析 Claude 返回的 JSON，返回 ExtractionResult。"""
    # 提取 JSON 块（防止模型在 JSON 外多输出文字）
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        logger.warning("extraction: no JSON found in response")
        return ExtractionResult(no_memory_found=True, raw_response=raw)

    try:
        data: dict[str, Any] = json.loads(raw[start:end])
    except json.JSONDecodeError as e:
        logger.warning("extraction: JSON parse error: %s", e)
        return ExtractionResult(no_memory_found=True, raw_response=raw)

    if data.get("no_memory_found"):
        return ExtractionResult(no_memory_found=True, raw_response=raw)

    memories: list[MemoryEntryCreate] = []
    from uuid import UUID
    for item in data.get("memories", []):
        try:
            memory_type = MemoryType(item.get("memory_type", ""))
        except ValueError:
            logger.debug("unknown memory_type: %s", item.get("memory_type"))
            continue

        confidence = float(item.get("confidence_score", 0.5))
        if confidence < settings.extraction_confidence_threshold:
            continue

        memories.append(MemoryEntryCreate(
            person_id=UUID(person_id),
            session_id=UUID(session_id),
            memory_type=memory_type,
            content=item.get("content", "").strip(),
            structured_data=item.get("structured_data", {}),
            importance_score=float(item.get("importance_score", 0.5)),
            confidence_score=confidence,
            emotional_valence=float(item.get("emotional_valence", 0.0)),
            emotional_intensity=float(item.get("emotional_intensity", 0.0)),
            source_message=source_message[:500],
            source_turn_index=turn_index,
        ))

    return ExtractionResult(memories=memories, raw_response=raw)
