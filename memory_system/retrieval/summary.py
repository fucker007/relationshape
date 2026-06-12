"""
记忆摘要拼装（供 LLM System Prompt 注入）。

借鉴 claude-mem 的 ContextBuilder：
- 按类型分组（身份、性格、喜好、注意事项、近期状态）
- 控制 token 预算（每条约 30 tokens，15 条约 450 tokens）
- 情感标注特殊处理（JOY/PAIN 生成"注意事项"）
"""
from __future__ import annotations

from typing import Any

from models import MemoryType

# 每种类型在摘要中的分组标题
_TYPE_SECTION: dict[str, str] = {
    MemoryType.IDENTITY:    "基本信息",
    MemoryType.PERSONALITY: "性格特点",
    MemoryType.BEHAVIOR:    "行为习惯",
    MemoryType.PREFERENCE:  "喜好",
    MemoryType.AVERSION:    "注意事项",    # 厌恶 → 注意事项
    MemoryType.EXPERIENCE:  "重要经历",
    MemoryType.JOY:         "令ta开心的事",
    MemoryType.PAIN:        "注意事项",    # 痛点 → 注意事项（与 AVERSION 合并）
}

_SECTION_ORDER = [
    "基本信息",
    "性格特点",
    "行为习惯",
    "喜好",
    "令ta开心的事",
    "重要经历",
    "注意事项",
]


def build_summary(
    display_name: str | None,
    memories: list[dict[str, Any]],
) -> str:
    """
    将记忆列表拼装成供 LLM 注入的人物摘要文本。
    """
    if not memories:
        return ""

    name = display_name or "该用户"
    sections: dict[str, list[str]] = {s: [] for s in _SECTION_ORDER}

    for m in memories:
        mt = m.get("memory_type", "")
        section = _TYPE_SECTION.get(mt, "重要经历")
        content = m.get("content", "").strip()
        if not content:
            continue

        # 情感事件加强调标记
        ev = float(m.get("emotional_valence", 0))
        if mt == MemoryType.PAIN and abs(ev) > 0.5:
            content = f"⚠️ {content}"
        elif mt == MemoryType.JOY and ev > 0.5:
            content = f"✨ {content}"

        sections[section].append(content)

    lines = [f"## 关于 {name} 的记忆\n"]
    for section in _SECTION_ORDER:
        items = sections.get(section, [])
        if not items:
            continue
        lines.append(f"**{section}**")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines).strip()
