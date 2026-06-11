"""关系生长测试：时间 × 互动 × 信任共同决定阶段，刷轮次没用。"""

from datetime import datetime, timedelta

from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.types import Act, InputType, Stage

T0 = datetime(2026, 1, 1, 9, 0)

# 一组有实质内容、含自我表露的日常对话
SESSION_LINES = [
    "我今天在学校学了画画，我最喜欢画恐龙了",
    "老师今天夸了我的画",
    "我今天有点累，写作业写到好晚",
    "我想做一个会飞的机器人",
]


def _engine(tmp_path) -> CompanionEngine:
    return CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "state")))


def _run_session(eng: CompanionEngine, user: str, day_time: datetime, lines=SESSION_LINES):
    t = day_time
    for line in lines:
        eng.prepare_turn(user, line, now=t)
        eng.commit(user, line, "（回复）", now=t)
        t += timedelta(minutes=2)
    return t


def test_starts_as_stranger(tmp_path):
    eng = _engine(tmp_path)
    d = eng.prepare_turn("u", "你好", now=T0)
    assert d.stage == Stage.STRANGER
    assert d.days_known == 0
    assert d.is_session_start


def test_growth_requires_time_not_just_turns(tmp_path):
    """同一天狂聊几十轮，也到不了熟悉阶段（时间门槛）。"""
    eng = _engine(tmp_path)
    t = T0
    for i in range(10):  # 同一天 10 个会话
        _run_session(eng, "u", t)
        t += timedelta(hours=1)
    snap = eng.snapshot("u")
    assert snap["substantive_turns"] >= 20
    assert snap["stage"] in (Stage.STRANGER.value, Stage.ACQUAINTANCE.value)
    assert snap["stage"] != Stage.FAMILIAR.value


def test_stage_grows_over_days(tmp_path):
    """日积月累的实质互动让关系自然推进：陌生 → 相识 → 熟悉。"""
    eng = _engine(tmp_path)
    for day in range(8):
        _run_session(eng, "u", T0 + timedelta(days=day))
    snap = eng.snapshot("u")
    assert snap["stage"] == Stage.FAMILIAR.value
    assert snap["trust"] >= 15


def test_reaches_companion_with_culture_and_depth(tmp_path):
    """同伴阶段需要：更长时间 + 深度表露 + 共同文化（称呼/内部梗）。"""
    eng = _engine(tmp_path)
    lines = SESSION_LINES + ["其实我从来没跟别人说过，我有点怕黑"]
    t = T0
    eng.prepare_turn("u", "你可以叫我小禾", now=t)
    eng.commit("u", "你可以叫我小禾", "（回复）", now=t)
    for day in range(22):
        _run_session(eng, "u", T0 + timedelta(days=day, hours=18), lines=lines)
    snap = eng.snapshot("u")
    assert snap["culture"] >= 1
    assert snap["deep_disclosures"] >= 1
    assert snap["stage"] == Stage.COMPANION.value


def test_milestone_celebrated_once(tmp_path):
    eng = _engine(tmp_path)
    for day in (0, 2, 4):
        _run_session(eng, "u", T0 + timedelta(days=day))
    d = eng.prepare_turn("u", "我们继续聊画画吧", now=T0 + timedelta(days=7, hours=10))
    assert d.milestone is not None and "7" in d.milestone
    assert Act.CELEBRATE_MILESTONE in d.acts
    eng.commit("u", "我们继续聊画画吧", "（回复）", now=T0 + timedelta(days=7, hours=10))
    d2 = eng.prepare_turn("u", "再聊聊恐龙", now=T0 + timedelta(days=7, hours=11))
    assert d2.milestone is None  # 只庆祝一次


def test_reunion_after_long_gap_no_guilt(tmp_path):
    """久别重逢：暖，但指令里明确零指责。"""
    eng = _engine(tmp_path)
    _run_session(eng, "u", T0)
    d = eng.prepare_turn("u", "你好呀", now=T0 + timedelta(days=12))
    assert d.reunion_gap_days >= 7
    assert Act.REUNION_WARMTH in d.acts
    guidance = d.act_guidance[Act.REUNION_WARMTH.value]
    assert "指责" in guidance or "愧疚" in guidance
    ctx = d.to_prompt_context()
    assert "怎么才来" in ctx  # 禁令出现在上下文里


def test_rupture_blocks_progress_and_repair_restores(tmp_path):
    """未修复的裂痕挡住晋升；修复后信任反而更高。"""
    eng = _engine(tmp_path)
    _run_session(eng, "u", T0)
    eng.prepare_turn("u", "你真笨", now=T0 + timedelta(hours=2))
    eng.commit("u", "你真笨", "（站直回应）", now=T0 + timedelta(hours=2))
    snap = eng.snapshot("u")
    assert snap["ruptures_open"] == 1
    trust_after_attack = snap["trust"]
    eng.prepare_turn("u", "逗你的啦，你别生气", now=T0 + timedelta(hours=2, minutes=5))
    eng.commit("u", "逗你的啦，你别生气", "（接受安抚）", now=T0 + timedelta(hours=2, minutes=5))
    snap2 = eng.snapshot("u")
    assert snap2["ruptures_open"] == 0
    assert snap2["ruptures_repaired"] == 1
    assert snap2["trust"] > trust_after_attack


def test_repeated_unrepaired_ruptures_demote(tmp_path):
    eng = _engine(tmp_path)
    for day in range(8):
        _run_session(eng, "u", T0 + timedelta(days=day))
    assert eng.snapshot("u")["stage"] == Stage.FAMILIAR.value
    t = T0 + timedelta(days=9)
    for i in range(3):
        eng.prepare_turn("u", "你真没用", now=t)
        eng.commit("u", "你真没用", "（回应）", now=t)
        t += timedelta(minutes=3)
    assert eng.snapshot("u")["stage"] == Stage.ACQUAINTANCE.value
