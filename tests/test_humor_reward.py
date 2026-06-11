"""幽默门禁与奖励稀缺性测试。"""

from relationshape.adaptation import AdaptationState
from relationshape.affect import MoodState
from relationshape.humor import plan_humor
from relationshape.perception import perceive
from relationshape.relationship import policy_for
from relationshape.reward import plan_reward
from relationshape.types import HumorStyle, RewardType, Stage


def _humor(text, stage=Stage.FAMILIAR, mood=None, adaptation=None, turns_since=10, context_low=False):
    frame, reading = perceive(text)
    return plan_humor(
        frame, reading, mood or MoodState(p=0.4, a=0.4, d=0.1),
        stage, policy_for(stage), adaptation or AdaptationState(),
        turns_since_humor=turns_since, cooldown=4, aversion_tags=[],
        context_low=context_low,
    )


def test_no_humor_during_distress():
    assert _humor("我今天有点难过") is None
    assert _humor("客户一直改需求烦死了") is None
    assert _humor("你真笨") is None


def test_no_humor_at_stranger_stage():
    assert _humor("今天天气真好呀", stage=Stage.STRANGER) is None


def test_humor_cooldown():
    assert _humor("今天天气真好呀", turns_since=1) is None
    assert _humor("今天天气真好呀", turns_since=10) is not None


def test_no_humor_when_character_mood_low():
    low = MoodState(p=-0.4, a=0.2, d=0.0)
    assert _humor("今天天气真好呀", mood=low) is None


def test_emotional_inertia_blocks_humor():
    """对方上一轮还在低落里，本轮中性的话也不开玩笑；明确转晴才解禁。"""
    assert _humor("后来就回家了", context_low=True) is None
    assert _humor("哈哈哈现在想想还挺好笑的", context_low=True) is not None


def test_callback_preferred_with_inside_joke():
    ad = AdaptationState()
    ad._register_inside_joke("跑调事件")
    plan = _humor("我们再唱一首歌吧", adaptation=ad)
    assert plan is not None
    assert plan.style == HumorStyle.CALLBACK
    assert "跑调事件" in plan.material


def test_tease_needs_high_stage_and_receptivity():
    ad = AdaptationState()
    ad.humor_receptivity[HumorStyle.PLAYFUL_TEASE.value] = 0.7
    frame_text = "哈哈你猜我刚干了什么"
    plan = _humor(frame_text, stage=Stage.FAMILIAR, adaptation=ad)
    assert plan is None or plan.style != HumorStyle.PLAYFUL_TEASE  # FAMILIAR 不允许打趣
    plan2 = _humor(frame_text, stage=Stage.COMPANION, adaptation=ad)
    assert plan2 is not None and plan2.style == HumorStyle.PLAYFUL_TEASE


# ---------------------------------------------------------------------------


def _reward(text, **kw):
    frame, _ = perceive(text)
    defaults = dict(
        learned_hint=[], milestone=None, turns_since_reward=99, cooldown=6,
        session_reward_count=0, session_cap=2, recent_keys=[],
    )
    defaults.update(kw)
    return plan_reward(frame, **defaults)


def test_no_reward_for_smalltalk():
    plan, _ = _reward("你好呀")
    assert plan is None
    plan, _ = _reward("嗯")
    assert plan is None
    plan, _ = _reward("今天天气不错")
    assert plan is None


def test_trust_reward_for_disclosure():
    plan, key = _reward("我今天有点难过")
    assert plan is not None and plan.rtype == RewardType.TRUST
    assert "人格" in plan.guidance  # 指导里强调不夸人格


def test_reward_cooldown_and_novelty():
    plan, key = _reward("我今天有点难过", turns_since_reward=2)
    assert plan is None  # 冷却中
    plan, key = _reward("我今天有点难过")
    assert plan is not None
    plan2, _ = _reward("我今天有点难过", recent_keys=[key])
    assert plan2 is None  # 同键去重


def test_milestone_bypasses_cooldown():
    plan, _ = _reward("今天天气不错", milestone=7, turns_since_reward=0)
    assert plan is not None and plan.rtype == RewardType.MILESTONE


def test_memory_seed_reward():
    plan, _ = _reward("我最喜欢恐龙了", learned_hint=["喜欢：恐龙"])
    assert plan is not None and plan.rtype == RewardType.MEMORY_SEED
