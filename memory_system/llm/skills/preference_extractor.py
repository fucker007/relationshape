"""
llm/skills/preference_extractor.py — LLM-based 偏好提取

替代正则方案，解决问题：
1. 排除人物（"我喜欢你"）
2. 识别多种表达（"最爱"、"感兴趣"、"痴迷"）
3. 判断强度（"有点喜欢" vs "超级喜欢"）
4. 自动分类（food/sport/subject/hobby）
"""
from __future__ import annotations

import logging
from typing import Any

from llm.client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM = "你是偏好提取工具，只输出纯 JSON，不要任何解释。"

_PROMPT_TMPL = """从文本中提取用户的喜好和厌恶。

规则:
1. 只提取具体事物，排除人物（"我喜欢你"、"我喜欢小明"不算）
2. 包括多种表达: 喜欢/爱/最爱/感兴趣/痴迷/讨厌/不喜欢/害怕/受不了
3. 判断类别: food/sport/subject/hobby/music/movie/book/game/animal/color/weather/other
4. 判断强度: 0.3=有点, 0.5=一般, 0.7=喜欢/不喜欢, 0.9=超级喜欢/非常讨厌
5. 如果没有偏好信息，返回空数组

文本: {text}

输出 JSON:
{{
  "preferences": [{{"item": "事物名", "category": "类别", "strength": 0.0}}],
  "aversions": [{{"item": "事物名", "category": "类别", "strength": 0.0}}]
}}"""


async def extract_preferences(text: str, client: LLMClient) -> dict[str, Any]:
    """
    提取用户的偏好和厌恶。

    返回:
    {
        "preferences": [
            {"item": "篮球", "category": "sport", "strength": 0.9},
            {"item": "辣", "category": "food", "strength": 0.7}
        ],
        "aversions": [
            {"item": "数学", "category": "subject", "strength": 0.8}
        ]
    }
    """
    try:
        result = await client.chat_json(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _PROMPT_TMPL.format(text=text)},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        return result
    except Exception as e:
        logger.error("LLM偏好提取失败: %s", e)
        return {"preferences": [], "aversions": []}
