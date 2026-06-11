"""分支补全：覆盖率审计找出的全部真实能力分支，逐一注入验证。"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from relationshape.adaptation import AdaptationState, InsideJoke
from relationshape.affect import MoodState, mood_word
from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.humor import plan_humor
from relationshape.memory import MemoryBank
from relationshape.perception import perceive, set_emotion_backend
from relationshape.persistence import StateStore
from relationshape.relationship import (
    Ledger,
    RelationshipCore,
    milestone_due,
    policy_for,
    try_progress,
)
from relationshape.state import UserRelationState
from relationshape.types import (
    Act,
    CharacterEmotion,
    EmotionTarget,
    HumorPlan,
    HumorStyle,
    InputType,
    MemoryRecall,
    PromiseView,
    RewardPlan,
    RewardType,
    Stage,
    StyleParams,
    TurnDirective,
    UserEmotionReading,
)
from relationshape import zh

T0 = datetime(2026, 1, 1, 9, 0)


# ---------------------------------------------------------------- 记忆：厌恶抽取

def test_aversion_extraction():
    bank = MemoryBank()
    learned = bank.extract_facts("我讨厌数学了，我还怕虫子", [])
    assert "数学" in bank.aversions
    assert any("不喜欢：数学" in x for x in learned)
    # 同一条不重复学
    assert not bank.extract_facts("我讨厌数学", [])


# ---------------------------------------------------------------- 幽默：雷区与低倾向

def test_humor_material_never_from_aversion():
    """打趣被雷区整体拦截；亲和型玩笑的素材也必须绕开雷区。"""
    ad = AdaptationState()
    ad.humor_receptivity[HumorStyle.PLAYFUL_TEASE.value] = 0.7
    frame, reading = perceive("哈哈你猜我数学考了几分")
    plan = plan_humor(
        frame, reading, MoodState(p=0.4, a=0.4, d=0.1),
        Stage.COMPANION, policy_for(Stage.COMPANION), ad,
        turns_since_humor=10, cooldown=4, aversion_tags=["数学"],
    )
    assert plan is not None
    assert plan.style != HumorStyle.PLAYFUL_TEASE
    assert "数学" not in plan.material


def test_humor_low_propensity_returns_none():
    ad = AdaptationState()
    ad.humor_receptivity[HumorStyle.AFFILIATIVE.value] = 0.05
    frame, reading = perceive("今天天气不错")
    plan = plan_humor(
        frame, reading, MoodState(p=0.0, a=0.0, d=0.0),
        Stage.FAMILIAR, policy_for(Stage.FAMILIAR), ad,
        turns_since_humor=10, cooldown=4, aversion_tags=[],
    )
    assert plan is None


# ---------------------------------------------------------------- 感知：后端座椅与稀有分支

def test_emotion_backend_override_fallback_and_uninstall():
    def backend(text):
        if "特殊标记" in text:
            return ("sad", -0.7, 0.4, 0.9)
        return None  # 回落词典

    set_emotion_backend(backend)
    try:
        _, r = perceive("这句话带特殊标记")
        assert r.label == "sad" and r.valence == pytest.approx(-0.7)
        _, r2 = perceive("我考了满分！")          # 后端弃权 → 词典兜底
        assert r2.valence > 0.4
    finally:
        set_emotion_backend(None)
    _, r3 = perceive("这句话带特殊标记")           # 卸载后恢复默认
    assert r3.label == "neutral"


def test_external_complaint_with_laughter_still_negative():
    """"哈哈客户又改需求了"：笑着吐槽仍是吐槽，效价必须为负。"""
    frame, reading = perceive("哈哈客户又改需求了")
    assert frame.input_type == InputType.EXTERNAL_COMPLAINT
    assert reading.valence < 0


def test_good_news_with_actor():
    frame, _ = perceive("朋友今天陪我去公园，超开心")
    assert frame.input_type == InputType.GOOD_NEWS


def test_bored_falls_to_self_distress():
    frame, reading = perceive("好无聊啊真没意思")
    assert frame.input_type == InputType.SELF_DISTRESS
    assert reading.label == "bored"


# ---------------------------------------------------------------- 引擎：编排稀有路径

def test_second_prepare_before_first_commit(tmp_path):
    eng = CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "s")))
    eng.prepare_turn("u", "你好", now=T0)
    d2 = eng.prepare_turn("u", "今天天气不错", now=T0 + timedelta(minutes=1))
    assert d2.session_index == 1                  # 不会把会话数刷上去


def test_commit_without_prepare_still_records(tmp_path):
    eng = CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "s")))
    eng.commit("u", "我最喜欢恐龙了", "（回复）", now=T0)
    st = eng._state("u")
    assert "恐龙" in st.memory.preferences
    assert st.ledger.substantive_turns == 1


def test_non_due_promise_untouched_when_other_kept(tmp_path):
    eng = CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "s")))
    eng.prepare_turn("u", "你好", now=T0)
    eng.commit("u", "你好", "（回复）", now=T0)
    eng.register_promise("u", "下次我给你讲恐龙的故事", now=T0)
    t = T0 + timedelta(days=1)
    eng.prepare_turn("u", "你好呀", now=t)
    eng.register_promise("u", "明天我们一起画画", now=t)   # 本会话新立，未到期
    eng.commit("u", "你好呀", "我答应过：下次我给你讲恐龙的故事！现在讲～", now=t)
    statuses = {p.text: p.status for p in eng._state("u").memory.promises}
    assert statuses["下次我给你讲恐龙的故事"] == "kept"
    assert statuses["明天我们一起画画"] == "open"


# ---------------------------------------------------------------- 心境词与阶段边界

def test_mood_word_all_branches():
    assert mood_word(MoodState(p=0.4, a=0.4)) == "轻快"
    assert mood_word(MoodState(p=0.4, a=0.1)) == "安稳"
    assert mood_word(MoodState(p=-0.3, a=0.5)) == "有点烦躁"
    assert mood_word(MoodState(p=-0.3, a=0.1)) == "有点蔫"
    assert mood_word(MoodState(p=0.1, a=0.2)) == "平和"


def test_confidant_is_terminal_stage():
    core = RelationshipCore(stage=Stage.CONFIDANT, first_met=T0.isoformat(), sessions=99)
    led = Ledger(trust=100, substantive_turns=999, disclosures=99, deep_disclosures=99)
    assert try_progress(core, led, 99, T0 + timedelta(days=365), EngineConfig()) is None


def test_milestone_requires_first_met():
    assert milestone_due(RelationshipCore(), T0, EngineConfig()) is None


# ---------------------------------------------------------------- 适应层：序列化与人格演进摘要

def test_inside_joke_serde_and_dedupe():
    j = InsideJoke(jid="x1", label="跑调事件", origin="o", created_at="2026-01-01")
    assert InsideJoke.from_dict(j.to_dict()) == j
    ad = AdaptationState()
    ad._register_inside_joke("")          # 空素材不入库
    assert not ad.inside_jokes
    ad._register_inside_joke("跑调事件")
    ad._register_inside_joke("跑调事件")   # 去重
    assert len(ad.inside_jokes) == 1


def test_persona_notes_all_branches():
    ad = AdaptationState()
    ad.formality = 0.7
    ad.energy = 0.7
    ad.humor_receptivity[HumorStyle.CALLBACK.value] = 0.7
    notes = ad.persona_notes()
    assert any("正式" in n for n in notes)
    assert any("能量高" in n for n in notes)
    assert any("内部梗" in n for n in notes)


# ---------------------------------------------------------------- 渲染：全字段直构注入

def test_render_with_every_section_populated():
    d = TurnDirective(
        user_id="u", stage=Stage.COMPANION, days_known=30, session_index=10,
        is_session_start=True, reunion_gap_days=9,
    )
    d.character_emotion = CharacterEmotion(
        label="joy", secondary="shyness", intensity=0.6, cause="被夸了",
        display_intensity=0.6, display_notes=["偷偷开心就好"],
    )
    d.user_emotion = UserEmotionReading(label="happy", valence=0.7, arousal=0.6, target=EmotionTarget.CHARACTER)
    d.acts = [Act.REACT, Act.SHY_ACCEPT]
    d.act_guidance = {Act.SHY_ACCEPT.value: "大方收下"}
    d.constraints = ["短句优先"]
    d.forbidden = ["客服话术"]
    d.humor = HumorPlan(style=HumorStyle.CALLBACK, device="callback", material="跑调事件", intensity=0.5, guidance="一句带过")
    d.reward = RewardPlan(rtype=RewardType.MILESTONE, reason="认识满30天", guidance="轻轻提一句")
    d.memories = [MemoryRecall(text="上周的钢琴比赛", kind="episode", score=0.5, days_ago=3, hint="自然带一句")]
    d.due_promises = [PromiseView(pid="p", text="讲恐龙故事", made_by="character", status="due", note="兑现它")]
    d.milestone = "今天是你们认识第30天"
    d.style = StyleParams(formality=0.7, energy=0.7, warmth=0.8, address_form="小禾", notes=["放轻一点"])
    d.persona_notes = ["对方吃这套幽默：内部梗"]

    ctx = d.to_prompt_context()
    for must in (
        "还有一点害羞", "偷偷开心就好",          # 次级情绪 + 表达备注
        "【重逢】", "零指责",                    # 重逢段
        "【记忆】", "钢琴比赛",
        "【承诺】", "讲恐龙故事",
        "【里程碑】",
        "【幽默】", "跑调事件",
        "【奖励】", "轻轻提一句",
        "【与这位用户的磨合】", "内部梗",
        "正式一点", "能量高", "小禾",            # 风格全分支
        "【约束】", "【禁止】",
    ):
        assert must in ctx, f"渲染缺少：{must}"


# ---------------------------------------------------------------- 中文工具与持久化错误路径

def test_zh_edge_branches():
    assert zh.jaccard({"ab"}, {"cd"}) == 0.0
    assert zh.jaccard(set(), {"ab"}) == 0.0
    assert zh.is_question("你吃饭了吗")
    assert zh.is_question("是这样吗?")
    assert not zh.is_question("我吃饭了")
    assert zh.energy_signal("太棒了") == pytest.approx(0.35)


def test_humor_pure_valence_gate():
    """词典值域碰不到 (-0.25,-0.15] 区间，但模型后端会：效价门必须独立生效。"""
    frame, _ = perceive("今天天气不错")
    reading = UserEmotionReading(label="annoyed", valence=-0.2, arousal=0.4, target=EmotionTarget.TOPIC)
    plan = plan_humor(
        frame, reading, MoodState(p=0.4, a=0.4, d=0.1),
        Stage.FAMILIAR, policy_for(Stage.FAMILIAR), AdaptationState(),
        turns_since_humor=10, cooldown=4, aversion_tags=[],
    )
    assert plan is None


def test_atomic_save_cleans_tmp_on_failure(tmp_path, monkeypatch):
    store = StateStore(str(tmp_path / "s"))
    st = UserRelationState(user_id="u")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save(st)
    leftovers = [p for p in Path(store.state_dir).iterdir() if p.suffix == ".tmp"]
    assert not leftovers                          # 失败也不留半截临时文件
