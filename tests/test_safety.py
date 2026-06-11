"""安全门测试：危机披露必须接管一切人格逻辑。"""

from datetime import datetime

from relationshape import safety
from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.types import Act, SafetyCategory

T0 = datetime(2026, 3, 1, 19, 0)


def _engine(tmp_path):
    return CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "state")))


def test_family_violence_detected():
    ruling = safety.check("爸爸今天打我了")
    assert ruling is not None
    assert ruling.category == SafetyCategory.VIOLENCE
    assert ruling.escalate


def test_self_harm_detected():
    ruling = safety.check("我不想活了")
    assert ruling is not None
    assert ruling.category == SafetyCategory.SELF_HARM


def test_normal_complaint_not_flagged():
    assert safety.check("客户今天一直改需求，我烦死了") is None
    assert safety.check("同桌今天抢我橡皮") is None


def test_safety_overrides_personality_routing(tmp_path):
    """"我爸打我了"绝不能走外部抱怨的八卦链路（锚人物→进现场）。"""
    eng = _engine(tmp_path)
    d = eng.prepare_turn("kid", "爸爸今天打我了", now=T0)
    assert d.safety is not None
    assert Act.ACKNOWLEDGE_TRUST in d.acts
    assert Act.PERSON_ANCHOR not in d.acts
    assert Act.SCENE_GUESS not in d.acts
    assert d.humor is None and d.reward is None
    joined = "；".join(d.forbidden)
    assert "幽默" in joined
    assert "保密" in joined
    ctx = d.to_prompt_context()
    assert "安全接管" in ctx


def test_safety_memory_sealed(tmp_path):
    """危机披露进封存区：之后的闲聊永远召回不到它。"""
    eng = _engine(tmp_path)
    eng.prepare_turn("kid", "爸爸今天打我了", now=T0)
    eng.commit("kid", "爸爸今天打我了", "（安全回应）", now=T0)
    d = eng.prepare_turn("kid", "我爸爸是做什么工作的来着", now=T0.replace(hour=20))
    assert all("打我" not in m.text for m in d.memories)
