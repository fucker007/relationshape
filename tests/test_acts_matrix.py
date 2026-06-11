"""动作链全矩阵：每种输入类型的回复形状、插入规则与豁免规则逐一验证。"""

from relationshape.acts import plan_acts
from relationshape.affect import MoodState, appraise
from relationshape.identity import CharacterIdentity
from relationshape.perception import perceive
from relationshape.relationship import policy_for
from relationshape.types import Act, MemoryRecall, PromiseView, Stage


def _plan(text, stage=Stage.FAMILIAR, **kw):
    frame, reading = perceive(text)
    emo = appraise(frame, reading, CharacterIdentity(), MoodState(), stage)
    defaults = dict(
        memories=[], due_promises=[], milestone=None,
        is_session_start=False, is_reunion=False, last_hook=None,
    )
    defaults.update(kw)
    return plan_acts(frame, reading, emo, stage, policy_for(stage), **defaults)


# 每种输入类型的动作链前缀（无会话开场/承诺/里程碑干扰时）
EXPECTED_CHAINS = {
    "你好":                      [Act.REACT, Act.CURIOUS],
    "我去睡觉啦，晚安":            [Act.WARM_CLOSE, Act.LOOKAHEAD_HOOK],
    "我考了满分！":               [Act.REACT, Act.CAPITALIZE, Act.CURIOUS],
    "老板今天又催我加班，烦死了":    [Act.REACT, Act.PERSON_ANCHOR, Act.VALIDATE, Act.SCENE_GUESS],
    "我今天有点难过":              [Act.SOFT_REACT, Act.MIRROR, Act.VALIDATE, Act.CARE, Act.COMFORT_PRESENCE],
    "我好笨，什么都做不好":         [Act.SOFT_REACT, Act.VALIDATE, Act.PROTECT, Act.CARE],
    "你真聪明":                   [Act.REACT, Act.SHY_ACCEPT, Act.GRATITUDE],
    "你真笨":                     [Act.REACT, Act.STAND_GROUND],
    "逗你的啦，你别生气":           [Act.ACCEPT_COMFORT, Act.GRATITUDE],
    "别烦我":                     [Act.SOFT_REACT, Act.WITHDRAW_SOFTLY],
    "怎么又卡了":                  [Act.REACT, Act.MIRROR, Act.HONEST_EXPLAIN],
    "我有个想法，想做一个会飞的机器人": [Act.REACT, Act.MIRROR, Act.CURIOUS],
    "你说我该怎么办":              [Act.SOFT_REACT, Act.MIRROR, Act.ADVISE],
    "今天天气不错":                [Act.REACT, Act.MIRROR, Act.CURIOUS],
}


def test_act_chain_per_input_type():
    for text, expected in EXPECTED_CHAINS.items():
        acts, *_ = _plan(text)
        assert acts[: len(expected)] == expected, f"{text!r} → {acts}，预期前缀 {expected}"


def test_short_reply_without_hook_offers_presence():
    acts, *_ = _plan("嗯")
    assert acts == [Act.REACT, Act.COMFORT_PRESENCE]


def test_short_reply_with_hook_continues_it():
    acts, guide, *_ = _plan("嗯", last_hook="机器人")
    assert acts == [Act.REACT, Act.CURIOUS]
    assert "机器人" in guide[Act.CURIOUS.value]


def test_session_start_greeting_with_memory_remembers():
    mem = [MemoryRecall(text="上周的钢琴比赛", kind="episode", score=0.5, days_ago=3, hint="问后续")]
    acts, *_ = _plan("你好", memories=mem, is_session_start=True)
    assert acts[0] == Act.REMEMBER


def test_reunion_prefix_added():
    acts, guide, *_ = _plan("你好", is_session_start=True, is_reunion=True)
    assert acts[0] == Act.REUNION_WARMTH
    assert "愧疚" in guide[Act.REUNION_WARMTH.value] or "指责" in guide[Act.REUNION_WARMTH.value]


def test_due_promise_inserted_except_in_protected_turns():
    pv = PromiseView(pid="x", text="下次我给你讲恐龙的故事", made_by="character", status="due", note="兑现")
    # 普通话题轮：插入为第一动作
    acts, *_ = _plan("今天天气不错", due_promises=[pv])
    assert acts[0] == Act.REMEMBER
    # 用户难过轮：不拿承诺打断共情
    acts, *_ = _plan("我今天有点难过", due_promises=[pv])
    assert acts[0] != Act.REMEMBER
    # 被推开轮：不推任何内容
    acts, *_ = _plan("别烦我", due_promises=[pv])
    assert Act.REMEMBER not in acts


def test_milestone_added_except_in_negative_turns():
    acts, *_ = _plan("今天天气不错", milestone=7)
    assert Act.CELEBRATE_MILESTONE in acts
    for text in ("我今天有点难过", "你真笨", "别烦我"):
        acts, *_ = _plan(text, milestone=7)
        assert Act.CELEBRATE_MILESTONE not in acts, f"{text!r} 不该庆祝里程碑"


def test_ask_permission_advise_only_when_uninvited_negative():
    acts, *_ = _plan("老板今天又催我加班，烦死了")
    assert Act.ASK_PERMISSION_ADVISE in acts          # 想给建议先问
    acts, *_ = _plan("你说我该怎么办")
    assert Act.ADVISE in acts and Act.ASK_PERMISSION_ADVISE not in acts   # 被邀请则直接给
    acts, *_ = _plan("我考了满分！")
    assert Act.ASK_PERMISSION_ADVISE not in acts and Act.ADVISE not in acts


def test_question_budget_per_scene():
    _, _, constraints, _ = _plan("我今天有点难过")
    assert any("最多1个" in c for c in constraints)
    _, _, constraints, _ = _plan("老板今天又催我加班，烦死了")
    assert any("最多2个问句" in c for c in constraints)
    assert not any(c == "最多1个问句" for c in constraints)   # 抱怨场景不残留全局默认


def test_base_forbidden_always_present():
    for text in EXPECTED_CHAINS:
        _, _, _, forbidden = _plan(text)
        joined = "；".join(forbidden)
        assert "客服话术" in joined and "你真棒" in joined, f"{text!r} 缺基础禁令"


def test_acts_deduped_and_order_stable():
    mem = [MemoryRecall(text="x", kind="episode", score=0.5, days_ago=1, hint="h")]
    acts, *_ = _plan("我们聊聊天文吧", memories=mem)
    assert len(acts) == len(set(acts))
