"""
llm/skills/forget_judge.py — Skill 4: Which low-score memories should be deleted?

Called during the scheduled forget_scan_job. Receives a batch of candidates
(already pre-filtered by decay score < threshold and hard-protection rules).
Makes the final call: delete or keep.

Model: settings.llm_fast_model (Haiku) — batch processing, cost-sensitive.
Batch size: max 50 (caller's responsibility to chunk).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from config import settings
from llm.client import LLMClient, get_llm_client

_SYSTEM_PROMPT = """\
你是一个记忆遗忘决策系统。给定一批遗忘分数低的候选记忆，决定哪些应该删除。

删除标准：
- forget_score < 0.05 且内容普通（日常闲聊、无特殊意义的情绪）→ 删除
- 与其他记忆内容高度重复 → 删除
- 虽然分数低，但内容是用户关键信息（重要事件、特殊经历）→ 保留

输出格式（严格 JSON）：
{"delete_ids": ["id1", "id2"], "keep_ids": ["id3"], "reason": "一句话说明"}
"""


@dataclass
class ForgetJudgeInput:
    candidates: list[dict]  # max 50, pre-filtered by caller


@dataclass
class ForgetJudgeOutput:
    delete_ids: list[str]
    keep_ids: list[str]
    reason: str


class ForgetJudge:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    async def decide(self, inp: ForgetJudgeInput) -> ForgetJudgeOutput:
        candidates_text = json.dumps(
            [
                {
                    "memory_id": c.get("memory_id", ""),
                    "type": c.get("memory_type", ""),
                    "content": str(c.get("content", ""))[:100],
                    "importance": c.get("importance_score", 0.5),
                    "days_old": c.get("days_old", 0),
                    "forget_score": round(c.get("forget_score", 0.0), 4),
                }
                for c in inp.candidates[:50]
            ],
            ensure_ascii=False,
        )

        data = await self._client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"候选记忆列表：{candidates_text}"},
            ],
            model=settings.effective_fast_model,
            max_tokens=512,
            temperature=0.0,
        )

        return ForgetJudgeOutput(
            delete_ids=[str(i) for i in data.get("delete_ids", [])],
            keep_ids=[str(i) for i in data.get("keep_ids", [])],
            reason=str(data.get("reason", "")),
        )
