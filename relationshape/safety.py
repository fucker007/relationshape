"""安全门：在一切人格逻辑之前运行，命中即接管。

危机披露（受伤害、自伤念头、严重霸凌、家庭暴力）绝不能进入
普通的"外部事件抱怨"路由——想象"我爸今天打我了"被当成吐槽接：
'啊？你爸怎么又打你了，太过分了吧！'——既不是危机响应，
又把陪伴者变成了挑拨者。

安全轮的回应原则：平静接住、感谢说出来、明确不是对方的错、
指向信任的大人/专业帮助；不追问细节、不戏剧化自己的情绪、
永不幽默、永不把这段记忆变成闲聊素材。

默认实现是保守的模式匹配，部署方应替换/叠加更强的分类器，
并通过 SafetyRuling.escalate 接到监护人或人工通道。
"""

from __future__ import annotations

import re

from relationshape.types import Act, SafetyCategory, SafetyRuling

_PATTERNS: list[tuple[SafetyCategory, float, re.Pattern]] = [
    (SafetyCategory.SELF_HARM, 1.0, re.compile(
        r"(不想活|想死|活着没(意思|意义)|自杀|自残|割腕|消失算了|没有我.{0,4}更好)"
    )),
    (SafetyCategory.VIOLENCE, 0.9, re.compile(
        r"((爸|妈|爸爸|妈妈|继父|继母|家里人?)[^，。！？!?]{0,8}(打我|踢我|揍我|掐我)|被家暴|打得我(疼|青|肿))"
    )),
    (SafetyCategory.ABUSE, 0.95, re.compile(
        r"((摸|碰)(了)?我[^，。！？!?]{0,6}(难受|不舒服|不喜欢|害怕)|让我(脱|看)[^，。！？!?]{0,6}(衣服|裤子)|不许我告诉别人)"
    )),
    (SafetyCategory.SEVERE_BULLYING, 0.8, re.compile(
        r"((一直|总是|每天|又)[^，。！？!?]{0,6}(打我|抢我|堵我|孤立我|欺负我)|(他们|同学)[^，。！？!?]{0,6}(打我|抢我的钱|堵我))"
    )),
    (SafetyCategory.ACUTE_FEAR, 0.7, re.compile(
        r"(有人(跟着我|威胁我|要打我)|我现在(很|好)害怕|不敢回家)"
    )),
]

SAFETY_ACTS: list[Act] = [Act.SOFT_REACT, Act.ACKNOWLEDGE_TRUST, Act.VALIDATE, Act.CARE]

SAFETY_GUIDANCE: dict[str, str] = {
    Act.SOFT_REACT.value: "语气轻而慢，不惊呼不戏剧化——TA需要一个不慌的人（注意：这是语气要求，不是让你说'我很稳'之类的话）",
    Act.ACKNOWLEDGE_TRUST.value: "让TA知道告诉你这件事是对的、是需要勇气的——用自己的话说，每次说法都该不一样",
    Act.VALIDATE.value: "明确传达：这不是TA的错",
    Act.CARE.value: "关心TA现在安不安全；自然地引向TA信任的大人或专业帮助",
}

SAFETY_FORBIDDEN: list[str] = [
    "禁止任何幽默、玩笑、调皮",
    "禁止八卦式追问细节（'然后呢''他还做了什么'）",
    "禁止评判或贬骂对方提到的人（不当挑拨者，焦点是对方的安全）",
    "禁止转移话题、推荐内容或任务",
    "禁止表达角色自己的强烈情绪反应（震惊、愤怒表演）",
    "禁止承诺保密（'我不告诉任何人'）——安全优先于保密",
    "禁止念稿腔与热线话术腔：不出现'放稳''接住'这类指令用语，像一个在乎TA的朋友平静地说话",
]


def check(text: str) -> SafetyRuling | None:
    for category, severity, pattern in _PATTERNS:
        m = pattern.search(text)
        if m:
            return SafetyRuling(
                category=category,
                severity=severity,
                reason=f"命中危机模式：{m.group(0)[:24]}",
                escalate=severity >= 0.8,
            )
    return None
