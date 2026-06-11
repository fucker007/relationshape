"""感知层：把一句话变成 ConversationFrame + UserEmotionReading。

对应情商模型（Mayer-Salovey）第一支"感知情绪"：
先搞清楚对方在表达什么情绪、情绪指向谁（自己/外人/角色/设备/话题），
再谈怎么回应。指向判断错了，后面全是错的共情。

默认实现为规则启发式；类型和槽位是稳定契约，可整体替换为分类模型。
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from relationshape import zh
from relationshape.types import (
    BidType,
    ConversationFrame,
    EmotionTarget,
    InputType,
    UserEmotionReading,
)

# ---------------------------------------------------------------------------
# 词典与模式
# ---------------------------------------------------------------------------

EXTERNAL_ROLES = (
    "客户", "老板", "领导", "同事", "老师", "同学", "同桌", "教练",
    "朋友", "闺蜜", "哥们", "室友", "邻居",
    "妈妈", "爸爸", "我妈", "我爸", "哥哥", "姐姐", "弟弟", "妹妹", "爷爷", "奶奶",
)

_GREETING_RE = re.compile(r"^(你好|您好|哈喽|hello|hi|嗨|早上好|中午好|下午好|晚上好|早安|在吗|在不在)[呀啊!！~～。]*$", re.IGNORECASE)
_FAREWELL_RE = re.compile(r"(再见|拜拜|晚安|睡觉去|我去睡|我先走|我走了|下次聊|回头聊|去忙了)")
_SHORT_SET = {"嗯", "哦", "噢", "好", "行", "好的", "可以", "继续", "嗯嗯", "好吧", "然后呢", "对", "是的", "没了"}

_PRAISE_CHAR_RE = re.compile(r"(你真(棒|聪明|可爱|贴心|懂我)|你好(棒|聪明|可爱|厉害)|你(太|真|好)厉害|喜欢你|爱你|你最(好|棒)|夸夸你|你懂我)")
_ATTACK_CHAR_RE = re.compile(r"(你(真|好|太)?(笨|蠢|傻|烦|没用|垃圾)|笨蛋|蠢货|你是?智障|你什么都不(会|懂)|你还不如|你怎么这么(笨|蠢|差))")
_COMPARE_RE = re.compile(r"(不如|比不上|还没)(豆包|小爱|天猫精灵|siri|chatgpt|别的)", re.IGNORECASE)
_REASSURE_CHAR_RE = re.compile(r"((你)?别(难过|伤心|生气|委屈)|不怪你|没怪你|不是说你|逗你的|跟你开玩笑|你已经很(棒|好)了|我不是那个意思)")
_REJECT_RE = re.compile(r"(别说了|先别说|安静一会|我想自己待|走开|别烦我|不想聊|别吵|让我静静|你别管)")
_DEVICE_RE = re.compile(r"(卡(了|住|顿)|没声音|声音(太|好)(大|小)|听不清|断(了|线)|没反应|延迟|网(络)?(不好|卡)|怎么又卡)")
_SELF_BLAME_RE = re.compile(r"(我(真|好|太|就是)?(笨|没用|不行|很差|废物|什么都做不好)|我是不是(很笨|不行|没用)|都怪我)")
_ADVICE_HINT_RE = re.compile(r"(怎么办|该怎么|咋办|你说我(该|要|应该)|有什么(建议|办法)|帮我想想)")
_CREATIVE_RE = re.compile(r"(我想(做|写|画|设计|编|发明|搞)|我有个(想法|点子|主意)|我在(做|写|画|设计)|你觉得这个(设定|机制|故事|角色))")
_GOOD_NEWS_RE = re.compile(r"(我(考|拿|赢|得|通过|学会|完成|做出|进)了?|被?(录取|表扬|选上)|成功了|终于(做到|搞定|完成)|满分|第一名|中奖)")

_VULNERABLE_RE = re.compile(r"(其实|从来没(跟|和)别人说过|这是个?秘密|我不敢说|只(跟|和)你说)")

# 情绪词典：词 -> (label, valence, arousal)
_EMO_LEXICON: list[tuple[re.Pattern, str, float, float]] = [
    (re.compile(r"(难过|伤心|想哭|哭了|委屈|心情不好|不开心|低落|emo|沮丧|失落)"), "sad", -0.7, 0.4),
    (re.compile(r"(烦死|气死|好气|生气|愤怒|讨厌死|无语|烦透|火大)"), "angry", -0.7, 0.8),
    (re.compile(r"(烦|郁闷|心累)"), "annoyed", -0.5, 0.55),
    (re.compile(r"(害怕|好怕|很怕|特别怕|超怕|怕输|怕黑|吓死|恐怖|紧张|担心|焦虑|慌)"), "anxious", -0.6, 0.7),
    (re.compile(r"(累死|好累|太累|有点累|累了|疲惫|困死|没力气|熬夜)"), "tired", -0.4, 0.15),
    (re.compile(r"(开心|高兴|太好了|太棒了|超爽|兴奋|激动|期待|耶|爽)"), "happy", 0.8, 0.75),
    (re.compile(r"(还不错|挺好|放松|舒服|安心)"), "content", 0.5, 0.3),
    (re.compile(r"(无聊|没意思|没劲)"), "bored", -0.3, 0.15),
    (re.compile(r"(孤单|寂寞|没人理我|一个人)"), "lonely", -0.6, 0.3),
]

_NEG_EVENT_RE = re.compile(r"(批评|骂|凶|催|改需求|加班|放鸽子|抢|欺负|嘲笑|针对|误会|吵架|罚|拖堂|作业(好多|写不完))")


# 可插拔情绪后端：模型分类器从这里接入（设计契约：感知实现可整体替换）。
# 只影响情绪读数；对角色的二人称模式路由（攻击/夸/安抚/拒绝）始终走规则，
# 因为那些模式在目标域里高度规整，且是自尊/安全行为的触发器，不交给概率模型。
EmotionBackend = Callable[[str], Optional[tuple[str, float, float, float]]]
_emotion_backend: Optional[EmotionBackend] = None


def set_emotion_backend(fn: Optional[EmotionBackend]) -> None:
    """安装/卸载情绪分类后端。后端返回 None 时回落到内置词典。"""
    global _emotion_backend
    _emotion_backend = fn


def _detect_emotion(text: str) -> tuple[str, float, float, float]:
    """返回 (label, valence, arousal, confidence)。"""
    if _emotion_backend is not None:
        out = _emotion_backend(text)
        if out is not None:
            return out
    for pat, label, val, aro in _EMO_LEXICON:
        if pat.search(text):
            return label, val, aro, 0.75
    if zh.is_laughter(text):
        return "happy", 0.6, 0.6, 0.6
    if _NEG_EVENT_RE.search(text):
        return "annoyed", -0.4, 0.5, 0.5
    return "neutral", 0.0, 0.2, 0.4


def _find_actors(text: str) -> list[str]:
    found = [r for r in EXTERNAL_ROLES if r in text]
    # 去掉被更长词覆盖的（"我妈"会同时命中"妈妈"吗——不会，分开匹配；保持原样）
    return found


def _disclosure_depth(input_type: InputType, label: str, text: str) -> int:
    """社会渗透深度：自我表露越深，关系推进的分量越重。"""
    if _VULNERABLE_RE.search(text):
        return 3
    if input_type in (InputType.SELF_BLAME,):
        return 3
    if input_type in (InputType.SELF_DISTRESS,) or label in ("sad", "anxious", "lonely"):
        return 2
    if re.search(r"我(最|特别|超|很|挺)?(喜欢|讨厌|最爱|爱|害怕|怕|在学|想学|想要|的梦想)", text):
        return 1
    if input_type in (InputType.GOOD_NEWS, InputType.EXTERNAL_COMPLAINT):
        return 1
    return 0


def perceive(text: str) -> tuple[ConversationFrame, UserEmotionReading]:
    """主入口：输入一句话，输出现场帧与用户情绪。"""
    t = zh.normalize(text)
    frame = ConversationFrame()
    frame.laughed = zh.is_laughter(t)
    frame.is_question = zh.is_question(t)
    frame.asks_advice = zh.asks_advice(t)
    frame.topic_tokens = zh.content_runs(t)[:6]
    frame.actors = _find_actors(t)

    label, valence, arousal, conf = _detect_emotion(t)

    # ---- 输入类型判定（顺序即优先级）----
    itype: InputType
    target: EmotionTarget

    has_char_anchor = "你" in t

    if _GREETING_RE.match(t):
        itype, target = InputType.GREETING, EmotionTarget.TOPIC
    elif _FAREWELL_RE.search(t):
        itype, target = InputType.FAREWELL, EmotionTarget.TOPIC
    elif t in _SHORT_SET or len(t) <= 2:
        itype, target = InputType.SHORT_REPLY, EmotionTarget.TOPIC
    elif _ATTACK_CHAR_RE.search(t) or _COMPARE_RE.search(t):
        itype, target = InputType.CHARACTER_ATTACK, EmotionTarget.CHARACTER
        label, valence, arousal = "angry", -0.6, 0.7
    elif _REASSURE_CHAR_RE.search(t) and has_char_anchor:
        itype, target = InputType.CHARACTER_REASSURANCE, EmotionTarget.CHARACTER
        label, valence = "warm", max(valence, 0.3)
    elif _PRAISE_CHAR_RE.search(t):
        itype, target = InputType.CHARACTER_PRAISE, EmotionTarget.CHARACTER
        label, valence, arousal = "happy", 0.7, 0.6
    elif _REJECT_RE.search(t):
        itype, target = InputType.CHARACTER_REJECTION, EmotionTarget.CHARACTER
        if valence >= 0:
            label, valence, arousal = "annoyed", -0.3, 0.4
    elif _DEVICE_RE.search(t):
        itype, target = InputType.DEVICE_COMPLAINT, EmotionTarget.DEVICE
        if valence >= 0:
            label, valence, arousal = "annoyed", -0.4, 0.6
    elif _SELF_BLAME_RE.search(t):
        itype, target = InputType.SELF_BLAME, EmotionTarget.USER_SELF
        label, valence, arousal = "sad", -0.7, 0.45
    elif frame.asks_advice or _ADVICE_HINT_RE.search(t):
        itype = InputType.ASK_ADVICE
        target = EmotionTarget.EXTERNAL_PERSON if frame.actors else EmotionTarget.USER_SELF
    elif frame.actors and (valence < -0.15 or _NEG_EVENT_RE.search(t)):
        itype, target = InputType.EXTERNAL_COMPLAINT, EmotionTarget.EXTERNAL_PERSON
        if valence >= 0:
            label, valence, arousal = "annoyed", -0.45, 0.55
    elif valence < -0.25 and label in ("sad", "anxious", "tired", "lonely"):
        itype, target = InputType.SELF_DISTRESS, EmotionTarget.USER_SELF
    elif _GOOD_NEWS_RE.search(t) and valence >= 0:
        itype, target = InputType.GOOD_NEWS, EmotionTarget.USER_SELF
        label = "happy" if label == "neutral" else label
        valence, arousal = max(valence, 0.6), max(arousal, 0.6)
    elif frame.actors and valence > 0.25:
        itype, target = InputType.GOOD_NEWS, EmotionTarget.USER_SELF
    elif _CREATIVE_RE.search(t):
        itype, target = InputType.CREATIVE_TOPIC, EmotionTarget.TOPIC
    elif valence < -0.25:
        itype, target = InputType.SELF_DISTRESS, EmotionTarget.USER_SELF
    else:
        itype, target = InputType.TOPIC, EmotionTarget.TOPIC

    frame.input_type = itype
    frame.target = target

    # ---- 情感邀请类型（Gottman bids）----
    if itype in (InputType.SELF_DISTRESS, InputType.SELF_BLAME, InputType.EXTERNAL_COMPLAINT, InputType.ASK_ADVICE):
        frame.bid = BidType.SUPPORT
    elif frame.laughed or itype == InputType.CHARACTER_PRAISE:
        frame.bid = BidType.PLAY
    elif itype in (InputType.GOOD_NEWS, InputType.CREATIVE_TOPIC, InputType.TOPIC):
        frame.bid = BidType.CONNECTION
    elif itype == InputType.GREETING:
        frame.bid = BidType.ATTENTION
    else:
        frame.bid = BidType.NONE

    frame.disclosure_depth = _disclosure_depth(itype, label, t)
    frame.substantive = (
        itype not in (InputType.GREETING, InputType.FAREWELL, InputType.SHORT_REPLY)
        and (len(frame.topic_tokens) >= 1 or frame.disclosure_depth > 0)
    )

    reading = UserEmotionReading(
        label=label, valence=valence, arousal=arousal, target=target, confidence=conf
    )
    return frame, reading
