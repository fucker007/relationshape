"""全分类矩阵：15 种输入类型 × 5 种情绪指向 × 邀请类型 × 表露深度，
每个类别注入多条数据逐一验证；外加枚举完备性（防渲染 KeyError）
与状态序列化逐字段相等。"""

from datetime import datetime, timedelta

from relationshape.affect import _EMOTION_PAD, MoodState, appraise
from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.identity import CharacterIdentity
from relationshape.perception import perceive
from relationshape.persistence import StateStore
from relationshape.prompting import _ACT_ZH, _EMO_ZH, _STYLE_HUMOR_ZH, _USER_EMO_ZH
from relationshape.types import Act, BidType, EmotionTarget, HumorStyle, InputType, Stage

T0 = datetime(2026, 1, 1, 9, 0)

# 每种输入类型 ≥2 条注入样本（覆盖成人/儿童两种语料风格）
INPUT_PROBES: dict[InputType, list[str]] = {
    InputType.GREETING: ["你好", "早上好", "在吗"],
    InputType.FAREWELL: ["拜拜", "我去睡觉啦，晚安"],
    InputType.SHORT_REPLY: ["嗯", "好的", "继续"],
    InputType.GOOD_NEWS: ["我考了满分！", "我赢了比赛，太开心了"],
    InputType.EXTERNAL_COMPLAINT: ["老板今天又催我加班，烦死了", "同桌抢我橡皮，气死我了", "老师今天批评我了，好难受"],
    InputType.SELF_DISTRESS: ["我今天有点难过", "最近压力好大，我好累"],
    InputType.SELF_BLAME: ["我好笨，什么都做不好", "都怪我，我真没用"],
    InputType.CHARACTER_PRAISE: ["你真聪明", "你好厉害呀"],
    InputType.CHARACTER_ATTACK: ["你真笨", "你还不如豆包聪明", "笨蛋"],
    InputType.CHARACTER_REASSURANCE: ["你别难过了", "逗你的啦，你别生气"],
    InputType.CHARACTER_REJECTION: ["别烦我", "走开", "我想自己待会"],
    InputType.DEVICE_COMPLAINT: ["怎么又卡了", "声音太小了，听不清", "卡了"],
    InputType.CREATIVE_TOPIC: ["我有个想法，想做一个会飞的机器人", "我在写一个故事"],
    InputType.ASK_ADVICE: ["你说我该怎么办", "帮我想想有什么办法"],
    InputType.TOPIC: ["今天天气不错", "我们聊聊天文吧"],
}

TARGET_PROBES = {
    "我今天有点难过": EmotionTarget.USER_SELF,
    "老板今天又催我加班，烦死了": EmotionTarget.EXTERNAL_PERSON,
    "你真笨": EmotionTarget.CHARACTER,
    "你别难过了": EmotionTarget.CHARACTER,
    "怎么又卡了": EmotionTarget.DEVICE,
    "今天天气不错": EmotionTarget.TOPIC,
}

BID_PROBES = {
    "我今天有点难过": BidType.SUPPORT,
    "你说我该怎么办": BidType.SUPPORT,
    "哈哈你猜我刚干了什么": BidType.PLAY,
    "你真聪明": BidType.PLAY,
    "我考了满分！": BidType.CONNECTION,
    "我们聊聊天文吧": BidType.CONNECTION,
    "你好": BidType.ATTENTION,
    "拜拜": BidType.NONE,
    "怎么又卡了": BidType.NONE,
}

DEPTH_PROBES = {
    "今天天气不错": 0,
    "我最喜欢恐龙了": 1,
    "我今天有点难过": 2,
    "其实我从来没跟别人说过，我特别怕输": 3,
    "我好笨，什么都做不好": 3,
}


def test_all_input_types_classified():
    for itype, probes in INPUT_PROBES.items():
        for text in probes:
            frame, _ = perceive(text)
            assert frame.input_type == itype, f"{text!r} → {frame.input_type}，预期 {itype}"


def test_all_emotion_targets():
    for text, target in TARGET_PROBES.items():
        frame, reading = perceive(text)
        assert frame.target == target, f"{text!r} → {frame.target}，预期 {target}"
        assert reading.target == target


def test_all_bid_types():
    for text, bid in BID_PROBES.items():
        frame, _ = perceive(text)
        assert frame.bid == bid, f"{text!r} → {frame.bid}，预期 {bid}"


def test_all_disclosure_depths():
    for text, depth in DEPTH_PROBES.items():
        frame, _ = perceive(text)
        assert frame.disclosure_depth == depth, f"{text!r} → {frame.disclosure_depth}，预期 {depth}"


def test_two_char_attacks_not_short_reply():
    """"笨蛋""走开""卡了"只有两个字，但必须按语义路由，不能当低信息短答。"""
    assert perceive("笨蛋")[0].input_type == InputType.CHARACTER_ATTACK
    assert perceive("走开")[0].input_type == InputType.CHARACTER_REJECTION
    assert perceive("卡了")[0].input_type == InputType.DEVICE_COMPLAINT
    assert perceive("嗯")[0].input_type == InputType.SHORT_REPLY


# ---------------------------------------------------------------- 枚举完备性

def test_every_act_has_render_name():
    for act in Act:
        assert act in _ACT_ZH, f"Act.{act.name} 缺中文渲染名"


def test_every_humor_style_has_render_name():
    for style in HumorStyle:
        assert style.value in _STYLE_HUMOR_ZH


def test_every_character_emotion_renderable_and_has_pad():
    """评估器在全类型 × 全阶段下产出的每个情绪标签，
    都必须有 PAD 向量（心境演化）和中文渲染名（提示词），否则线上 KeyError。"""
    identity = CharacterIdentity()
    seen = set()
    for probes in INPUT_PROBES.values():
        for text in probes:
            frame, reading = perceive(text)
            for stage in Stage:
                for mood in (MoodState(0.4, 0.3, 0.2), MoodState(-0.5, 0.3, -0.5)):
                    emo = appraise(frame, reading, identity, mood, stage)
                    seen.add(emo.label)
                    if emo.secondary:
                        seen.add(emo.secondary)
    for label in seen:
        assert label in _EMOTION_PAD, f"情绪 {label} 缺 PAD 向量"
        assert label in _EMO_ZH, f"情绪 {label} 缺中文渲染名"


def test_every_user_emotion_label_renderable():
    seen = set()
    for probes in INPUT_PROBES.values():
        for text in probes:
            _, reading = perceive(text)
            seen.add(reading.label)
    for label in seen:
        assert label in _USER_EMO_ZH, f"用户情绪 {label} 缺中文渲染名"


# ---------------------------------------------------------------- 序列化完整性

def test_state_roundtrip_field_equality(tmp_path):
    """重启前后状态必须逐字段相等——丢任何一个字段都是慢性失忆。"""
    cfg = EngineConfig(state_dir=str(tmp_path / "s"))
    eng = CompanionEngine(config=cfg)
    t = T0
    script = [
        ("你好呀", "（回复）"),
        ("你可以叫我小禾", "好呀小禾！"),
        ("我最喜欢恐龙了，乐乐是我最好的朋友", "记住啦！下次我给你讲恐龙的故事"),
        ("我今天有点难过", "（轻轻回复）"),
        ("别烦我", "（收住）"),
        ("你真笨", "（站直）"),
        ("逗你的啦，你别生气", "（接受安抚）"),
    ]
    for text, reply in script:
        eng.prepare_turn("u", text, now=t)
        eng.commit("u", text, reply, now=t)
        t += timedelta(minutes=3)

    before = eng._state("u").to_dict()
    loaded = StateStore(cfg.state_dir).load("u").to_dict()
    assert loaded == before
    # 关键字段抽查：确实有内容在被序列化，而不是两个空壳相等
    assert before["adaptation"]["address_form"] == "小禾"
    assert "恐龙" in before["memory"]["preferences"]
    assert "乐乐" in before["memory"]["people"]
    assert before["memory"]["promises"], "承诺没有被持久化"
    assert before["ledger"]["ruptures_repaired"] == 1
    assert before["adaptation"]["lessons"], "相处教训没有被持久化"
