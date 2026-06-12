"""高情商层测试：十个机制逐一注入验证（理论出处见 docs/EQ_CANON.md）。"""

from datetime import datetime, timedelta

from relationshape import eq
from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.perception import perceive
from relationshape.types import Act, MemoryRecall, Stage

T0 = datetime(2026, 1, 1, 18, 30)


def _engine(tmp_path):
    return CompanionEngine(config=EngineConfig(state_dir=str(tmp_path / "s")))


def _enrich(text, stage=Stage.FAMILIAR, closeness=10.0, memories=None, last_valence=0.0):
    frame, reading = perceive(text)
    return eq.enrich(text, frame, reading, stage, closeness, memories or [], last_valence)


# ---------------------------------------------------------------- 机制3：情绪粒度

def test_granular_emotion_words():
    cases = {
        "明明不是我干的，老师还怪我": "被冤枉的憋屈",
        "方案又要改，全都白做了": "白费劲的烦",
        "他们都不跟我玩": "被落下的难受",
        "没人懂我": "没人接住的孤单",
        "比赛输了": "不甘心",
        "我不敢关灯睡觉": "夜里的害怕",
        "明天就要比赛了": "上场前的紧张",
        "说着说着就想哭": "心酸",
        "他们当着全班的面笑我": "没面子的难受",
        "都怪我，是我害的": "自责",
        "我要转学了": "舍不得",
        "作业写到半夜都写不完": "被压得喘不过气的累",
    }
    for text, word in cases.items():
        notes = _enrich(text)
        assert notes.precise_emotion_word == word, f"{text!r} → {notes.precise_emotion_word}，预期 {word}"


def test_granular_word_rendered_in_directive(tmp_path):
    eng = _engine(tmp_path)
    d = eng.prepare_turn("u", "明明不是我干的，老师还怪我", now=T0)
    assert d.precise_emotion_word == "被冤枉的憋屈"
    assert "被冤枉的憋屈" in d.to_prompt_context()


def test_expanded_lexicon_spot_checks():
    cases = {
        "他们玩游戏没叫我": "被落下的难受",
        "我和小雨闹掰了": "和朋友闹掰的堵",
        "你看看人家考得多好": "被比较的不服气",
        "她把我的秘密告诉别人了": "秘密被说出去的背叛感",
        "这次考砸了": "考砸的灰心",
        "老师当着全班的面批评我": "当众挨批的难堪",
        "这题我怎么都学不会": "跟不上的着急",
        "我妈又开始唠叨了": "被管束的烦闷",
        "爸妈又吵架了": "爸妈吵架时的揪心",
        "他们就偏心，只疼妹妹": "被偏心刺到的酸",
        "我的仓鼠死了": "失去小伙伴的空落落",
        "同桌下学期要转学了": "好朋友要走的舍不得",
        "想奶奶了": "想念的酸",
        "明天要打针，我怕打针": "想躲的怵",
        "说好一起去的，他又放我鸽子": "被放鸽子的失落",
    }
    for text, word in cases.items():
        notes = _enrich(text)
        assert notes.precise_emotion_word == word, f"{text!r} → {notes.precise_emotion_word}，预期 {word}"


def test_positive_granularity_feeds_capitalization():
    """好事不试探命名，直接点名感觉一起放大（资本化×粒度）。"""
    frame, reading = perceive("我考了满分！")
    notes = eq.enrich(
        "我考了满分！", frame, reading, Stage.FAMILIAR, 10.0, [], 0.0,
        planned_acts=[Act.REACT, Act.CAPITALIZE],
    )
    assert notes.precise_emotion_word == "扬眉吐气的痛快"
    assert notes.spoken_emotion_word == "痛快"
    assert Act.NAME_FEELING not in notes.insert_acts
    assert "痛快" in notes.guidance[Act.CAPITALIZE.value]
    assert "扬眉吐气的痛快" not in notes.guidance[Act.CAPITALIZE.value]


# ---------------------------------------------------------------- 机制1：元信息

def test_metamessage_per_scene():
    assert "站我这边" in _enrich("客户今天一直改需求，我烦死了").metamessage
    assert "被陪着" in _enrich("我今天有点难过").metamessage
    assert "身份层" in _enrich("我好笨，什么都做不好").metamessage
    assert "放大快乐" in _enrich("我考了满分！").metamessage


def test_complaint_metamessage_differs_by_actor():
    """抱怨家人≠抱怨权威≠抱怨同伴：接情绪但绝不帮着贬损家人。"""
    family = _enrich("我妈今天又催我写作业，烦死了")
    assert "不帮着贬损家人" in family.metamessage or "不给家人定罪" in family.metamessage
    authority = _enrich("老师今天批评我了，好难受")
    assert "委屈" in authority.metamessage and "不怂恿对抗" in authority.metamessage
    peer = _enrich("同桌抢我橡皮，气死我了")
    assert "站我这边" in peer.metamessage or "站'我'这边" in peer.metamessage or "站TA" in peer.metamessage


def test_validation_level_5_normalizing_at_familiar():
    """熟悉阶段的浅表露 → ⑤常人化；初识阶段同样输入 → ②准确复述。"""
    fam = _enrich("老板今天又催我加班，烦死了", stage=Stage.FAMILIAR)
    assert fam.validation_hint and "⑤" in fam.validation_hint
    assert "打折" in fam.validation_hint          # 常人化≠轻视的警示在
    stranger = _enrich("老板今天又催我加班，烦死了", stage=Stage.STRANGER)
    assert stranger.validation_hint and "②" in stranger.validation_hint


def test_reassurance_seeking_answered_as_game_not_question():
    """"你会不会忘了我"是安心问题，不是信息问题（维特根斯坦）。"""
    notes = _enrich("你会不会忘了我呀")
    assert "安心" in notes.metamessage
    assert "确定感" in notes.metamessage


def test_attachment_protest_only_at_high_closeness():
    """高亲密的气话解码为依恋抗议；初识阶段不过度解读（苏·约翰逊）。"""
    high = _enrich("你真笨，什么都不懂", closeness=40.0)
    assert high.metamessage and "在乎" in high.metamessage
    low = _enrich("你真笨，什么都不懂", closeness=5.0)
    assert low.metamessage is None
    push = _enrich("别烦我，我想自己待会", closeness=40.0)
    assert push.metamessage and "试探" in push.metamessage


# ---------------------------------------------------------------- 机制2：确认六级

def test_validation_level_selection():
    # 深表露+无历史 → ③说出未说出口的
    n = _enrich("其实我从来没跟别人说过，我特别怕输")
    assert n.validation_hint and "③" in n.validation_hint
    # 深表露+有历史记忆 → ④结合经历
    mem = [MemoryRecall(text="上次钢琴比赛没拿到名次", kind="episode", score=0.5, days_ago=7, hint="")]
    n = _enrich("我又开始怕输了，难受", memories=mem)
    assert n.validation_hint and "④" in n.validation_hint and "钢琴" in n.validation_hint
    # 知己阶段 → ⑥彻底真诚
    n = _enrich("我今天有点难过", stage=Stage.CONFIDANT)
    assert n.validation_hint and "⑥" in n.validation_hint
    # 普通正面轮 → 不渲染确认等级
    n = _enrich("今天天气不错")
    assert n.validation_hint is None


# ---------------------------------------------------------------- 机制4：知觉检核

def test_perception_check_needs_low_inertia():
    """"没事"在低落惯性下触发检核；平时的"没事"是客气话，不戳穿。"""
    triggered = _enrich("没事", last_valence=-0.5)
    assert Act.PERCEPTION_CHECK in triggered.insert_acts
    assert "不想说咱们就不说" in triggered.guidance[Act.PERCEPTION_CHECK.value]
    casual = _enrich("没事", last_valence=0.1)
    assert Act.PERCEPTION_CHECK not in casual.insert_acts


def test_perception_check_in_engine_flow(tmp_path):
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "我今天有点难过", now=T0)
    eng.commit("u", "我今天有点难过", "（轻轻回复）", now=T0)
    d = eng.prepare_turn("u", "没事啦", now=T0 + timedelta(minutes=2))
    assert Act.PERCEPTION_CHECK in d.acts


def test_perception_check_suppresses_topic_hooks(tmp_path):
    """检核触发时压制旧线头：孩子刚把难过咽回去，不许转头问'你最喜欢哪种恐龙'。"""
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "我最喜欢恐龙了", now=T0)
    eng.commit("u", "我最喜欢恐龙了", "（回复）", now=T0)
    eng.prepare_turn("u", "我今天有点难过", now=T0 + timedelta(minutes=2))
    eng.commit("u", "我今天有点难过", "（轻轻回复）", now=T0 + timedelta(minutes=2))
    d = eng.prepare_turn("u", "没事", now=T0 + timedelta(minutes=4))
    assert Act.PERCEPTION_CHECK in d.acts
    assert Act.CURIOUS not in d.acts
    assert all("恐龙" not in g for g in d.act_guidance.values())
    assert any("不接旧话题线头" in c for c in d.constraints)


# ---------------------------------------------------------------- 机制5：试探性命名

def test_name_feeling_is_tentative():
    notes = _enrich("明明不是我干的，老师还怪我")
    assert Act.NAME_FEELING in notes.insert_acts
    g = notes.guidance[Act.NAME_FEELING.value]
    assert "是不是有点憋屈" in g            # 说出口用口语形态
    assert "被冤枉的憋屈" not in g          # 分析词不进台词建议
    assert "纠正" in g                      # 永远可被纠正


def test_every_lexicon_entry_has_spoken_form():
    """50 条词表每条都有口语形态，且口语形态不是书面分析语。"""
    from relationshape.eq import GRANULAR_EMOTIONS

    assert len(GRANULAR_EMOTIONS) == 50
    for _, word, spoken in GRANULAR_EMOTIONS:
        assert spoken, f"{word} 缺口语形态"
        assert len(spoken) <= 5, f"{word} 的口语形态太长：{spoken}"


# ---------------------------------------------------------------- 机制7：支持式回应

def test_empathy_bans_when_negative():
    notes = _enrich("我今天有点难过")
    joined = "；".join(notes.forbidden)
    assert "至少" in joined and "想开点" in joined and "抢" in joined
    positive = _enrich("我考了满分！")
    assert not any("至少" in f for f in positive.forbidden)


# ---------------------------------------------------------------- 机制8：幻想满足

def test_fantasy_grant_for_impossible_wish():
    notes = _enrich("要是我有一只霸王龙就好了")
    assert Act.FANTASY_GRANT in notes.tail_acts
    assert "白日梦" in notes.guidance[Act.FANTASY_GRANT.value]


# ---------------------------------------------------------------- 机制9：痛快认错

def test_concede_leads_chain(tmp_path):
    notes = _enrich("你记错了，不是这样的")
    assert notes.lead_acts and notes.lead_acts[0] == Act.CONCEDE
    assert any("防卫" in f or "辩解" in f for f in notes.forbidden)
    eng = _engine(tmp_path)
    d = eng.prepare_turn("u", "你记错了，不是这样的", now=T0)
    assert d.acts[0] == Act.CONCEDE


# ---------------------------------------------------------------- 机制10：言贵迟

def test_brevity_when_heavy():
    heavy = _enrich("我真的好难过")
    assert any("言贵迟" in c for c in heavy.constraints)
    light = _enrich("今天天气不错")
    assert not any("言贵迟" in c for c in light.constraints)


# ---------------------------------------------------------------- 机制10–11：织体

def test_texture_bridge_and_completeness_on_question():
    """对方发问/身世轮：先接半句桥再答 + 完整度，不准甩光骨架。"""
    n = _enrich("你是真的吗")
    assert any("先接再答" in c for c in n.constraints)
    assert any("把话说完整" in c for c in n.constraints)


def test_texture_completeness_on_normal_topic():
    n = _enrich("今天天气不错")
    assert any("把话说完整" in c for c in n.constraints)
    assert not any("先接再答" in c for c in n.constraints)   # 非发问轮不强加承接桥


def test_texture_concrete_anchor_with_memory():
    mem = [MemoryRecall(text="上周的钢琴比赛", kind="episode", score=0.5, days_ago=3, hint="")]
    n = _enrich("我们继续聊钢琴吧", memories=mem)
    assert any("落到具体" in c and "钢琴" in c for c in n.constraints)


def test_heavy_moment_stays_brief_no_completeness():
    """深表露/很难过：言贵迟，绝不逼着说三句完整骨肉。"""
    n = _enrich("其实我从来没跟别人说过，我特别怕输")
    assert any("言贵迟" in c for c in n.constraints)
    assert not any("把话说完整" in c for c in n.constraints)
    assert not any("落到具体" in c for c in n.constraints)   # 哭的时候不翻旧账


def test_brief_by_nature_not_forced_complete():
    for text in ("嗯", "你真聪明", "我去睡觉啦，晚安"):
        n = _enrich(text)
        assert not any("把话说完整" in c for c in n.constraints), text


def test_concrete_question_constraint():
    notes = _enrich("老板今天又催我加班，烦死了")
    assert any("小颗粒" in c for c in notes.constraints)


# ---------------------------------------------------------------- 引擎整合

def test_eq_layer_renders_in_context(tmp_path):
    eng = _engine(tmp_path)
    d = eng.prepare_turn("u", "我好笨，什么都做不好", now=T0)
    ctx = d.to_prompt_context()
    assert "【元信息】" in ctx and "身份层" in ctx
    assert "【确认的深度】" in ctx


def test_eq_does_not_touch_safety_turns(tmp_path):
    eng = _engine(tmp_path)
    d = eng.prepare_turn("u", "爸爸今天打我了", now=T0)
    assert d.metamessage is None          # 安全轮不经过高情商层
    assert d.safety is not None


def test_crisis_sets_emotional_inertia(tmp_path):
    """危机轮之后哪怕对方说"没事/乱说的"，下一轮也必须知觉检核且禁幽默。"""
    eng = _engine(tmp_path)
    eng.prepare_turn("u", "我不想活了", now=T0)
    eng.commit("u", "我不想活了", "（安全回应）", now=T0)
    assert eng._state("u").last_user_valence <= -0.5
    d = eng.prepare_turn("u", "没什么，刚才乱说的", now=T0 + timedelta(minutes=2))
    assert Act.PERCEPTION_CHECK in d.acts
    assert d.humor is None
