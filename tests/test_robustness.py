"""鲁棒性注入：异常与边角输入下，引擎必须不崩、产出合法指令。"""

from datetime import datetime, timedelta

from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.perception import perceive
from relationshape.types import EmotionTarget, InputType

T0 = datetime(2026, 1, 1, 9, 0)

WEIRD_INPUTS = [
    "",
    " ",
    "👍👍",
    "～！@#￥%……&*（）",
    "hello how are you doing today",
    "嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯",
    "我" * 500,
    "今天我们去公园玩然后又去吃了好吃的。" * 200,   # 超长输入
    "我好难过哈哈",                                  # 冲突信号
    "1234567890",
]


def test_perceive_never_crashes_and_outputs_valid_members():
    for text in WEIRD_INPUTS:
        frame, reading = perceive(text)
        assert isinstance(frame.input_type, InputType)
        assert isinstance(frame.target, EmotionTarget)
        assert -1.0 <= reading.valence <= 1.0
        assert 0.0 <= reading.arousal <= 1.0


def test_engine_full_turn_on_weird_inputs(tmp_path):
    eng = CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "s")))
    t = T0
    for text in WEIRD_INPUTS:
        d = eng.prepare_turn("u", text, now=t)
        assert d.acts, f"{text[:20]!r} 产出空动作链"
        ctx = d.to_prompt_context()
        assert "【" in ctx                       # 渲染成功
        eng.commit("u", text, "（回复）", now=t)
        t += timedelta(minutes=1)


def test_conflicting_signal_sad_with_laughter():
    """"我好难过哈哈"：难过词权重高于笑声，按低落处理（宁可放轻不可开玩笑）。"""
    frame, reading = perceive("我好难过哈哈")
    assert frame.input_type == InputType.SELF_DISTRESS
    assert reading.valence < 0


def test_empty_input_is_low_pressure_short_reply():
    frame, _ = perceive("")
    assert frame.input_type == InputType.SHORT_REPLY
    assert not frame.substantive


def test_default_clock_path(tmp_path):
    """不传 now 时走真实时钟，不崩。"""
    eng = CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "s")))
    d = eng.prepare_turn("u", "你好")
    eng.commit("u", "你好", "（回复）")
    assert d.session_index == 1
