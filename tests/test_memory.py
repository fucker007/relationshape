"""记忆系统测试：抽取、召回、遗忘、承诺。"""

from datetime import datetime, timedelta

from relationshape.memory import MemoryBank

T0 = datetime(2026, 1, 1, 10, 0)


def test_fact_extraction():
    bank = MemoryBank()
    learned = bank.extract_facts("我最喜欢恐龙了，我叫小禾", [])
    assert "恐龙" in bank.preferences
    assert bank.user_name == "小禾"
    assert len(learned) == 2


def test_person_extraction():
    bank = MemoryBank()
    bank.extract_facts("乐乐是我最好的朋友", [])
    assert "乐乐" in bank.people


def test_recall_by_topic_overlap():
    bank = MemoryBank()
    bank.add_episode("我在准备钢琴比赛", valence=0.3, arousal=0.6, now=T0)
    bank.add_episode("今天午饭吃了面条", valence=0.1, arousal=0.2, now=T0)
    hits = bank.recall("钢琴练得怎么样了", T0 + timedelta(days=3), k=3, half_life_days=14)
    assert hits
    assert "钢琴" in hits[0].text


def test_forgetting_curve_and_rehearsal():
    """低显著度的旧事会被遗忘；被召回过的记忆寿命更长。"""
    bank = MemoryBank()
    weak = bank.add_episode("路过了一家便利店", valence=0.0, arousal=0.1, now=T0)
    strong = bank.add_episode("钢琴比赛拿了第一名", valence=0.9, arousal=0.9, now=T0)
    strong.recall_count = 2  # 模拟被复习过
    bank.decay_and_prune(T0 + timedelta(days=60), half_life_days=14, threshold=0.05, cap=400)
    texts = [e.text for e in bank.episodes]
    assert all("便利店" not in t for t in texts)
    assert any("钢琴比赛" in t for t in texts)


def test_sensitive_memory_never_recalled():
    bank = MemoryBank()
    bank.add_episode("爸爸打我了", valence=-0.9, arousal=0.8, now=T0, sensitive=True)
    hits = bank.recall("爸爸今天怎么样", T0 + timedelta(days=1), k=3, half_life_days=14)
    assert not hits
    assert bank.recent_highlight(T0 + timedelta(days=1)) is None


def test_secret_disclosure_not_used_as_greeting_hook():
    """秘密级表露话题相关时可召回，但不做开场白。"""
    bank = MemoryBank()
    bank.add_episode("其实我从来没跟别人说过，我特别怕输", valence=-0.6, arousal=0.7, now=T0, vulnerability=3)
    bank.add_episode("我画了一只翼龙", valence=0.2, arousal=0.4, now=T0)
    hl = bank.recent_highlight(T0 + timedelta(days=2))
    assert hl is not None and "翼龙" in hl.text
    # 话题相关时仍能温柔接住
    hits = bank.recall("我还是很怕输怎么办", T0 + timedelta(days=2), k=3, half_life_days=14)
    assert any("怕输" in h.text for h in hits)


def test_recent_highlight_handles_future_episode_timestamp():
    bank = MemoryBank()
    bank.add_episode("我明天要画画比赛了", valence=0.4, arousal=0.5, now=T0 + timedelta(days=10))
    assert bank.recent_highlight(T0) is None


def test_recall_skips_future_episode_timestamp():
    bank = MemoryBank()
    bank.add_episode("我明天要画画比赛了", valence=0.4, arousal=0.5, now=T0 + timedelta(days=10))
    hits = bank.recall("画画比赛怎么样", T0, k=3, half_life_days=14)
    assert not hits


def test_promise_lifecycle():
    bank = MemoryBank()
    p = bank.detect_character_promise("好呀！下次我给你讲恐龙的故事", T0, session_index=2)
    assert p is not None and p.made_by == "character"
    # 同一会话内不到期
    assert not bank.due_promises(session_index=2)
    # 下一会话到期
    due = bank.due_promises(session_index=3)
    assert len(due) == 1
    assert "兑现" in due[0].note
    bank.mark_promise(p.pid, "kept")
    assert not bank.due_promises(session_index=4)


def test_promise_not_duplicated():
    bank = MemoryBank()
    bank.detect_character_promise("下次我给你讲恐龙的故事", T0, 1)
    bank.detect_character_promise("下次我给你讲恐龙的故事", T0 + timedelta(minutes=5), 1)
    assert len(bank.promises) == 1


# ── 用户记忆抽取与档案（generalization fixes，断言通用机制非特例）──

def test_name_extraction_pattern_class():
    """名字抽取是一类模式，不是单串。"""
    for utt, gold in [("我叫航航","航航"), ("我的名字是子轩","子轩"),
                      ("我的名字叫朵朵","朵朵"), ("你可以叫我糖糖","糖糖"),
                      ("我是浩浩，今年7岁","浩浩")]:
        b = MemoryBank(); b.extract_facts(utt, [])
        assert b.user_name == gold, f"{utt} → {b.user_name}"


def test_interrogative_never_polluts_facts():
    """问句不能被当成陈述学进去（'我叫什么'不能把名字设成'什么'）。"""
    b = MemoryBank(); b.user_name = "小明"
    b.extract_facts("你还记得我叫什么名字吗", [])
    assert b.user_name == "小明"                       # 名字没被问句覆盖
    b.extract_facts("我最喜欢什么来着", [])
    assert "什么" not in b.preferences


def test_cared_extraction_and_persist():
    b = MemoryBank()
    b.extract_facts("我最在乎的就是画画梦想", [])
    assert "画画梦想" in b.cared
    assert "画画梦想" in b.user_profile_facts()["cared"]


def test_person_prefix_and_rolewords_filtered():
    """'好朋友'带前缀要抽到真名；关系泛称不当人名进档案。"""
    b = MemoryBank()
    b.extract_facts("闹闹是我的好朋友", [])
    facts_people = dict(b.user_profile_facts()["people"])
    assert "闹闹" in facts_people                       # 真名抽到
    assert "朋友" not in facts_people and "好朋友" not in facts_people  # 关系词不算人


def test_profile_facts_structure():
    b = MemoryBank()
    b.extract_facts("我叫小鱼", []); b.extract_facts("我超喜欢恐龙", [])
    b.extract_facts("乐乐是我的同桌", []); b.extract_facts("我最在乎的就是太空梦", [])
    f = b.user_profile_facts()
    assert f["name"] == "小鱼" and "恐龙" in f["preferences"]
    assert ("乐乐", "同桌") in f["people"] and "太空梦" in f["cared"]


def test_vague_reference_surfaces_candidates():
    """模糊回指浮出 top-K 候选（多件事时让模型据上下文挑/追问）。"""
    from datetime import datetime, timedelta
    b = MemoryBank()
    t0 = datetime(2026, 1, 1)
    for i, e in enumerate(["养了小乌龟", "搬了新家", "学会了游泳"]):
        b.add_episode(f"前几天我{e}", 0.2, 0.3, t0 + timedelta(days=i))
    out = b.most_salient(t0 + timedelta(days=5), half_life_days=14, k=3)
    assert len(out) == 3
    assert all("哪件" in m.hint or "候选" in m.hint or "这件" in m.hint for m in out)


def test_extraction_robust_to_colloquial_phrasings():
    """10k 压力测试逼出的口语句式：抽取应是模式类，覆盖这些变体。"""
    name_cases = {"我小名叫文明": "文明", "人家叫贝曦啦": "贝曦", "我，叫可婷": "可婷",
                  "我名叫杰曦": "杰曦", "我的名字呀，是贝静": "贝静"}
    for utt, gold in name_cases.items():
        b = MemoryBank(); b.extract_facts(utt, [])
        assert b.user_name == gold, f"{utt} → {b.user_name}"

    pref_cases = {"我超爱听故事": "听故事", "最近迷上了唱歌": "唱歌", "我就喜欢玩魔方": "玩魔方",
                  "我对写日记特别着迷": "写日记", "说真的我超迷乐高": "乐高"}
    for utt, gold in pref_cases.items():
        b = MemoryBank(); b.extract_facts(utt, [])
        assert gold in b.preferences, f"{utt} → {b.preferences}"

    cared_cases = {"我这辈子最在乎当画家的志向": "当画家的志向",
                   "收养的流浪狗对我来说就是一切": "收养的流浪狗",
                   "我心心念念的就是出国留学的梦": "出国留学的梦"}
    for utt, gold in cared_cases.items():
        b = MemoryBank(); b.extract_facts(utt, [])
        assert gold in b.cared, f"{utt} → {b.cared}"


def test_person_extraction_all_phrasings():
    """人物抽取覆盖多种关系句式，且真名落入档案。"""
    cases = {"我跟乐乐是同桌": "乐乐", "我那个同桌叫朵朵": "朵朵",
             "我的闺蜜婷婷对我特别好": "婷婷", "浩浩，我发小": "浩浩",
             "睿睿是我最好的哥哥": "睿睿"}
    for utt, gold in cases.items():
        b = MemoryBank(); b.extract_facts(utt, [])
        people = dict(b.user_profile_facts()["people"])
        assert any(gold in k for k in people), f"{utt} → {list(people)}"


# ── 多事实冲突更新（#10 keystone：偏好失效/更替，非只增列表）──

def test_preference_retraction_removes_old():
    b = MemoryBank()
    b.extract_facts("我最喜欢恐龙了", [])
    assert "恐龙" in b.preferences
    b.extract_facts("我不喜欢恐龙了", [])
    assert "恐龙" not in b.preferences          # 撤回：删掉旧偏好
    assert "恐龙" not in b.aversions            # 且不矛盾地变成"讨厌"


def test_preference_replacement_updates_to_new():
    b = MemoryBank()
    b.extract_facts("我最喜欢恐龙了", [])
    b.extract_facts("我不喜欢恐龙了，现在最喜欢机器人", [])
    assert "恐龙" not in b.preferences and "机器人" in b.preferences


def test_preference_shift_keeps_both_recent_first():
    """更喜欢新的≠讨厌旧的：两者都留，新的排到最近。"""
    b = MemoryBank()
    b.extract_facts("我最喜欢恐龙了", [])
    b.extract_facts("我现在更喜欢机器人了", [])
    assert b.preferences[-1] == "机器人"        # 最近偏好在末尾（档案展示当前最爱）


def test_real_aversion_still_captured():
    b = MemoryBank()
    b.extract_facts("我讨厌青椒", []); b.extract_facts("我害怕打雷", [])
    assert "青椒" in b.aversions and "打雷" in b.aversions
