"""精确数学验证：每条公式注入已知值，输出必须等于手算结果。

这里不接受"大概对"——衰减、EMA、账本算术是关系生长的物理定律，
错一位，三十天后的关系就长歪了。
"""

from datetime import datetime, timedelta

import pytest

from relationshape.adaptation import AdaptationState
from relationshape.affect import MoodState
from relationshape.config import EngineConfig
from relationshape.identity import BigFive, CharacterIdentity
from relationshape.memory import MemoryBank
from relationshape.relationship import (
    Ledger,
    RelationshipCore,
    apply_absence,
    maybe_demote,
    milestone_due,
    record_promise,
    record_repair,
    record_rupture,
    record_session,
    record_substantive_turn,
    try_progress,
)
from relationshape.types import HumorStyle, Stage

T0 = datetime(2026, 1, 1, 9, 0)
approx = lambda x: pytest.approx(x, abs=1e-9)


# ---------------------------------------------------------------- 心境物理

def test_mood_decay_exact_one_half_life():
    """间隔正好一个半衰期(8h)：与基线的距离精确减半。"""
    m = MoodState(p=-0.6, a=0.7, d=-0.3, updated_at=T0.isoformat())
    m.decay_toward((0.4, 0.3, 0.1), T0 + timedelta(hours=8), half_life_hours=8.0)
    assert m.p == approx(0.4 + (-0.6 - 0.4) * 0.5)   # -0.1
    assert m.a == approx(0.3 + (0.7 - 0.3) * 0.5)    # 0.5
    assert m.d == approx(0.1 + (-0.3 - 0.1) * 0.5)   # -0.1


def test_mood_decay_exact_three_half_lives():
    m = MoodState(p=-0.6, a=0.3, d=0.1, updated_at=T0.isoformat())
    m.decay_toward((0.4, 0.3, 0.1), T0 + timedelta(hours=24), half_life_hours=8.0)
    assert m.p == approx(0.4 - 1.0 * 0.125)          # 0.275


def test_mood_first_touch_snaps_to_baseline():
    m = MoodState(p=-0.9, a=0.9, d=0.9, updated_at="")
    m.decay_toward((0.4, 0.3, 0.1), T0, half_life_hours=8.0)
    assert (m.p, m.a, m.d) == (approx(0.4), approx(0.3), approx(0.1))


def test_mood_impulse_gain_and_clamp():
    m = MoodState(p=0.0, a=0.0, d=0.0)
    m.impulse(0.6, -0.4, 0.2)            # gain=0.35
    assert m.p == approx(0.21) and m.a == approx(-0.14) and m.d == approx(0.07)
    m2 = MoodState(p=0.9, a=0.0, d=0.0)
    m2.impulse(1.0, 0.0, 0.0)            # 0.9+0.35 → 钳到 1.0
    assert m2.p == approx(1.0)


def test_bigfive_to_baseline_formula():
    identity = CharacterIdentity(traits=BigFive(0.8, 0.65, 0.7, 0.85, 0.3))
    p, a, d = identity.baseline_mood()
    assert p == approx(0.10 + 0.30 * 0.7 + 0.25 * 0.85 - 0.35 * 0.3)   # 0.4175
    assert a == approx(0.05 + 0.30 * 0.7 + 0.15 * 0.8)                  # 0.38
    assert d == approx(0.20 * 0.65 + 0.15 * 0.7 - 0.25 * 0.3)           # 0.16


# ---------------------------------------------------------------- 记忆物理

def test_salience_formula_exact():
    bank = MemoryBank()
    assert bank.add_episode("a", 0.5, 0.5, T0).salience == approx(0.65)   # .3+.2+.15
    assert bank.add_episode("b", -1.0, 1.0, T0).salience == approx(1.0)   # 钳顶
    assert bank.add_episode("c", 0.0, 0.1, T0).salience == approx(0.33)


def test_forgetting_threshold_exact_boundary():
    """salience .33 在 60 天/半衰期14 天时 = 0.0169 < 0.05 → 剪掉；
    复习 2 次 → 有效半衰期 ×2.6 → 0.105 ≥ 0.05 → 保留。"""
    bank = MemoryBank()
    weak = bank.add_episode("路过便利店", 0.0, 0.1, T0)
    strong = bank.add_episode("钢琴比赛第一名", 0.0, 0.1, T0)
    strong.recall_count = 2
    bank.decay_and_prune(T0 + timedelta(days=60), 14.0, 0.05, 400)
    texts = [e.text for e in bank.episodes]
    assert weak.text not in texts and strong.text in texts


def test_episodic_cap_keeps_newest():
    bank = MemoryBank()
    for i in range(10):
        bank.add_episode(f"事件{i}", 0.9, 0.9, T0 + timedelta(hours=i))
    bank.decay_and_prune(T0 + timedelta(hours=10), 14.0, 0.05, cap=3)
    assert [e.text for e in bank.episodes] == ["事件7", "事件8", "事件9"]


# ---------------------------------------------------------------- 账本算术

def test_ledger_arithmetic_exact():
    led = Ledger()
    record_substantive_turn(led, disclosure_depth=3)
    assert led.trust == approx(0.5 + 1.2 * 3)        # 4.1
    assert led.closeness == approx(0.4 + 0.8 * 3)    # 2.8
    assert (led.disclosures, led.deep_disclosures, led.substantive_turns, led.bids_toward) == (1, 1, 1, 1)
    record_session(led)
    assert led.trust == approx(4.4) and led.closeness == approx(3.8)
    record_rupture(led)
    assert led.trust == approx(0.4) and led.ruptures_open == 1
    record_repair(led)
    assert led.trust == approx(6.4) and (led.ruptures_open, led.ruptures_repaired) == (0, 1)
    record_promise(led, kept=True)
    assert led.trust == approx(12.4) and led.promises_kept == 1
    record_promise(led, kept=False)
    assert led.trust == approx(4.4) and led.promises_broken == 1


def test_absence_decay_exact():
    cfg = EngineConfig()   # grace=7, half=45
    led = Ledger(closeness=50.0)
    apply_absence(led, gap_days=52.0, cfg=cfg)       # over=45 → ×0.5
    assert led.closeness == approx(25.0)
    led2 = Ledger(closeness=50.0)
    apply_absence(led2, gap_days=7.0, cfg=cfg)       # 宽限期内不衰减
    assert led2.closeness == approx(50.0)


# ---------------------------------------------------------------- 阶段门：缺一不可

def _qualified_for_familiar():
    core = RelationshipCore(stage=Stage.ACQUAINTANCE, first_met=(T0 - timedelta(days=10)).isoformat(), sessions=6)
    led = Ledger(trust=20, closeness=10, substantive_turns=25, disclosures=3, deep_disclosures=0, bids_toward=25)
    return core, led


def test_stage_gate_each_condition_blocks():
    cfg = EngineConfig()
    # 全满足 → 晋升
    core, led = _qualified_for_familiar()
    assert try_progress(core, led, culture_size=0, now=T0, cfg=cfg) == Stage.FAMILIAR
    # 缺时间
    core, led = _qualified_for_familiar()
    core.first_met = (T0 - timedelta(days=4)).isoformat()
    assert try_progress(core, led, 0, T0, cfg) is None
    # 缺会话数
    core, led = _qualified_for_familiar()
    core.sessions = 4
    assert try_progress(core, led, 0, T0, cfg) is None
    # 缺实质轮
    core, led = _qualified_for_familiar()
    led.substantive_turns = 19
    assert try_progress(core, led, 0, T0, cfg) is None
    # 缺信任
    core, led = _qualified_for_familiar()
    led.trust = 14.9
    assert try_progress(core, led, 0, T0, cfg) is None
    # 缺表露
    core, led = _qualified_for_familiar()
    led.disclosures = 1
    assert try_progress(core, led, 0, T0, cfg) is None
    # 有未修复裂痕
    core, led = _qualified_for_familiar()
    led.ruptures_open = 1
    assert try_progress(core, led, 0, T0, cfg) is None


def test_demote_steps_one_stage_and_resets():
    cfg = EngineConfig()
    core = RelationshipCore(stage=Stage.COMPANION, first_met=T0.isoformat())
    led = Ledger(ruptures_open=3)
    assert maybe_demote(core, led, cfg) == Stage.FAMILIAR
    assert core.stage == Stage.FAMILIAR and led.ruptures_open == 0
    assert maybe_demote(core, led, cfg) is None      # 已重置，不连降


def test_milestone_window_exact():
    cfg = EngineConfig()
    core = RelationshipCore(first_met=T0.isoformat())
    assert milestone_due(core, T0 + timedelta(days=6), cfg) is None
    assert milestone_due(core, T0 + timedelta(days=7), cfg) == 7
    assert milestone_due(core, T0 + timedelta(days=14), cfg) == 7    # 窗口最后一天
    assert milestone_due(core, T0 + timedelta(days=15), cfg) is None  # 过期不补
    core.milestones_done = [7]
    assert milestone_due(core, T0 + timedelta(days=10), cfg) is None  # 不重复
    assert milestone_due(core, T0 + timedelta(days=30), cfg) == 30


# ---------------------------------------------------------------- 适应层 EMA

def test_formality_ema_exact():
    ad = AdaptationState()                            # 初始 0.45
    ad.observe_user_style("您好，请问现在方便吗")        # 信号=1.0
    assert ad.formality == approx(0.45 + 0.15 * (1.0 - 0.45))   # 0.5325
    ad2 = AdaptationState()
    ad2.observe_user_style("哈哈好啦好啦呗")             # 信号=0.0
    assert ad2.formality == approx(0.45 + 0.15 * (0.0 - 0.45))  # 0.3825


def test_formality_bounded():
    ad = AdaptationState()
    for _ in range(60):
        ad.observe_user_style("哈哈好啦呗")
    assert ad.formality == approx(0.05)               # 下界钳制


def test_humor_receptivity_exact_and_window():
    ad = AdaptationState()
    ad.set_pending_humor(HumorStyle.AFFILIATIVE.value, "恐龙", turn=1)
    assert ad.react_to_pending_humor("哈哈哈笑死", turn=2) is True
    assert ad.humor_receptivity[HumorStyle.AFFILIATIVE.value] == approx(0.62)   # +0.12
    ad2 = AdaptationState()
    ad2.set_pending_humor(HumorStyle.WORDPLAY.value, "面条", turn=1)
    assert ad2.react_to_pending_humor("哦", turn=2) is False
    assert ad2.humor_receptivity[HumorStyle.WORDPLAY.value] == approx(0.42)     # -0.08
    ad3 = AdaptationState()
    ad3.set_pending_humor(HumorStyle.CALLBACK.value, "梗", turn=1)
    assert ad3.react_to_pending_humor("哈哈", turn=3) is None    # 隔了一轮 → 不计
    assert ad3.humor_receptivity[HumorStyle.CALLBACK.value] == approx(0.5)
    assert ad3.pending_humor is None                              # 且已清空
