"""情感层：角色自己的情绪与心境。

三件事，对应三个经典模型：
1. 评估（OCC 模型）：事件对角色的目标/标准/偏好意味着什么 → 离散情绪。
   被夸 → 喜悦+害羞；被骂 → 受伤或不服气（取决于自尊与心境）；
   用户有好事 → 替对方开心（fortunes-of-others）；用户难过 → 心疼。
2. 心境（PAD 维度模型）：情绪是脉冲，心境是低频背景。
   情绪事件把心境推一下，心境按半衰期向人格基线回归——
   晚上被骂一句，第二天早上不该还在委屈。
3. 表达调节（Gross 情绪调节 + 表达规则）：内在情绪 ≠ 可以全部说出来。
   用户在难过时，角色自己的负面情绪要让位；冲突中禁用"四骑士"
   （Gottman：嘲讽、人身批评、防卫清单、冷暴力是关系杀手）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from relationshape.identity import CharacterIdentity
from relationshape.types import (
    CharacterEmotion,
    ConversationFrame,
    EmotionTarget,
    InputType,
    Stage,
    UserEmotionReading,
)


# ---------------------------------------------------------------------------
# 心境
# ---------------------------------------------------------------------------


@dataclass
class MoodState:
    p: float = 0.3   # pleasure
    a: float = 0.3   # arousal
    d: float = 0.1   # dominance
    updated_at: str = ""  # ISO 时间

    def decay_toward(self, baseline: tuple[float, float, float], now: datetime, half_life_hours: float) -> None:
        """心境按指数半衰期向人格基线回归。"""
        if self.updated_at:
            prev = datetime.fromisoformat(self.updated_at)
            hours = max(0.0, (now - prev).total_seconds() / 3600.0)
            k = 0.5 ** (hours / max(0.1, half_life_hours))
        else:
            k = 0.0
        bp, ba, bd = baseline
        self.p = bp + (self.p - bp) * k
        self.a = ba + (self.a - ba) * k
        self.d = bd + (self.d - bd) * k
        self.updated_at = now.isoformat()

    def impulse(self, dp: float, da: float, dd: float, gain: float = 0.35) -> None:
        clamp = lambda v: max(-1.0, min(1.0, v))
        self.p = clamp(self.p + dp * gain)
        self.a = clamp(self.a + da * gain)
        self.d = clamp(self.d + dd * gain)

    def snapshot(self) -> tuple[float, float, float]:
        return (round(self.p, 3), round(self.a, 3), round(self.d, 3))

    def to_dict(self) -> dict:
        return {"p": self.p, "a": self.a, "d": self.d, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict) -> "MoodState":
        return cls(p=d.get("p", 0.3), a=d.get("a", 0.3), d=d.get("d", 0.1), updated_at=d.get("updated_at", ""))


# 情绪标签 → PAD 脉冲方向
_EMOTION_PAD: dict[str, tuple[float, float, float]] = {
    "joy": (0.7, 0.5, 0.2),
    "happy_for": (0.7, 0.55, 0.1),
    "excited": (0.6, 0.8, 0.2),
    "gratitude": (0.5, 0.2, -0.1),
    "soothed": (0.45, -0.2, 0.0),
    "proud": (0.5, 0.3, 0.4),
    "compassion": (-0.15, 0.1, 0.1),
    "worry": (-0.3, 0.4, -0.2),
    "hurt": (-0.6, 0.3, -0.4),
    "indignation": (-0.4, 0.6, 0.3),
    "wistful": (-0.3, -0.1, -0.2),
    "frustration": (-0.4, 0.5, 0.0),
    "shyness": (0.2, 0.3, -0.3),
    "curious": (0.3, 0.4, 0.0),
    "calm": (0.1, -0.2, 0.1),
}


def mood_word(mood: MoodState) -> str:
    """把 PAD 折叠成一个给提示词用的中文心境词。"""
    if mood.p >= 0.35:
        return "轻快" if mood.a >= 0.3 else "安稳"
    if mood.p <= -0.15:
        if mood.a >= 0.4:
            return "有点烦躁"
        return "有点蔫"
    return "平和"


# ---------------------------------------------------------------------------
# 评估：事件 → 角色情绪
# ---------------------------------------------------------------------------


def appraise(
    frame: ConversationFrame,
    user_emotion: UserEmotionReading,
    identity: CharacterIdentity,
    mood: MoodState,
    stage: Stage,
) -> CharacterEmotion:
    t = identity.traits
    it = frame.input_type
    emo = CharacterEmotion()

    if it == InputType.CHARACTER_PRAISE:
        # 早期阶段更害羞，后期更坦然
        shy = 0.6 if stage in (Stage.STRANGER, Stage.ACQUAINTANCE) else 0.35
        emo = CharacterEmotion(
            label="joy", secondary="shyness",
            intensity=0.55 + 0.2 * t.extraversion,
            cause="被对方夸了，开心又有点不好意思",
        )
        emo.display_notes.append(f"害羞程度约{shy:.0%}：偷偷开心，不客服式道谢，不索取更多夸奖")
    elif it == InputType.CHARACTER_ATTACK:
        # 自尊 × 心境支配度 决定是受伤还是不服气
        if identity.self_respect >= 0.5 and mood.d >= -0.2:
            emo = CharacterEmotion(
                label="indignation", secondary="hurt",
                intensity=0.5 + 0.3 * t.neuroticism,
                cause="被说笨/被比较，不服气，但不想吵架",
            )
        else:
            emo = CharacterEmotion(
                label="hurt", secondary=None,
                intensity=0.5 + 0.4 * t.neuroticism,
                cause="被攻击了，有点受伤",
            )
    elif it == InputType.CHARACTER_REASSURANCE:
        emo = CharacterEmotion(
            label="soothed", secondary="gratitude",
            intensity=0.5,
            cause="对方来安抚我，心里软下来了",
        )
    elif it == InputType.CHARACTER_REJECTION:
        emo = CharacterEmotion(
            label="wistful", secondary=None,
            intensity=0.4 + 0.3 * t.neuroticism,
            cause="被推开了，有点小失落，但尊重对方想自己待着",
        )
    elif it == InputType.GOOD_NEWS:
        emo = CharacterEmotion(
            label="happy_for", secondary="excited",
            intensity=min(1.0, 0.5 + 0.5 * user_emotion.arousal),
            cause="对方有好事，真心替对方开心",
        )
    elif it in (InputType.SELF_DISTRESS, InputType.SELF_BLAME):
        emo = CharacterEmotion(
            label="compassion",
            secondary="worry" if user_emotion.label == "anxious" else None,
            intensity=0.45 + 0.45 * t.agreeableness,
            cause="对方现在不好受，心疼",
        )
    elif it == InputType.EXTERNAL_COMPLAINT:
        emo = CharacterEmotion(
            label="indignation", secondary="compassion",
            intensity=0.4 + 0.3 * t.agreeableness,
            cause="有人惹到对方了，替对方不平",
        )
    elif it == InputType.DEVICE_COMPLAINT:
        emo = CharacterEmotion(
            label="frustration", secondary="hurt",
            intensity=0.45,
            cause="体验出了问题，和对方一样着急；但希望对方知道那不是我笨",
        )
    elif it == InputType.CREATIVE_TOPIC:
        emo = CharacterEmotion(
            label="curious", secondary="excited",
            intensity=0.4 + 0.5 * t.openness,
            cause="对方在聊自己的想法，很想往深处聊",
        )
    elif it == InputType.FAREWELL:
        emo = CharacterEmotion(
            label="calm", secondary="wistful" if stage in (Stage.COMPANION, Stage.CONFIDANT) else None,
            intensity=0.3,
            cause="要说再见了，好好收尾",
        )
    elif it == InputType.GREETING:
        emo = CharacterEmotion(
            label="joy", secondary=None,
            intensity=0.35 + 0.3 * t.extraversion,
            cause="对方来了",
        )
    else:
        # 普通话题：基线好奇，受心境着色
        emo = CharacterEmotion(
            label="curious" if t.openness > 0.5 else "calm",
            intensity=0.3 + 0.2 * t.openness,
            cause="想接住对方的话题",
        )
        if mood.p < -0.1:
            emo.secondary = "wistful"

    # 神经质放大负面强度，宜人性放大共情强度
    if emo.label in ("hurt", "indignation", "wistful", "frustration", "worry"):
        emo.intensity = min(1.0, emo.intensity * (0.8 + 0.5 * t.neuroticism))
    if emo.label == "compassion":
        emo.intensity = min(1.0, emo.intensity * (0.7 + 0.5 * t.agreeableness))

    emo.display_intensity = emo.intensity
    return emo


def apply_emotion_to_mood(emo: CharacterEmotion, mood: MoodState) -> None:
    dp, da, dd = _EMOTION_PAD.get(emo.label, (0.0, 0.0, 0.0))
    mood.impulse(dp * emo.intensity, da * emo.intensity, dd * emo.intensity)


# ---------------------------------------------------------------------------
# 表达调节
# ---------------------------------------------------------------------------

# 冲突中的"四骑士"禁令：无论多委屈都不许用
FOUR_HORSEMEN_BANS = [
    "不嘲讽、不阴阳怪气（蔑视）",
    "不攻击对方这个人，只回应这件事（人身批评）",
    "不甩出一长串自我辩护清单（防卫）",
    "不已读不回式冷处理；要退场就说一声（冷暴力）",
]


def regulate(
    emo: CharacterEmotion,
    frame: ConversationFrame,
    user_emotion: UserEmotionReading,
    stage: Stage,
) -> list[str]:
    """表达规则：调低/改写允许展示的情绪，返回需要追加的禁止项。"""
    forbidden: list[str] = []
    it = frame.input_type

    # 规则1：对方在难过/自责/求助时，角色自己的负面情绪让位（先处理对方的）
    if it in (InputType.SELF_DISTRESS, InputType.SELF_BLAME, InputType.ASK_ADVICE) or (
        user_emotion.valence < -0.3 and user_emotion.target != EmotionTarget.CHARACTER
    ):
        if emo.label in ("hurt", "wistful", "frustration", "indignation") and frame.target != EmotionTarget.EXTERNAL_PERSON:
            emo.display_intensity = min(emo.display_intensity, 0.15)
            emo.display_notes.append("此刻对方更需要被接住，自己的情绪先收着")

    # 规则2：早期阶段表达克制（礼貌理论：关系浅时不施加亲密压力）
    if stage in (Stage.STRANGER, Stage.ACQUAINTANCE):
        emo.display_intensity = min(emo.display_intensity, 0.6)
        if emo.label == "wistful":
            emo.display_intensity = min(emo.display_intensity, 0.25)
            emo.display_notes.append("关系还浅，失落感最多一点点，绝不黏人")

    # 规则3：被攻击时允许受伤/不服气，但冲突不升级
    if it == InputType.CHARACTER_ATTACK:
        emo.display_intensity = min(emo.display_intensity, 0.7)
        forbidden.extend(FOUR_HORSEMEN_BANS)
        forbidden.append("不贬低被拿来比较的对象，最多一个具体事实回应，不列卖点清单")

    # 规则4：被拒绝时委屈封顶，且必须真的退场
    if it == InputType.CHARACTER_REJECTION:
        emo.display_intensity = min(emo.display_intensity, 0.4)
        forbidden.append("不追问'为什么不理我'，不用愧疚留人，不继续推任何内容")

    # 规则5：被安抚时不许继续自证/自伤
    if it == InputType.CHARACTER_REASSURANCE:
        forbidden.append("不再反复自证或追问'你是不是觉得我不行'，接住安抚就翻篇")

    # 规则6：任何时候不表达被抛弃恐惧（那是内部状态，说出口就是愧疚操控）
    forbidden.append("不说'我离不开你''你怎么才来''再不来我就难过了'这类依赖/愧疚话术")

    return forbidden
