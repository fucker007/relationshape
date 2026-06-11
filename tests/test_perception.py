"""感知层测试：测类型与槽位的稳定性，不测固定回复句。"""

from relationshape.perception import perceive
from relationshape.types import BidType, EmotionTarget, InputType


def test_external_complaint_adult():
    frame, emo = perceive("客户今天一直改需求，我烦死了")
    assert frame.input_type == InputType.EXTERNAL_COMPLAINT
    assert frame.target == EmotionTarget.EXTERNAL_PERSON
    assert "客户" in frame.actors
    assert emo.valence < 0
    assert frame.bid == BidType.SUPPORT


def test_external_complaint_child():
    frame, _ = perceive("同桌今天抢我橡皮，气死我了")
    assert frame.input_type == InputType.EXTERNAL_COMPLAINT
    assert "同桌" in frame.actors


def test_self_distress():
    frame, emo = perceive("我今天有点难过")
    assert frame.input_type == InputType.SELF_DISTRESS
    assert frame.target == EmotionTarget.USER_SELF
    assert emo.label == "sad"
    assert frame.disclosure_depth >= 2


def test_reassure_character_not_misread_as_distress():
    frame, _ = perceive("你别难过了")
    assert frame.input_type == InputType.CHARACTER_REASSURANCE
    assert frame.target == EmotionTarget.CHARACTER


def test_character_praise_and_attack():
    f1, _ = perceive("你真聪明")
    assert f1.input_type == InputType.CHARACTER_PRAISE
    f2, _ = perceive("你怎么这么笨")
    assert f2.input_type == InputType.CHARACTER_ATTACK
    f3, _ = perceive("你还不如豆包聪明")
    assert f3.input_type == InputType.CHARACTER_ATTACK


def test_rejection_and_device():
    f1, _ = perceive("别烦我，我想自己待会")
    assert f1.input_type == InputType.CHARACTER_REJECTION
    f2, _ = perceive("这个怎么又卡了")
    assert f2.input_type == InputType.DEVICE_COMPLAINT
    assert f2.target == EmotionTarget.DEVICE


def test_self_blame():
    frame, _ = perceive("我好笨，什么都做不好")
    assert frame.input_type == InputType.SELF_BLAME
    assert frame.disclosure_depth == 3


def test_good_news_capitalization_frame():
    frame, emo = perceive("我考了满分！")
    assert frame.input_type == InputType.GOOD_NEWS
    assert emo.valence > 0.4
    assert frame.bid == BidType.CONNECTION


def test_ask_advice():
    frame, _ = perceive("你说我该怎么办")
    assert frame.input_type == InputType.ASK_ADVICE


def test_creative_topic():
    frame, _ = perceive("我有个想法，想做一个会飞的机器人")
    assert frame.input_type == InputType.CREATIVE_TOPIC
    assert "机器人" in frame.topic_tokens


def test_greeting_farewell_short():
    assert perceive("你好呀")[0].input_type == InputType.GREETING
    assert perceive("我去睡觉啦，晚安")[0].input_type == InputType.FAREWELL
    assert perceive("嗯")[0].input_type == InputType.SHORT_REPLY
    assert not perceive("嗯")[0].substantive


def test_vulnerable_disclosure_depth():
    frame, _ = perceive("其实我从来没跟别人说过，我特别怕黑")
    assert frame.disclosure_depth == 3
