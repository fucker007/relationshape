"""动作规划测试：每种场景的回复形状与禁区。"""

from relationshape.acts import plan_acts
from relationshape.affect import MoodState, appraise
from relationshape.identity import CharacterIdentity
from relationshape.perception import perceive
from relationshape.relationship import policy_for
from relationshape.types import Act, Stage


def _plan(text, stage=Stage.FAMILIAR, **kw):
    frame, reading = perceive(text)
    emo = appraise(frame, reading, CharacterIdentity(), MoodState(), stage)
    defaults = dict(
        memories=[], due_promises=[], milestone=None,
        is_session_start=False, is_reunion=False, last_hook=None,
    )
    defaults.update(kw)
    return plan_acts(frame, reading, emo, stage, policy_for(stage), **defaults)


def test_external_complaint_allies_with_user():
    acts, guide, constraints, forbidden = _plan("老板今天又催我加班，烦死了")
    assert Act.PERSON_ANCHOR in acts
    assert Act.VALIDATE in acts
    assert Act.SCENE_GUESS in acts
    assert any("解决方案" in f for f in forbidden)


def test_distress_gets_presence_not_advice():
    acts, guide, constraints, forbidden = _plan("我今天好难过")
    assert Act.COMFORT_PRESENCE in acts
    assert Act.ADVISE not in acts
    assert Act.ASK_PERMISSION_ADVISE in acts  # 想给建议要先问
    assert any("想开点" in f for f in forbidden)


def test_self_blame_protected_with_specifics():
    acts, guide, constraints, forbidden = _plan("我好笨，什么都做不好")
    assert Act.PROTECT in acts
    assert any("具体" in guide.get(Act.PROTECT.value, "") for _ in [0])


def test_attack_stands_ground_briefly():
    acts, guide, constraints, forbidden = _plan("你真笨")
    assert Act.STAND_GROUND in acts
    assert any("简短" in c or "1-2句" in c for c in constraints)


def test_praise_shy_accept_no_fishing():
    acts, guide, constraints, forbidden = _plan("你真聪明")
    assert Act.SHY_ACCEPT in acts
    assert any("索取" in f for f in forbidden)


def test_rejection_withdraws_softly():
    acts, guide, constraints, forbidden = _plan("别烦我，我想自己待会")
    assert Act.WITHDRAW_SOFTLY in acts
    assert any("不开任何新话题" in c for c in constraints)


def test_good_news_capitalized():
    acts, guide, constraints, forbidden = _plan("我考了满分！")
    assert Act.CAPITALIZE in acts
    assert any("泼冷水" in f for f in forbidden)


def test_farewell_peak_end():
    acts, guide, constraints, forbidden = _plan("我去睡觉啦，晚安")
    assert Act.WARM_CLOSE in acts
    assert Act.LOOKAHEAD_HOOK in acts


def test_ask_advice_unlocks_advise():
    acts, *_ = _plan("你说我该怎么办")
    assert Act.ADVISE in acts


def test_short_reply_continues_hook():
    acts, guide, *_ = _plan("嗯", last_hook="机器人")
    assert Act.CURIOUS in acts
    assert "机器人" in guide[Act.CURIOUS.value]


def test_due_promise_inserted_first():
    from relationshape.types import PromiseView

    pv = PromiseView(pid="x", text="下次我给你讲恐龙的故事", made_by="character", status="due", note="主动提起并兑现它")
    acts, guide, *_ = _plan("你好呀", due_promises=[pv], is_session_start=True)
    assert acts[0] == Act.REMEMBER
    assert "恐龙" in guide[Act.REMEMBER.value]
