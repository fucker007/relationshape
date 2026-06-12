"""
llm/skills/merge_judge.py — Skill 3: How should a new memory be written?

Called BEFORE writing a new memory. Searches for similar existing memories
and asks the LLM to decide: ADD / UPDATE / DELETE / NONE.

Model: settings.llm_model (Sonnet) — accuracy over speed (async background path).
Target: action accuracy >= 85%, UPDATE content quality >= 80%.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from config import settings
from llm.client import LLMClient, get_llm_client

logger = logging.getLogger(__name__)

_MAX_NEW_CONTENT_LEN = 500   # B3: new_memory content 截断上限（existing 截断到 150）

_SYSTEM_PROMPT = """\
你是一个记忆合并系统。给定一条新提取的记忆和若干条相似的已有记忆，
决定如何处理这条新记忆。

操作选项：
- ADD: 全新信息，没有相似记忆，直接添加
- UPDATE: 找到相似记忆，新信息更完整/更新 → 合并覆盖旧记忆（保留旧 memory_id）
- DELETE: 新信息与旧信息矛盾 → 删除旧记忆（然后系统会自动 ADD 新记忆）
- NONE: 信息完全重复，已知内容 → 丢弃新记忆

UPDATE 规则：如果旧记忆"喜欢吃辣"，新记忆"喜欢川菜特别爱麻辣"（同向，更具体），
merged_content 应该是更完整的版本："喜欢川菜，特别爱麻辣口味"

DELETE 规则：如果旧记忆"喜欢吃辣"，新记忆"不想吃辣了/讨厌辣的/再也不吃辣"（直接相反），
则删除旧记忆 → 选 DELETE（系统会自动添加新记忆）

输出格式（严格 JSON）：
{
  "action": "ADD"|"UPDATE"|"DELETE"|"NONE",
  "target_id": "旧记忆的 memory_id（UPDATE/DELETE 时填写，ADD/NONE 时为 null）",
  "merged_content": "UPDATE 时的合并后内容（其他操作为 null）",
  "reason": "一句话理由"
}
"""


@dataclass
class MergeJudgeInput:
    new_memory: dict             # {type, content, importance}
    similar_existing: list[dict] # [{memory_id, content, memory_type, importance_score}, ...]


@dataclass
class MergeJudgeOutput:
    action: Literal["ADD", "UPDATE", "DELETE", "NONE"]
    target_id: str | None
    merged_content: str | None
    reason: str


class MergeJudge:
    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    async def decide(self, inp: MergeJudgeInput) -> MergeJudgeOutput:
        existing_text = json.dumps(
            [
                {
                    "memory_id": m.get("memory_id", ""),
                    "content": m.get("content", "")[:150],
                    "type": m.get("memory_type", m.get("type", "")),
                    "importance": m.get("importance_score", m.get("importance", 0.5)),
                }
                for m in inp.similar_existing[:5]
            ],
            ensure_ascii=False,
        ) if inp.similar_existing else "[]"

        new_text = json.dumps({
            "type": inp.new_memory.get("type", ""),
            "content": inp.new_memory.get("content", "")[:_MAX_NEW_CONTENT_LEN],  # Fix B3: 截断
            "importance": inp.new_memory.get("importance", 0.5),
        }, ensure_ascii=False)

        data = await self._client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"新记忆：{new_text}\n\n"
                    f"相似已有记忆（最多5条）：{existing_text}"
                )},
            ],
            model=settings.effective_model,
            max_tokens=256,
            temperature=0.0,
        )

        # Fix B1/B2: action 可能是 null 或非字符串类型
        raw_action = data.get("action", "ADD")
        if not isinstance(raw_action, str):
            logger.warning("MergeJudge: action is not str (%r), falling back to ADD", raw_action)
            action = "ADD"
        else:
            action = raw_action.strip().upper()
            if action not in ("ADD", "UPDATE", "DELETE", "NONE"):
                logger.warning("MergeJudge: unknown action %r, falling back to ADD", action)
                action = "ADD"

        target_id: str | None = data.get("target_id") or None
        merged_content: str | None = data.get("merged_content") or None

        # Fix B7: reason=null → str(None)='None'，应为空字符串
        raw_reason = data.get("reason")
        reason: str = str(raw_reason) if isinstance(raw_reason, str) else ""

        # Fix B6: DELETE 但 target_id 为 None → 回退 ADD（无法执行删除）
        if action == "DELETE" and target_id is None:
            logger.warning("MergeJudge: DELETE action but target_id is None, falling back to ADD")
            action = "ADD"

        # Fix B5: UPDATE 但 merged_content 为 None → 回退 ADD（无法执行更新）
        if action == "UPDATE" and merged_content is None:
            logger.warning("MergeJudge: UPDATE action but merged_content is None, falling back to ADD")
            action = "ADD"
            target_id = None

        # Fix B8: target_id 不在 similar_existing 中（孤儿引用）
        if target_id and inp.similar_existing:
            valid_ids = {
                str(m.get("memory_id", ""))
                for m in inp.similar_existing
                if isinstance(m, dict)
            }
            if target_id not in valid_ids:
                logger.warning(
                    "MergeJudge: target_id %r not in similar_existing %r, falling back to ADD",
                    target_id, valid_ids,
                )
                action = "ADD"
                target_id = None
                merged_content = None

        return MergeJudgeOutput(
            action=action,
            target_id=target_id,
            merged_content=merged_content,
            reason=reason,
        )
