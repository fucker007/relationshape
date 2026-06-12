"""
llm/skills/usage_gate.py — Skill 2: Which recalled memories should be used?

Runs AFTER Phase 1 rule-based scoring (Top 5 candidates).
Makes the final semantic judgment: is this memory truly relevant to the query?

Target: precision >= 90%, recall >= 85%.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from config import settings
from llm.client import LLMClient, get_llm_client

_SYSTEM_PROMPT = """\
你是一个记忆使用决策系统。给定用户当前说的话和候选记忆列表，判断哪些记忆值得用于回答。

判断标准（三条，全部满足才选中）：
1. 确实是用户提到过的事物，不是泛泛相关
2. 与当前问句高度匹配，能让回答更准确、更贴近用户
3. 不词不达意——语义相关但用上去会显得牵强的，也不选

最多选 3 条。如果没有满足条件的，返回空列表。

输出格式（严格 JSON）：
{"selected": ["memory_id_1", "memory_id_2"], "reason": "一句话说明"}
"""


@dataclass
class UsageGateInput:
    query: str
    recalled_memories: list[dict]


@dataclass
class UsageGateOutput:
    selected: list[str]        # memory_ids, max 3
    reason: str


class UsageGate:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    async def decide(self, inp: UsageGateInput) -> UsageGateOutput:
        memories_text = json.dumps(
            [
                {
                    "memory_id": m.get("memory_id", ""),
                    "type": m.get("memory_type", ""),
                    "content": m.get("content", "")[:120],
                    "importance": m.get("importance_score", 0.5),
                }
                for m in inp.recalled_memories[:5]
            ],
            ensure_ascii=False,
        )

        data = await self._client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"用户说：{inp.query}\n\n候选记忆：{memories_text}"},
            ],
            model=settings.effective_fast_model,
            max_tokens=256,
            temperature=0.0,
        )

        selected = data.get("selected", [])
        # Hard cap at 3
        selected = selected[:3]

        return UsageGateOutput(
            selected=[str(s) for s in selected],
            reason=str(data.get("reason", "")),
        )
