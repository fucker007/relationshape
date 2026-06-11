"""幽默系统：什么时候可以幽默、用哪种幽默、拿什么当素材。

理论依据：
- 良性冒犯理论（McGraw & Warren）：好笑 = 有点越界 × 确定无害。
  时机不对（对方在难过）或素材不对（对方的脆弱处），冒犯就不再良性——先门禁后创作。
- Martin 幽默风格：只用亲和型与自强型这两个健康象限；
  攻击型永久禁用；自嘲有自尊下限（可以讲糗事，不可以自贬人格）。
- 内部梗（关系文化）：回调一个一起笑过的梗，是性价比最高的亲密信号。
- 喜剧节奏：稀缺才有惊喜，幽默有冷却时间。

规划是确定性的（阈值制，不掷骰子），便于测试与复盘。
"""

from __future__ import annotations

from relationshape.adaptation import AdaptationState
from relationshape.affect import MoodState
from relationshape.relationship import StagePolicy
from relationshape.types import (
    ConversationFrame,
    HumorPlan,
    HumorStyle,
    InputType,
    Stage,
    UserEmotionReading,
)

# 这些输入类型下绝不幽默（共情优先于好笑）
_NO_HUMOR_TYPES = {
    InputType.SELF_DISTRESS,
    InputType.SELF_BLAME,
    InputType.EXTERNAL_COMPLAINT,
    InputType.CHARACTER_ATTACK,
    InputType.CHARACTER_REJECTION,
    InputType.DEVICE_COMPLAINT,
    InputType.ASK_ADVICE,
    InputType.FAREWELL,
}


def plan_humor(
    frame: ConversationFrame,
    user_emotion: UserEmotionReading,
    mood: MoodState,
    stage: Stage,
    policy: StagePolicy,
    adaptation: AdaptationState,
    turns_since_humor: int,
    cooldown: int,
    aversion_tags: list[str],
    context_low: bool = False,
) -> HumorPlan | None:
    # ---- 门禁（benign 检查）----
    if frame.input_type in _NO_HUMOR_TYPES:
        return None
    if user_emotion.valence < -0.15:
        return None
    # 情绪惯性：对方上一轮还在低落里，这一轮没有明确转晴就不开玩笑
    if context_low and user_emotion.valence <= 0.1:
        return None
    if stage == Stage.STRANGER:
        return None          # 初识阶段先把安全感建立起来
    if turns_since_humor < cooldown:
        return None
    if mood.p < -0.1:
        return None          # 角色自己情绪低的时候不硬挤笑话

    # ---- 风格选择 ----
    rec = adaptation.humor_receptivity

    # 内部梗回调：有梗、近期没用过、接收度不差 → 首选
    if policy.callbacks_allowed and adaptation.inside_jokes:
        joke = max(adaptation.inside_jokes, key=lambda j: j.created_at)
        if rec.get(HumorStyle.CALLBACK.value, 0.5) >= 0.4 and frame.bid.value in ("connection", "play", "attention"):
            return HumorPlan(
                style=HumorStyle.CALLBACK,
                device="callback",
                material=joke.label,
                intensity=0.5,
                guidance=f"自然回调你们的内部梗「{joke.label}」，一句带过，别解释梗",
            )

    # 轻度打趣：高阶段 + 对方明确吃这套 + 素材不碰对方雷区
    if (
        policy.tease_allowed
        and rec.get(HumorStyle.PLAYFUL_TEASE.value, 0.5) >= 0.58
        and frame.topic_tokens
        and not any(av in tok for av in aversion_tags for tok in frame.topic_tokens)
        and frame.bid.value == "play"
    ):
        return HumorPlan(
            style=HumorStyle.PLAYFUL_TEASE,
            device="tease",
            material=frame.topic_tokens[0],
            intensity=0.4,
            guidance="可以就这个话题轻轻打趣对方一下，点到为止，带着明显的善意",
        )

    # 亲和型：默认健康选项，在轻松话题里加一点夸张或趣味观察
    propensity = (
        0.30 * max(0.0, mood.p)
        + 0.20 * max(0.0, mood.a)
        + 0.30 * rec.get(HumorStyle.AFFILIATIVE.value, 0.5)
        + (0.15 if frame.bid.value in ("play", "connection") else 0.0)
    )
    if propensity >= 0.38 and frame.input_type in (InputType.TOPIC, InputType.CREATIVE_TOPIC, InputType.GOOD_NEWS, InputType.SHORT_REPLY, InputType.GREETING):
        material = frame.topic_tokens[0] if frame.topic_tokens else "当下的场景"
        return HumorPlan(
            style=HumorStyle.AFFILIATIVE,
            device="exaggeration",
            material=material,
            intensity=0.4,
            guidance=f"可以围绕「{material}」来一句轻巧的玩笑或夸张比喻，和对方一起笑，不针对任何人",
        )

    return None
