"""
pipeline/coreference.py — 代词与指代消解

将事件 summary 中所有模糊指代（代词、关系称谓、泛指词）替换为对话中出现的真实人名。
"""
import logging
import re
from llm.client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
你是一个指代消解专家。给定一段对话上下文和一句事件描述，将描述中对**第三方人物**的模糊指代替换为真实人名。

已知人物列表中，**第一个人是主用户**（说话者本人），其余是对话中出现的第三方人物。

需要替换的模糊指代包括（仅限指代第三方的）：
- 人称代词：他、她、他们、她们
- 关系称谓：朋友、同学、老师、同事、对方、队友、室友、邻居、同伴、伙伴
- 泛指词：那个人、有人、另一个人、别人

替换规则：
1. 结合对话上下文判断指代的是哪位第三方人物，替换为真实姓名
2. "朋友/同学/对方"等通常指第三方，不要替换成主用户名字
3. 不要修改"我/我们"——事件描述以用户视角写，保留第一人称
4. 如果上下文中无法确认真实姓名，保留原词不变
5. 只输出替换后的句子，不要解释，不要改动其他内容
"""


async def resolve_pronouns_llm(
    text: str,
    context_persons: list[str],
    recent_turns: list[dict],
    llm_client: LLMClient,
) -> str:
    """
    用 LLM 对 text 做指代消解。
    结合 recent_turns 上下文和 context_persons 已知人物列表。
    失败时降级到规则方案。
    """
    if not text:
        return text

    # 构建对话上下文（最近8轮）
    context_lines = []
    for t in recent_turns[-8:]:
        role = "用户" if t.get("role") == "user" else "助手"
        context_lines.append(f"{role}: {t.get('content', '')}")
    context_text = "\n".join(context_lines)

    persons_hint = "、".join(context_persons) if context_persons else "（无已知人物）"

    user_msg = f"""已知人物：{persons_hint}

对话上下文：
{context_text}

事件描述（需要消解）：{text}

输出替换后的事件描述："""

    try:
        resp = await llm_client.chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.0,
        )
        resolved = resp.content.strip()

        # 去掉 LLM 可能带的引号或前缀
        resolved = re.sub(r'^["""「」【】\s]+|["""「」【】\s]+$', '', resolved)

        # 验证：长度不能离谱地膨胀（防止 LLM 输出了解释文字）
        if resolved and len(resolved) <= len(text) * 3:
            if resolved != text:
                logger.debug(f"[coref] '{text}' → '{resolved}'")
            return resolved

    except Exception as e:
        logger.warning(f"[coref] LLM 消解失败，降级到规则: {e}")

    return _resolve_rule_fallback(text, context_persons)


def _resolve_rule_fallback(text: str, context_persons: list[str]) -> str:
    """规则降级：替换最基础的人称代词。"""
    if not context_persons:
        return text

    main = context_persons[0]
    result = text
    result = re.sub(r'他([^，。！？\s]{1,3})', f'{main}的\\1', result)
    result = re.sub(r'她([^，。！？\s]{1,3})', f'{main}的\\1', result)
    result = re.sub(r'(?<![的是在])他(?![的是在们])', main, result)
    result = re.sub(r'(?<![的是在])她(?![的是在们])', main, result)
    return result
