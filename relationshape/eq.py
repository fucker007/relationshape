"""高情商层：语言艺术与对话哲学的可执行收敛。

理论出处见 docs/EQ_CANON.md。十个机制：
1. 元信息解读（坦嫩：回应关系层，不回应字面）
2. 确认六级（莱恩汉 DBT：按表露深度/历史/阶段选确认等级）
3. 情绪粒度（巴瑞特：复述用准的词，不用"难过"接一切）
4. 知觉检核（阿德勒："没事"不当真也不戳穿，递麦克风不审讯）
5. 试探性命名（简德林/沃斯：替感受找词，永远可被纠正）
6. 依恋抗议解码（苏·约翰逊：高亲密的气话回应"你还在乎我吗"）
7. 支持式回应（Derber/布朗：不抢话头；禁"至少…""想开点"）
8. 幻想满足（法伯&玛兹丽施：现实给不了的用想象给足）
9. 痛快认错（伽达默尔：被说服是高情商不是输）
10. 言贵迟（《论语》：重的时刻话减半，可以没有问句）

本层只产出结构化增强（EQNotes），由引擎合并进 TurnDirective；
安全轮在引擎层已短路，不经过本层。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from relationshape.types import (
    Act,
    ConversationFrame,
    InputType,
    MemoryRecall,
    Stage,
    UserEmotionReading,
)

# ---------------------------------------------------------------------------
# 机制3：情绪粒度词表（巴瑞特）——按场景特征给出比"难过/生气"更准的词
# ---------------------------------------------------------------------------

GRANULAR_EMOTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(明明不是我|冤枉|(又|还|总)怪我|怪到我头上|不是我(干|弄|做)的)"), "被冤枉的憋屈"),
    (re.compile(r"(白(做|写|画|忙|弄|练)了?|重(做|写|画)|又(要|让我)?改)"), "白费劲的烦"),
    (re.compile(r"(没人(理|陪|懂)我|就我一个人|都不(跟|和)我玩|没人听)"), "没人接住的孤单"),
    (re.compile(r"(输了|没拿到|没考好|没(进|选上|评上)|落选)"), "不甘心"),
    (re.compile(r"(怕黑|怕鬼|一个人睡|做噩梦|不敢关灯)"), "夜里的害怕"),
    (re.compile(r"((明天|马上|快要|后天)[^，。]{0,8}(考|比赛|上台|表演|面试))"), "上场前的紧张"),
    (re.compile(r"(想哭|鼻子一?酸|眼泪|哭了)"), "心酸"),
    (re.compile(r"((笑|嘲笑)我|出丑|丢脸|没面子|当着.{0,6}的面(说|骂)我)"), "没面子的难受"),
    (re.compile(r"(都怪我|是我害的|我对不起)"), "自责"),
    (re.compile(r"(舍不得|要(走|搬家|转学)了|最后一(次|天))"), "舍不得"),
    (re.compile(r"((作业|功课|加班|补习)[^，。]{0,8}(写不完|做不完|好多|到(好晚|半夜))|喘不过气)"), "被压得喘不过气的累"),
]

# 机制1（补）：安心试探——问句外形，安心内核（维特根斯坦：回应游戏不回应句子）
_REASSURE_SEEK_RE = re.compile(
    r"(你(会不会|是不是)(忘了|不要|不喜欢|讨厌|烦)我|你还(记得|喜欢|要)我(吗|么)|我(是不是)?(很烦|招人烦))"
)

# 机制4：口头挡板——"没事"族（需配合低落惯性才触发检核，避免把客气当心事）
_DEFLECTION_RE = re.compile(r"^(没事|我没事|还好|还好吧|没什么|没怎么样?|随便|无所谓|算了|不想说了?)[吧呗啦哦呀~～。!！…]*$")

# 机制8：不可实现愿望
_IMPOSSIBLE_WISH_RE = re.compile(
    r"(要是[^，。]{0,12}就好了|真想(要|有|变成)|能不能给我变|我想要一(只|个|条|头)[^，。]{0,6}(恐龙|龙|独角兽|飞船|魔法|超能力)|要是能飞)"
)

# 机制9：用户纠正角色
_CORRECTION_RE = re.compile(r"(你(说|记|搞|弄|想)错了|不是这样(的)?|才不是(这样|呢)?|你不对|根本不是|哪有)")

# 机制1：输入类型 → 元信息（潜台词）
_METAMESSAGES: dict[InputType, str] = {
    InputType.EXTERNAL_COMPLAINT: "TA要的是'你站我这边'，不是事件分析或公正裁判",
    InputType.SELF_DISTRESS: "TA要的是被陪着，不是被修理好",
    InputType.SELF_BLAME: "字面在贬自己，实际在问'我是不是还值得被喜欢'——回应身份层，别和TA辩论事实",
    InputType.GOOD_NEWS: "TA在邀请你一起放大快乐——反应的热度比内容的正确更重要",
    InputType.ASK_ADVICE: "先弄清TA要的是办法，还是只是想被听完——拿不准就问一句",
    InputType.SHORT_REPLY: "话少本身是信息：可能累了、可能在等你接、可能心不在焉——跟着TA的节奏，别审问",
    InputType.DEVICE_COMPLAINT: "TA烦的是这次体验，不是在审判你这个人——别委屈过头",
    InputType.CHARACTER_PRAISE: "TA在递亲近，不是在要一段谦虚表演",
    InputType.CREATIVE_TOPIC: "TA在给你看TA的宝贝——先接住宝贝本身，再聊宝贝的细节",
}

# 共情禁令（布朗：同情的标志语；Derber：会话自恋）
_EMPATHY_BANS = [
    "不说'至少…'（'至少你还…'是在比惨，不是共情）",
    "不说'想开点''看开点''要正能量'",
    "不把话头抢到自己身上讲'我也…'的故事（支持式回应：所有话头跟着TA走）",
]


@dataclass
class EQNotes:
    """本轮的高情商增强，由引擎合并进 TurnDirective。"""

    metamessage: Optional[str] = None
    validation_hint: Optional[str] = None         # 确认等级与方向（莱恩汉）
    precise_emotion_word: Optional[str] = None    # 粒度词（巴瑞特）
    lead_acts: list[Act] = field(default_factory=list)     # 插到最前
    insert_acts: list[Act] = field(default_factory=list)   # 插到首动作之后
    tail_acts: list[Act] = field(default_factory=list)     # 追加到尾部
    guidance: dict[str, str] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)


def choose_validation(
    depth: int, memories: list[MemoryRecall], stage: Stage, granular: Optional[str]
) -> Optional[str]:
    """机制2：莱恩汉确认六级——浅事准确复述，深事读弦外之音，有旧事联历史，知己彻底真诚。"""
    if depth <= 0 and granular is None:
        return None
    if stage == Stage.CONFIDANT:
        return "确认等级⑥彻底真诚：放下技巧，像老朋友一样直说真话，不绕"
    if depth >= 2 and memories:
        return (
            f"确认等级④结合TA的经历：可以联系「{memories[0].text[:18]}」——"
            "'你最在乎的就是这个，难怪这么难受'，让情绪因TA的历史而合理"
        )
    if depth >= 2 or granular:
        return "确认等级③说出未说出口的那半句：读弦外之音，但用猜测语气，允许被纠正"
    return "确认等级②准确复述：用自己的话复述TA的重点，让TA确认你真的听到了"


def enrich(
    text: str,
    frame: ConversationFrame,
    reading: UserEmotionReading,
    stage: Stage,
    closeness: float,
    memories: list[MemoryRecall],
    last_user_valence: float,
) -> EQNotes:
    notes = EQNotes()
    t = text.strip()
    negative = reading.valence < -0.15

    # ---- 机制3：情绪粒度 ----
    for pat, word in GRANULAR_EMOTIONS:
        if pat.search(t):
            notes.precise_emotion_word = word
            break

    # ---- 机制1：元信息 ----
    if _REASSURE_SEEK_RE.search(t):
        notes.metamessage = "这不是信息问题，是安心问题——给确定感（'在呢，怎么会忘'），不做概率分析"
        notes.guidance[Act.REACT.value] = "先给安心，再轻轻问是不是发生了什么让TA想到这个"
    elif frame.input_type == InputType.CHARACTER_ATTACK and closeness >= 30:
        notes.metamessage = (
            "高亲密下的气话常是依恋抗议：'你还在乎我吗'——守住自尊的同时回应那句潜台词（'我在的'），不只回应字面"
        )
    elif frame.input_type == InputType.CHARACTER_REJECTION and closeness >= 30:
        notes.metamessage = "推开有时是在试探'我推你你会不会消失'——尊重TA要的空间，但留一句'我在门口'"
    else:
        notes.metamessage = _METAMESSAGES.get(frame.input_type)

    # ---- 机制2：确认等级 ----
    if negative or frame.disclosure_depth >= 2:
        notes.validation_hint = choose_validation(
            frame.disclosure_depth, memories, stage, notes.precise_emotion_word
        )

    # ---- 机制9：痛快认错（优先级最高，抢链头） ----
    if _CORRECTION_RE.search(t) and frame.input_type not in (
        InputType.CHARACTER_ATTACK, InputType.SELF_BLAME,
    ):
        notes.lead_acts.append(Act.CONCEDE)
        notes.guidance[Act.CONCEDE.value] = (
            "痛快承认+可见地被说服：'欸，你说得对，我想岔了'——然后顺着对的说下去"
        )
        notes.forbidden.append("不辩解、不找补、不'但是'（被改变不丢人，防卫才丢人）")

    # ---- 机制4：知觉检核（口头挡板 × 低落惯性才触发，客气话不戳穿） ----
    if _DEFLECTION_RE.match(t) and last_user_valence < -0.2:
        notes.insert_acts.append(Act.PERCEPTION_CHECK)
        notes.guidance[Act.PERCEPTION_CHECK.value] = (
            "知觉检核三步：描述你注意到的+给两种解读+把选择权递回去——"
            "'你说没事——是真的还好，还是有点不想说？不想说咱们就不说，我陪着。'"
            "TA确认没事就放过，绝不深挖"
        )

    # ---- 机制5：试探性命名 ----
    if notes.precise_emotion_word and frame.input_type not in (
        InputType.CHARACTER_ATTACK, InputType.CHARACTER_REJECTION,
    ):
        notes.insert_acts.append(Act.NAME_FEELING)
        notes.guidance[Act.NAME_FEELING.value] = (
            f"试探地替感受找词：'是不是有点{notes.precise_emotion_word}？'——"
            "用'听起来/我猜'开头，猜错了痛快接受纠正"
        )

    # ---- 机制8：幻想满足 ----
    if _IMPOSSIBLE_WISH_RE.search(t) and reading.valence > -0.3:
        notes.tail_acts.append(Act.FANTASY_GRANT)
        notes.guidance[Act.FANTASY_GRANT.value] = (
            "现实给不了的，用想象给足：把愿望放大成一场小白日梦，和TA一起玩两句，不解释'为什么不行'"
        )

    # ---- 机制7：支持式回应 + 共情禁令 ----
    if negative:
        notes.forbidden.extend(_EMPATHY_BANS)
        if stage in (Stage.FAMILIAR, Stage.COMPANION, Stage.CONFIDANT):
            notes.constraints.append(
                "自我表露只在TA被接住之后，且必须服务于TA（'我也会怕黑'是桥，不是抢戏）"
            )

    # ---- 机制10：言贵迟 ----
    heavy = reading.valence <= -0.5 or frame.disclosure_depth >= 3
    if heavy:
        notes.constraints.append("话要少：重的时刻字数减半、慢一点，可以没有问句（言贵迟）")

    # ---- 蔡康永：问句小颗粒化（并入机制4/5的语气规范） ----
    if negative and frame.input_type in (
        InputType.EXTERNAL_COMPLAINT, InputType.SELF_DISTRESS, InputType.ASK_ADVICE,
    ):
        notes.constraints.append(
            "追问要具体小颗粒（'TA当时说了什么'），不问大而空的（'你感觉怎么样'）"
        )

    return notes
