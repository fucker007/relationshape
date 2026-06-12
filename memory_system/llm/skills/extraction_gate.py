"""
llm/skills/extraction_gate.py — Skill 1: Should this message be extracted?

Filters out low-value messages (greetings, commands, pure questions)
before the expensive Claude extraction call.

Target: recall >= 90% (valid memories pass through), false-positive <= 30%.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import settings
from llm.client import LLMClient, get_llm_client

_SYSTEM_PROMPT = """\
你是一个记忆提取门控系统。判断用户消息是否值得提取为长期记忆。

判断标准：
- 包含个人事实（身份/喜好/经历/情绪/习惯） → should_extract: true
- 纯闲聊、礼貌用语（"好的"、"嗯嗯"、"谢谢"）→ should_extract: false
- 纯问句、无陈述内容（"今天几号？"）→ should_extract: false
- 指令操作（"帮我查一下..."）→ should_extract: false

输出格式（严格 JSON）：
{"should_extract": true/false, "reason": "一句话理由", "confidence": 0.0到1.0}
"""


@dataclass
class ExtractionGateInput:
    message: str
    role: str
    recent_context: str = ""


@dataclass
class ExtractionGateOutput:
    should_extract: bool
    reason: str
    confidence: float


class ExtractionGate:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    async def check(self, inp: ExtractionGateInput) -> ExtractionGateOutput:
        context_note = f"\n最近对话摘要：{inp.recent_context}" if inp.recent_context else ""
        user_content = f"角色：{inp.role}\n消息：{inp.message}{context_note}"

        data = await self._client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            model=settings.effective_fast_model,
            max_tokens=128,
            temperature=0.0,
        )
        return ExtractionGateOutput(
            should_extract=bool(data.get("should_extract", True)),
            reason=str(data.get("reason", "")),
            confidence=float(data.get("confidence", 0.5)),
        )
