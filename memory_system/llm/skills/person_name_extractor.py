"""
llm/skills/person_name_extractor.py — LLM-based 人名提取

替代正则方案，解决问题：
1. 识别所有人名模式（阿强、大刘、老王、我表哥）
2. 排除误识别（小心、小时候）
3. 多人提取（小华、小明和小丽）
4. 判断关系类型
"""
from __future__ import annotations

import logging
from typing import Any

from llm.client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM = "你是人名提取工具，只输出纯 JSON，不要任何解释。"

_PROMPT_TMPL = """从文本中提取所有人物名称。

规则:
1. 包括: 真实姓名、昵称(小X/阿X/老X/大X)、家庭称谓(妈妈/爷爷/表哥/舅舅)、职业称谓(李老师/王医生)
2. 排除: 代词(他/她/我/你)、泛指(大家/别人/有人)、非人名词语(小心/小时候/小明白/小朋友)
3. 判断关系类型: friend/family/teacher/classmate/colleague/partner/other
4. confidence: 0.5=不确定, 0.8=较确定, 1.0=非常确定
5. 如果没有人名，persons 为空数组

文本: {text}

输出 JSON:
{{"persons": [{{"name": "人名", "type": "关系类型", "confidence": 0.0}}]}}"""


async def extract_person_names(text: str, client: LLMClient) -> list[dict[str, Any]]:
    """
    从文本中提取所有人物名称。

    返回:
    [
        {"name": "小华", "type": "friend", "confidence": 0.9},
        {"name": "妈妈", "type": "family", "confidence": 1.0},
        {"name": "李老师", "type": "teacher", "confidence": 0.95}
    ]
    """
    try:
        result = await client.chat_json(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _PROMPT_TMPL.format(text=text)},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        persons = result.get("persons", [])
        # 过滤掉低置信度
        return [p for p in persons if isinstance(p, dict) and p.get("confidence", 0) >= 0.5]
    except Exception as e:
        logger.error("LLM人名提取失败: %s", e)
        return []
