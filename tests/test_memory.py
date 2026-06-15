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
