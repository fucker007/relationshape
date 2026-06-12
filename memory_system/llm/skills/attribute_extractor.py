"""
llm/skills/attribute_extractor.py — LLM-based 属性提取

替代正则方案，解决问题：
1. 区分"我10岁" vs "我儿子10岁"
2. 排除"我叫小明过来"
3. 识别任意格式学校名
4. 判断日期是否为生日
"""
from __future__ import annotations

import json
import logging
from typing import Any

from llm.client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM = "你是身份信息提取工具，只输出纯 JSON，不要任何解释。"

_PROMPT_TMPL = """从对话中提取用户（"我"）的身份信息。

当前已知信息: {current_identity}

规则:
1. 只提取"我"的信息，忽略"我儿子"、"我朋友"、"我妈妈"、"我弟弟"等他人信息
2. 年龄 age（输出为整数）:
   - 提取"我X岁"、"我今年X岁"、"我X周岁"
   - 中文数字必须转阿拉伯数字: "我七岁啦"->7, "六岁"->6, "我十岁"->10, "二十"->20
   - 忽略"我儿子X岁"、"我今年X岁但我弟弟Y岁"中的Y
3. 名字 name:
   - 只在这些情况提取: "我叫X"、"我的名字是X"、"我名字叫X"、"叫我X"
   - 必须排除: "我叫X过来"、"我叫X帮忙"、"我叫X去"、"我叫X做"等（X是被叫的人，不是我的名字）
   - 判断方法: 如果"我叫"后面跟着动词（过来/帮忙/去/做/拿/给），则X不是我的名字
4. 学校 school:
   - 提取"我在XX上学"、"我读XX"、"我在XX(小学/中学/大学/幼儿园)读书"中的 XX
   - 例: "我在阳光小学读书"->"阳光小学", "我读北大附中"->"北大附中"
5. 年级 grade:
   - 提取"我读X年级"、"我上X年级"、"我是X年级"
   - 中文/阿拉伯数字均保留原样: "我上二年级"->"二年级", "我读3年级"->"3年级"
6. 性别 gender（输出 "male" 或 "female"）:
   - male: "我是男生"、"我是男的"、"我是个男孩"、"我是男孩子"
   - female: "我是女生"、"我是女的"、"我是个女孩"、"我是女孩子"
7. 生日 birthday: 仅当明确说"我的生日"、"我生日是"时提取，普通日期不算，格式 MM-DD
8. 如果文本中没有新信息，返回空对象{{}}
9. 字段缺失时不要写入 null/空字符串，直接省略该字段

文本: {text}

输出 JSON（只输出有的字段）:
{{"age": 数字, "name": "字符串", "school": "字符串", "grade": "字符串", "birthday": "MM-DD", "gender": "male/female"}}"""


async def extract_identity_attributes(
    text: str, current_identity: dict, client: LLMClient
) -> dict[str, Any]:
    """
    从文本中提取用户（"我"）的身份属性。

    返回:
    {
        "age": 10,
        "name": "小明",
        "school": "阳光小学",
        "grade": "四年级",
        "birthday": "03-15",
        "gender": "male"
    }
    """
    try:
        result = await client.chat_json(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _PROMPT_TMPL.format(
                    current_identity=json.dumps(current_identity, ensure_ascii=False),
                    text=text
                )},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        return result
    except Exception as e:
        logger.error("LLM属性提取失败: %s", e)
        return {}
