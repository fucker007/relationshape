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
