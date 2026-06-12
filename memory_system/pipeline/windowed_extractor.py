"""
pipeline/windowed_extractor.py — 窗口事件分割器

每5轮对话触发一次，以最近10轮为上下文，只标记最新5轮中的事件边界。
不做语义压缩，只做分割。原始对话完整保存。

语言配置通过 config/i18n/{lang}.yaml 加载，支持多语言独立调优。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from config import settings
from config.lang_config import get_lang_config
from llm.client import LLMClient, get_llm_client

logger = logging.getLogger(__name__)


def _force_extract_critical_event(target_turns: list[dict]) -> ExtractedEvent | None:
    """强制提取关键生活事件，不依赖 LLM。关键词和情绪映射从语言配置读取。"""
    cfg = get_lang_config()
    ext = cfg.extract

    critical_keywords = set(kw.lower() for kw in ext.get("critical_keywords", []))
    emotion_keywords = ext.get("emotion_keywords", {})
    critical_action = ext.get("critical_action", "关键事件")

    # 预编译情绪关键词（小写）
    emotion_map: list[tuple[str, set[str]]] = []
    for emotion_name, kw_list in emotion_keywords.items():
        emotion_map.append((emotion_name, set(kw.lower() for kw in kw_list)))

    for i, turn in enumerate(target_turns):
        if turn.get("role") != "user":
            continue

        content = turn.get("content", "").lower()
        if any(kw in content for kw in critical_keywords):
            # 检测情绪
            emotion = "neutral"
            for emotion_name, kw_set in emotion_map:
                if any(kw in content for kw in kw_set):
                    emotion = emotion_name
                    break

            return ExtractedEvent(
                turn_indices=[i],
                raw_turns=[turn],
                summary=content[:60],
                event_type="critical",
                action=critical_action,
                participants=[],
                scene=None,
                time_expr=None,
                emotion=emotion,
                emotion_detail=None,
            )
    return None


@dataclass
class ExtractedEvent:
    turn_indices: list[int]  # 属于这个事件的用户轮次索引
    raw_turns: list[dict]    # 原始对话 [{\"role\":\"user\",\"content\":\"...\"}]
    summary: str             # LLM 生成的一句话事件描述
    event_type: str
    action: str
    participants: list[str]
    scene: str | None
    time_expr: str | None
    emotion: str
    emotion_detail: str | None


@dataclass
class ExtractedAttribute:
    field: str
    key: str
    value: str
    target: str


@dataclass
class WindowExtractionResult:
    events: list[ExtractedEvent]
    attributes: list[ExtractedAttribute]


class WindowedExtractor:
    WINDOW_SIZE = 10
    TRIGGER_EVERY = 5

    def __init__(self, client: LLMClient | None = None):
        self._client = client or get_llm_client()

    async def extract(
        self,
        context_turns: list[dict],
        target_turns: list[dict],
        user_name: str,
        existing_events: list[str] | None = None,
    ) -> WindowExtractionResult:
        cfg = get_lang_config()
        system_prompt = cfg.extract.get("system_prompt", "")

        # P0-1: 强制提取关键事件
        critical_event = _force_extract_critical_event(target_turns)

        # 给 target_turns 加索引
        indexed_target = []
        for i, t in enumerate(target_turns):
            indexed_target.append({"idx": i, **t})

        msg_data = {
            "context_turns": context_turns,
            "target_turns": indexed_target,
            "user_name": user_name,
        }
        if existing_events:
            msg_data["existing_events"] = existing_events

        user_msg = json.dumps(msg_data, ensure_ascii=False)

        try:
            resp = await self._client.chat_json(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                model=settings.effective_fast_model,
                max_tokens=2000,
                temperature=0.0,
            )
            items = resp if isinstance(resp, list) else []
            # 兼容 qwen-plus 可能返回 {"events": [...]} 或 {"data": [...]} 的情况
            if not items and isinstance(resp, dict):
                items = resp.get("events") or resp.get("data") or resp.get("results") or []
                if not isinstance(items, list):
                    items = []
            logger.info(f"[windowed] LLM 返回 type={type(resp).__name__}, items={len(items)}")
        except Exception as e:
            logger.warning(f"窗口提取失败: {e}")
            # 如果 LLM 失败但有关键事件，仍然返回
            if critical_event:
                return WindowExtractionResult(events=[critical_event], attributes=[])
            return WindowExtractionResult(events=[], attributes=[])

        events = []
        attributes = []

        # P0-1: 优先添加关键事件
        if critical_event:
            events.append(critical_event)

        for item in items:
            item_type = item.get("type")

            if item_type == "event":
                try:
                    turn_indices = item.get("turn_indices", [])
                    if not turn_indices:
                        continue

                    # 提取原始对话
                    raw_turns = []
                    for idx in turn_indices:
                        if 0 <= idx < len(target_turns):
                            raw_turns.append(target_turns[idx])

                    if not raw_turns:
                        continue

                    # event_type 规范化：只允许合法 taxonomy，否则降级为 daily
                    _VALID_EVENT_TYPES = {"social", "conflict", "achievement", "emotional", "change", "daily"}
                    _raw_et = str(item.get("event_type", "daily")).lower().strip()
                    # 处理 LLM 返回多类型（如 "social|achievement"）→ 取第一个合法值
                    _et = "daily"
                    for _part in _raw_et.replace("|", "/").replace(",", "/").split("/"):
                        _part = _part.strip()
                        if _part in _VALID_EVENT_TYPES:
                            _et = _part
                            break

                    ev = ExtractedEvent(
                        turn_indices=turn_indices,
                        raw_turns=raw_turns,
                        summary=str(item.get("summary", "")).strip(),
                        event_type=_et,
                        action=str(item.get("action", ""))[:20],
                        participants=_clean_participants(item.get("participants"), user_name),
                        scene=item.get("scene"),
                        time_expr=item.get("time_expr"),
                        emotion=str(item.get("emotion", "neutral")),
                        emotion_detail=item.get("emotion_detail"),
                    )
                    # 过滤掉没有有效 summary 的事件
                    if not ev.summary or len(ev.summary) < 5:
                        continue
                    events.append(ev)
                except Exception:
                    pass

            elif item_type == "attribute":
                try:
                    attr = ExtractedAttribute(
                        field=str(item.get("field", "")),
                        key=str(item.get("key", ""))[:20],
                        value=str(item.get("value", ""))[:50],
                        target=str(item.get("target", "self")),
                    )
                    valid_fields = {
                        "identity", "preference", "aversion",
                        "behavior", "personality", "relationship",
                    }
                    if attr.field in valid_fields and attr.key and attr.value:
                        attributes.append(attr)
                except Exception:
                    pass

        return WindowExtractionResult(events=events, attributes=attributes)


def _clean_participants(raw: Any, user_name: str) -> list[str]:
    """清理参与者列表，黑名单和过滤规则从语言配置读取"""
    cfg = get_lang_config()
    ext = cfg.extract

    blacklist = set(ext.get("participant_blacklist", []))
    blacklist_lower = {b.lower() for b in blacklist}
    filter_words = ext.get("participant_filter_words", [])
    filter_particles = ext.get("participant_filter_particles", [])

    result = []
    if not isinstance(raw, list):
        return [user_name]
    for p in raw:
        if not isinstance(p, str) or not p.strip():
            continue
        p = p.strip()
        # 长度限制
        if len(p) > 30:
            continue
        if p.lower() in blacklist_lower or p == user_name:
            continue
        # 过滤含特定词的名字
        if filter_words and any(word in p for word in filter_words):
            continue
        # 短名字含语气助词过滤
        if filter_particles and len(p) <= 3 and any(char in p for char in filter_particles):
            continue
        result.append(p)
    return result if result else [user_name]
