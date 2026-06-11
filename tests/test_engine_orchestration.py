"""引擎编排边界：会话切分、冷却边界、钩子治理、承诺失约、配置生效。"""

from datetime import datetime, timedelta

from relationshape.adaptation import AdaptationState
from relationshape.affect import MoodState
from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.humor import plan_humor
from relationshape.perception import perceive
from relationshape.relationship import policy_for
from relationshape.reward import plan_reward
from relationshape.types import Stage

T0 = datetime(2026, 1, 1, 9, 0)


def _engine(tmp_path, **cfg_kw):
    return CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "s"), **cfg_kw))


# ---------------------------------------------------------------- 会话切分

def test_session_boundary_at_exact_gap(tmp_path):
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "你好", now=T0)
    eng.commit("u", "你好", "（回复）", now=T0)
    d = eng.prepare_turn("u", "今天天气不错", now=T0 + timedelta(minutes=29))
    assert not d.is_session_start and d.session_index == 1
    eng.commit("u", "今天天气不错", "（回复）", now=T0 + timedelta(minutes=29))
    d = eng.prepare_turn("u", "我们聊聊天文吧", now=T0 + timedelta(minutes=29 + 31))
    assert d.is_session_start and d.session_index == 2


def test_session_gap_configurable(tmp_path):
    eng = _engine(tmp_path, session_gap_minutes=5)
    eng.prepare_turn("u", "你好", now=T0)
    eng.commit("u", "你好", "（回复）", now=T0)
    d = eng.prepare_turn("u", "今天天气不错", now=T0 + timedelta(minutes=6))
    assert d.is_session_start and d.session_index == 2


def test_reunion_threshold(tmp_path):
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "你好", now=T0)
    eng.commit("u", "你好", "（回复）", now=T0)
    d = eng.prepare_turn("u", "你好呀", now=T0 + timedelta(days=6))
    assert d.reunion_gap_days == 0          # 6 天 < 阈值 7
    eng.commit("u", "你好呀", "（回复）", now=T0 + timedelta(days=6))
    d = eng.prepare_turn("u", "你好呀", now=T0 + timedelta(days=14))
    assert d.reunion_gap_days >= 7


# ---------------------------------------------------------------- 冷却边界

def test_humor_cooldown_boundary():
    frame, reading = perceive("今天天气真好呀")
    kw = dict(
        mood=MoodState(p=0.4, a=0.4, d=0.1), stage=Stage.FAMILIAR,
        policy=policy_for(Stage.FAMILIAR), adaptation=AdaptationState(),
        cooldown=4, aversion_tags=[],
    )
    assert plan_humor(frame, reading, turns_since_humor=3, **kw) is None      # 3 < 4
    assert plan_humor(frame, reading, turns_since_humor=4, **kw) is not None  # 边界恰好放行


def test_reward_cooldown_boundary():
    frame, _ = perceive("我最喜欢恐龙了")
    kw = dict(
        learned_hint=["喜欢：恐龙"], milestone=None, cooldown=6,
        session_reward_count=0, session_cap=2, recent_keys=[],
    )
    plan, _ = plan_reward(frame, turns_since_reward=5, **kw)
    assert plan is None
    plan, _ = plan_reward(frame, turns_since_reward=6, **kw)
    assert plan is not None


def test_reward_session_cap_resets_on_new_session(tmp_path):
    eng = _engine(tmp_path, reward_cooldown_turns=0, reward_session_cap=1)
    d = eng.prepare_turn("u", "我最喜欢恐龙了", now=T0)
    assert d.reward is not None
    eng.commit("u", "我最喜欢恐龙了", "（回复）", now=T0)
    # 同会话第二次：被会话上限挡住
    d = eng.prepare_turn("u", "我最喜欢画画了", now=T0 + timedelta(minutes=2))
    assert d.reward is None
    eng.commit("u", "我最喜欢画画了", "（回复）", now=T0 + timedelta(minutes=2))
    # 新会话：额度恢复
    d = eng.prepare_turn("u", "我最喜欢足球了", now=T0 + timedelta(hours=2))
    assert d.reward is not None


# ---------------------------------------------------------------- 钩子治理

def test_hook_set_only_by_user_life_topics(tmp_path):
    eng = _engine(tmp_path)
    t = T0
    eng.prepare_turn("u", "我考了满分！", now=t)
    eng.commit("u", "我考了满分！", "（回复）", now=t)
    assert eng._state("u").last_hook == "满分"
    # 设备抱怨与夸角色都不改写钩子
    for text in ("怎么又卡了", "你真聪明"):
        t += timedelta(minutes=2)
        eng.prepare_turn("u", text, now=t)
        eng.commit("u", text, "（回复）", now=t)
        assert eng._state("u").last_hook == "满分", f"{text!r} 污染了钩子"
    # 被推开：钩子清空
    t += timedelta(minutes=2)
    eng.prepare_turn("u", "别烦我", now=t)
    eng.commit("u", "别烦我", "（收住）", now=t)
    assert eng._state("u").last_hook is None


# ---------------------------------------------------------------- 承诺失约

def test_promise_missed_after_three_unfulfilled_surfaces(tmp_path):
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "你好", now=T0)
    eng.commit("u", "你好", "（回复）", now=T0)
    eng.register_promise("u", "下次我给你讲恐龙的故事", now=T0)

    t = T0 + timedelta(days=1)
    for i in range(3):
        d = eng.prepare_turn("u", f"我们聊聊画画吧，第{i}次", now=t)
        assert d.due_promises, "到期承诺未被注入指令"
        eng.commit("u", "我们聊聊画画吧", "（回复，没有兑现承诺）", now=t)
        t += timedelta(minutes=3)

    st = eng._state("u")
    promise = st.memory.promises[0]
    assert promise.status == "missed"
    assert st.ledger.promises_broken == 1
    assert any("承诺" in lesson for lesson in st.adaptation.lessons)
    # 失约后不再继续骚扰
    d = eng.prepare_turn("u", "继续聊画画", now=t)
    assert not d.due_promises


def test_promise_kept_via_register_api(tmp_path):
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "你好", now=T0)
    eng.commit("u", "你好", "（回复）", now=T0)
    eng.register_promise("u", "下次我给你讲恐龙的故事", now=T0)
    t = T0 + timedelta(days=1)
    eng.prepare_turn("u", "你好呀", now=t)
    eng.commit("u", "你好呀", "我答应过你：下次我给你讲恐龙的故事！现在讲～", now=t)
    st = eng._state("u")
    assert st.memory.promises[0].status == "kept"
    assert st.ledger.promises_kept == 1


# ---------------------------------------------------------------- 杂项编排

def test_last_user_valence_persisted_for_inertia(tmp_path):
    cfg_dir = tmp_path / "s"
    eng = CompanionEngine(config=EngineConfig(state_dir=str(cfg_dir)))
    eng.prepare_turn("u", "我今天有点难过", now=T0)
    eng.commit("u", "我今天有点难过", "（回复）", now=T0)
    eng2 = CompanionEngine(config=EngineConfig(state_dir=str(cfg_dir)))
    assert eng2._state("u").last_user_valence < -0.3   # 重启后情绪惯性仍然生效


def test_snapshot_has_complete_keys(tmp_path):
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "你好", now=T0)
    eng.commit("u", "你好", "（回复）", now=T0)
    snap = eng.snapshot("u")
    for key in ("stage", "sessions", "trust", "closeness", "substantive_turns",
                "disclosures", "deep_disclosures", "ruptures_open", "ruptures_repaired",
                "mood", "culture", "inside_jokes", "preferences", "promises", "lessons"):
        assert key in snap, f"snapshot 缺 {key}"
