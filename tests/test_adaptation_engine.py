"""人格演进与引擎端到端测试。"""

from datetime import datetime, timedelta

from relationshape.adaptation import AdaptationState
from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.types import Act, HumorStyle, Stage

T0 = datetime(2026, 1, 1, 9, 0)


def _engine(tmp_path) -> CompanionEngine:
    return CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "state")))


# ---------------------------------------------------------------- 适应层单元


def test_style_converges_to_casual_user():
    ad = AdaptationState()
    start = ad.formality
    for _ in range(8):
        ad.observe_user_style("哈哈好啦好啦，超好玩的呗")
    assert ad.formality < start


def test_humor_receptivity_learning():
    ad = AdaptationState()
    ad.set_pending_humor(HumorStyle.AFFILIATIVE.value, "恐龙", turn=1)
    assert ad.react_to_pending_humor("哈哈哈笑死我了", turn=2) is True
    assert ad.humor_receptivity[HumorStyle.AFFILIATIVE.value] > 0.5
    assert any("恐龙" in j.label for j in ad.inside_jokes)  # 笑过的梗成为内部梗

    ad2 = AdaptationState()
    ad2.set_pending_humor(HumorStyle.WORDPLAY.value, "面条", turn=1)
    assert ad2.react_to_pending_humor("哦", turn=2) is False
    assert ad2.humor_receptivity[HumorStyle.WORDPLAY.value] < 0.5
    assert not ad2.inside_jokes


def test_address_form_learned():
    ad = AdaptationState()
    assert ad.maybe_learn_address("你可以叫我小禾") == "小禾"
    assert ad.culture_size() >= 1


# ---------------------------------------------------------------- 引擎端到端


def test_per_user_isolation(tmp_path):
    """A 用户的攻击不能污染 B 用户看到的心境与关系。"""
    eng = _engine(tmp_path)
    eng.prepare_turn("alice", "你真笨", now=T0)
    eng.commit("alice", "你真笨", "（回应）", now=T0)
    d_bob = eng.prepare_turn("bob", "你好呀", now=T0 + timedelta(minutes=1))
    assert d_bob.mood[0] > 0  # bob 看到的心境是基线，不带 alice 留下的低落
    assert eng.snapshot("bob")["ruptures_open"] == 0
    assert eng.snapshot("alice")["ruptures_open"] == 1


def test_state_survives_restart(tmp_path):
    """换一个引擎实例（模拟重启），关系记忆与阶段原样恢复。"""
    cfg = EngineConfig(state_dir=str(tmp_path / "state"))
    eng1 = CompanionEngine(config=cfg)
    eng1.prepare_turn("u", "我最喜欢恐龙了，我叫小禾", now=T0)
    eng1.commit("u", "我最喜欢恐龙了，我叫小禾", "（回复）", now=T0)

    eng2 = CompanionEngine(config=cfg)
    snap = eng2.snapshot("u")
    assert "恐龙" in snap["preferences"]
    d = eng2.prepare_turn("u", "我们聊聊恐龙吧", now=T0 + timedelta(days=1))
    assert any("恐龙" in m.text for m in d.memories)


def test_memory_grounds_the_answer(tmp_path):
    """"根据用户的记忆回答"：上周提过的事，这周问起能召回。"""
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "我下周要参加钢琴比赛，好紧张", now=T0)
    eng.commit("u", "我下周要参加钢琴比赛，好紧张", "（回复）", now=T0)
    d = eng.prepare_turn("u", "钢琴的事有进展啦", now=T0 + timedelta(days=6))
    assert any("钢琴" in m.text for m in d.memories)
    ctx = d.to_prompt_context()
    assert "钢琴" in ctx


def test_greeting_recalls_recent_highlight(tmp_path):
    """开机问候能主动惦记最近的大事，而不是干巴巴说你好。"""
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "我明天要钢琴比赛了，好紧张", now=T0)
    eng.commit("u", "我明天要钢琴比赛了，好紧张", "（回复）", now=T0)
    d = eng.prepare_turn("u", "你好呀", now=T0 + timedelta(days=2))
    assert any("钢琴" in m.text for m in d.memories)
    assert Act.REMEMBER in d.acts


def test_promise_lifecycle_end_to_end(tmp_path):
    """承诺：许下 → 下次会话到期被提起 → 兑现入账。"""
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "我最喜欢恐龙了", now=T0)
    eng.commit("u", "我最喜欢恐龙了", "知道啦！下次我给你讲恐龙的故事", now=T0)
    assert eng.snapshot("u")["promises"][0][1] == "open"

    t1 = T0 + timedelta(days=1)
    d = eng.prepare_turn("u", "你好呀", now=t1)
    assert d.due_promises and "恐龙" in d.due_promises[0].text
    assert d.acts[0] == Act.REMEMBER
    eng.commit("u", "你好呀", "我记得答应过你：下次我给你讲恐龙的故事！现在就讲～", now=t1)
    snap = eng.snapshot("u")
    assert snap["promises"][0][1] == "kept"
    assert snap["trust"] > 0


def test_hook_continuity_and_pollution_rules(tmp_path):
    """短答接上一轮线头；被拒绝后线头清空，不死缠。"""
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "我想做一个会飞的机器人", now=T0)
    eng.commit("u", "我想做一个会飞的机器人", "（回复）", now=T0)
    t = T0 + timedelta(minutes=2)
    d = eng.prepare_turn("u", "嗯", now=t)
    assert any("机器人" in g for g in d.act_guidance.values())
    eng.commit("u", "嗯", "（回复）", now=t)

    t2 = t + timedelta(minutes=2)
    eng.prepare_turn("u", "别烦我，我想自己待会", now=t2)
    eng.commit("u", "别烦我，我想自己待会", "（收住）", now=t2)
    t3 = t2 + timedelta(minutes=10)
    d3 = eng.prepare_turn("u", "嗯", now=t3)
    assert all("机器人" not in g for g in d3.act_guidance.values())


def test_persona_notes_reflect_evolution(tmp_path):
    """人格演进可见：磨合出的语气和幽默偏好会写进指令。"""
    eng = _engine(tmp_path)
    t = T0
    for _ in range(10):
        eng.prepare_turn("u", "哈哈今天也超好玩的呗", now=t)
        eng.commit("u", "哈哈今天也超好玩的呗", "（回复）", now=t)
        t += timedelta(minutes=2)
    d = eng.prepare_turn("u", "今天天气不错", now=t)
    assert any("随意" in n or "轻松" in n for n in d.persona_notes)


def test_prompt_context_renders_core_sections(tmp_path):
    eng = _engine(tmp_path)
    d = eng.prepare_turn("u", "我今天有点难过", now=T0)
    ctx = d.to_prompt_context()
    assert "【关系】" in ctx
    assert "【你此刻的情绪】" in ctx
    assert "【回应形状】" in ctx
    assert "【禁止】" in ctx
    assert "心疼" in ctx  # 角色情绪被翻译成中文
