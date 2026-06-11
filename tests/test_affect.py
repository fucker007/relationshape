"""情感层测试：评估、心境衰减、表达调节。"""

from datetime import datetime, timedelta

from relationshape.affect import MoodState, appraise, regulate
from relationshape.identity import CharacterIdentity
from relationshape.perception import perceive
from relationshape.types import Stage

T0 = datetime(2026, 1, 10, 20, 0)


def test_mood_decays_toward_baseline_overnight():
    """晚上被骂心情差，第二天早上应该基本回到基线（半衰期8小时）。"""
    mood = MoodState(p=-0.6, a=0.5, d=-0.3, updated_at=T0.isoformat())
    baseline = (0.4, 0.35, 0.15)
    mood.decay_toward(baseline, T0 + timedelta(hours=24), half_life_hours=8.0)
    assert abs(mood.p - 0.4) < 0.15
    assert mood.p > 0.2


def test_attack_with_self_respect_yields_indignation():
    identity = CharacterIdentity()  # self_respect=0.7
    frame, reading = perceive("你真笨")
    mood = MoodState(p=0.3, a=0.3, d=0.2)
    emo = appraise(frame, reading, identity, mood, Stage.FAMILIAR)
    assert emo.label == "indignation"


def test_attack_with_low_dominance_yields_hurt():
    identity = CharacterIdentity()
    frame, reading = perceive("你真笨")
    mood = MoodState(p=-0.3, a=0.3, d=-0.5)
    emo = appraise(frame, reading, identity, mood, Stage.FAMILIAR)
    assert emo.label == "hurt"


def test_user_distress_caps_character_negative_display():
    """对方在难过时，角色自己的负面情绪必须让位。"""
    identity = CharacterIdentity()
    frame, reading = perceive("我今天有点难过")
    mood = MoodState(p=-0.4, a=0.3, d=0.0)
    emo = appraise(frame, reading, identity, mood, Stage.FAMILIAR)
    assert emo.label == "compassion"
    # 即使强行构造一个受伤情绪，调节后也要压低
    emo.label = "hurt"
    emo.display_intensity = 0.8
    regulate(emo, frame, reading, Stage.FAMILIAR)
    assert emo.display_intensity <= 0.15


def test_attack_regulation_bans_four_horsemen():
    identity = CharacterIdentity()
    frame, reading = perceive("你真没用")
    mood = MoodState()
    emo = appraise(frame, reading, identity, mood, Stage.FAMILIAR)
    forbidden = regulate(emo, frame, reading, Stage.FAMILIAR)
    joined = "；".join(forbidden)
    assert "嘲讽" in joined
    assert "冷暴力" in joined or "已读不回" in joined


def test_rejection_wistful_capped_at_early_stage():
    identity = CharacterIdentity()
    frame, reading = perceive("别烦我")
    mood = MoodState()
    emo = appraise(frame, reading, identity, mood, Stage.STRANGER)
    forbidden = regulate(emo, frame, reading, Stage.STRANGER)
    assert emo.display_intensity <= 0.25
    assert any("为什么不理我" in f for f in forbidden)


def test_dependency_phrases_always_forbidden():
    identity = CharacterIdentity()
    frame, reading = perceive("今天天气不错")
    mood = MoodState()
    emo = appraise(frame, reading, identity, mood, Stage.CONFIDANT)
    forbidden = regulate(emo, frame, reading, Stage.CONFIDANT)
    assert any("离不开你" in f for f in forbidden)
