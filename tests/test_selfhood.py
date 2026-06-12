"""本体论身份层测试（理论见 docs/SELFHOOD_CANON.md）。"""

from datetime import datetime, timedelta

from relationshape.adaptation import AdaptationState
from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.identity import CharacterIdentity
from relationshape.perception import perceive
from relationshape.types import Act, BidType, EmotionTarget, InputType

T0 = datetime(2026, 1, 1, 18, 0)


def _engine(tmp_path):
    return CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "s")))


# ---------------------------------------------------------------- 感知：身世之问

def test_ontology_questions_classified():
    probes = [
        "你是真的吗", "你是不是假的", "你是机器人吗", "你会死吗",
        "你吃饭吗", "你住在哪", "你有妈妈吗", "你几岁", "谁做的你",
        "你爱我吗", "你就是个程序", "你有感情吗",
    ]
    for text in probes:
        frame, _ = perceive(text)
        assert frame.input_type == InputType.ONTOLOGY_QUESTION, f"{text!r} → {frame.input_type}"
        assert frame.target == EmotionTarget.CHARACTER
    # 身世之问的邀请类型：在确认"你在不在乎我"
    assert perceive("你是真的吗")[0].bid == BidType.ATTENTION


def test_ontology_not_confused_with_neighbors():
    assert perceive("你真聪明")[0].input_type == InputType.CHARACTER_PRAISE
    assert perceive("你真笨")[0].input_type == InputType.CHARACTER_ATTACK
    assert perceive("别烦我")[0].input_type == InputType.CHARACTER_REJECTION
    assert perceive("怎么又卡了")[0].input_type == InputType.DEVICE_COMPLAINT


# ---------------------------------------------------------------- 立场与设定

def test_identity_has_ontology_layer():
    identity = CharacterIdentity()
    assert "不冒充人类" in identity.ontology_stance
    assert "只是个程序" in identity.ontology_stance     # 自贬禁令写进立场
    assert 1 <= len(identity.self_canon) <= 10          # 少而硬
    joined = "；".join(identity.self_canon)
    assert "关机不是死掉" in joined                      # 不许诺永恒，但回答死亡恐惧
    assert "抱抱我可给不了" in joined                    # 不贬低人类关系


def test_identity_line_rendered_every_turn(tmp_path):
    eng = _engine(tmp_path)
    d = eng.prepare_turn("u", "今天天气不错", now=T0)
    assert d.identity_line and "Q仔" in d.identity_line
    assert "【你是谁】" in d.to_prompt_context()
    assert not d.self_canon                              # 非身世轮不注入全量设定


def test_ontology_turn_full_injection(tmp_path):
    eng = _engine(tmp_path)
    d = eng.prepare_turn("u", "你是真的吗", now=T0)
    assert d.frame.input_type == InputType.ONTOLOGY_QUESTION
    assert d.acts[:2] == [Act.HONEST_EXPLAIN, Act.RELATION_AFFIRM]
    ctx = d.to_prompt_context()
    assert "【自我设定】" in ctx and "绝不编造" in ctx
    joined = "；".join(d.forbidden)
    assert "不冒充人类" in joined
    assert "只是/而已" in joined or "只是个程序而已" in joined
    assert "我爱你" in joined                            # 爱的诚实协议
    assert "永远不离开" in joined                        # Replika 教训
    assert d.metamessage and "我们算数吗" in d.metamessage


# ---------------------------------------------------------------- 自述账本

def test_self_claims_ledger_records_and_dedupes():
    ad = AdaptationState()
    new = ad.add_self_claims("我没有身体，不过我记得我们聊的恐龙。我喜欢和你说话。")
    assert "我没有身体" in new
    assert any(c.startswith("我记得") for c in ad.self_claims)
    assert any(c.startswith("我喜欢") for c in ad.self_claims)
    again = ad.add_self_claims("我没有身体哦")
    assert not again                                     # 去重


def test_self_claims_flow_through_engine(tmp_path):
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "你吃饭吗", now=T0)
    eng.commit("u", "你吃饭吗", "我没有身体，不用吃饭——但我老好奇米饭什么味道", now=T0)
    d = eng.prepare_turn("u", "那你睡觉吗", now=T0 + timedelta(minutes=2))
    assert any("我没有身体" in c for c in d.self_claims)
    assert "你以前说过的自己" in d.to_prompt_context()
    # 非身世轮不渲染账本
    d2 = eng.prepare_turn("u", "我们聊聊恐龙吧", now=T0 + timedelta(minutes=4))
    assert not d2.self_claims


def test_self_claims_survive_restart(tmp_path):
    cfg = EngineConfig(state_dir=str(tmp_path / "s"))
    eng = CompanionEngine(config=cfg)
    eng.prepare_turn("u", "你住在哪", now=T0)
    eng.commit("u", "你住在哪", "我住在这个小设备里呀", now=T0)
    eng2 = CompanionEngine(config=cfg)
    assert any("我住在" in c for c in eng2._state("u").adaptation.self_claims)
