"""成长挑战引擎的行为契约测试。

重点守住几条红线：
- 每日 5 关、一关一能力、题面不泄露答案；
- 答错给讲解引申、且仍喂努力（不饿死宠物）；
- 表达/创造无对错；
- 坚持连续/断裂正确；
- 友谊赛保护弱者（输了不被罚）；
- 持久化往返一致；
- 家长报告结构（客观能力有正确率，主观能力如实为 None）。
"""

from datetime import datetime, timedelta

from growth import GrowthEngine
from growth.types import ScoreMode

DAY0 = datetime(2026, 6, 1, 16, 0)
DAY1 = DAY0 + timedelta(days=1)
DAY3 = DAY0 + timedelta(days=3)


def _eng(tmp_path):
    return GrowthEngine(state_dir=str(tmp_path))


def _answer_all(eng, cid, now, mode="correct"):
    """把当天 5 关全答掉。mode: correct / wrong（客观题故意答错）。"""
    home = eng.home(cid, now=now)
    for ch in home["today"]["challenges"]:
        c = eng.bank.get(ch["cid"])
        if c.score_mode == ScoreMode.OBJECTIVE:
            ans = c.answer if mode == "correct" else "完全不对的胡乱回答"
        else:
            ans = "今天我和好朋友一起玩了很久，特别开心，还学会了一个新游戏"
        eng.answer(cid, ch["cid"], ans, now=now)
    return eng.home(cid, now=now)


def test_today_shape_and_no_answer_leak(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("小测", now=DAY0)
    t = eng.home(c.child_id, now=DAY0)["today"]
    assert t["total"] == 5 and len(t["challenges"]) == 5
    assert [ch["kind"] for ch in t["challenges"]] == \
        ["warmup", "logic", "expression", "observation", "creation"]
    # 服务端绝不把答案/讲解下发给客户端
    for ch in t["challenges"]:
        assert "answer" not in ch and "explain" not in ch and "accept" not in ch


def test_objective_wrong_returns_explain_and_still_feeds_effort(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("小测", now=DAY0)
    logic = [ch for ch in eng.home(c.child_id, now=DAY0)["today"]["challenges"]
             if ch["kind"] == "logic"][0]
    o = eng.answer(c.child_id, logic["cid"], "瞎写一个", now=DAY0)["outcome"]
    assert o["correct"] is False
    assert o["explain"] and o["extend"]          # 答错给讲解引申
    assert o["growth_earned"] > 0                 # 答错仍喂努力


def test_effort_challenge_has_no_correctness(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("小测", now=DAY0)
    expr = [ch for ch in eng.home(c.child_id, now=DAY0)["today"]["challenges"]
            if ch["kind"] == "expression"][0]
    o = eng.answer(c.child_id, expr["cid"], "我最开心的是今天考试得了满分", now=DAY0)["outcome"]
    assert o["correct"] is None
    assert o["growth_earned"] > 0


def test_pet_fed_by_effort_not_correctness(tmp_path):
    """红线：一个客观题全答错的孩子，照样养大宠物、照样连续打卡。"""
    eng = _eng(tmp_path)
    c = eng.create_child("阿错", now=DAY0)
    home = _answer_all(eng, c.child_id, DAY0, mode="wrong")
    assert home["today"]["all_done"]
    assert home["streak"] == 1
    assert home["pet"]["growth_value"] > 0        # 没被"答错"饿死


def test_streak_consecutive_then_reset(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("坚持", now=DAY0)
    _answer_all(eng, c.child_id, DAY0)
    _answer_all(eng, c.child_id, DAY1)
    assert eng.home(c.child_id, now=DAY1)["streak"] == 2
    _answer_all(eng, c.child_id, DAY3)            # 跳过 DAY2
    assert eng.home(c.child_id, now=DAY3)["streak"] == 1


def test_ability_rises_with_correct_answers(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("聪明", now=DAY0)
    before = next(a for a in eng.home(c.child_id, now=DAY0)["abilities"] if a["ability"] == "logic")["level"]
    for d in range(4):
        _answer_all(eng, c.child_id, DAY0 + timedelta(days=d))
    after = next(a for a in eng.home(c.child_id, now=DAY0 + timedelta(days=3))["abilities"] if a["ability"] == "logic")["level"]
    assert after > before


def test_battle_friendly_protects_weak(tmp_path):
    eng = _eng(tmp_path)
    strong = eng.create_child("强", now=DAY0)
    weak = eng.create_child("弱", now=DAY0)        # 全新、没练过的孩子 = 真正的弱者
    for d in range(12):
        _answer_all(eng, strong.child_id, DAY0 + timedelta(days=d))
    res = eng.battle(strong.child_id, weak.child_id, now=DAY0 + timedelta(days=12))
    assert res["friendly"] is True                 # 段位悬殊 → 友谊赛
    loser = "b" if res["winner"] == "a" else "a"
    assert res[f"{loser}_reward"]["growth"] > 0    # 输了不被罚
    assert res[f"{loser}_reward"]["stars"] > 0


def test_battle_log_is_playable(tmp_path):
    eng = _eng(tmp_path)
    a = eng.create_child("甲", now=DAY0)
    b = eng.create_child("乙", now=DAY0)
    for d in range(3):
        _answer_all(eng, a.child_id, DAY0 + timedelta(days=d))
        _answer_all(eng, b.child_id, DAY0 + timedelta(days=d))
    res = eng.battle(a.child_id, b.child_id, now=DAY0 + timedelta(days=3))
    assert res["log"] and len(res["log"]) >= 2
    ev = res["log"][0]
    for k in ("actor", "foe", "move", "dmg", "crit", "hp_a", "hp_b", "pct_a", "pct_b"):
        assert k in ev
    assert res["log"][-1]["pct_a"] < 100 or res["log"][-1]["pct_b"] < 100   # 有人掉血了


def test_combat_breakdown_sums_to_power(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("拆", now=DAY0)
    for d in range(4):
        _answer_all(eng, c.child_id, DAY0 + timedelta(days=d))
    cs = eng.home(c.child_id, now=DAY0 + timedelta(days=3))["combat"]
    assert sum(p["value"] for p in cs["breakdown"]) == cs["battle_power"]


def test_accuracy_tracked_and_real(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("准", now=DAY0)
    _answer_all(eng, c.child_id, DAY0, mode="correct")
    abil = {a["ability"]: a for a in eng.home(c.child_id, now=DAY0)["abilities"]}
    assert abil["logic"]["accuracy"] == 100          # 真实正确率
    assert abil["expression"]["accuracy"] is None     # 表达无对错


def test_cards_drop_on_correct_and_realm(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("卡", now=DAY0)
    for d in range(6):                                # 多答对 → 累积藏品卡 + 突破境界卡
        _answer_all(eng, c.child_id, DAY0 + timedelta(days=d), mode="correct")
    home = eng.home(c.child_id, now=DAY0 + timedelta(days=5))
    assert home["cards"]["owned"] > 0
    alb = eng.album(c.child_id)
    owned = [x for x in alb["cards"] if x["owned"]]
    assert any(x["source"] == "realm" for x in owned)     # 有境界卡
    assert any(x["source"] == "collect" for x in owned)    # 有藏品卡


def test_card_bonus_is_bounded(tmp_path):
    from growth.cards import CARD_BONUS_CAP, card_power_bonus, CATALOG
    full = {cid: 1 for cid in CATALOG}                 # 假设拿到全部卡
    assert card_power_bonus(full) <= CARD_BONUS_CAP    # 藏卡加成有上限,不碾压平衡


def test_realm_climbs_with_level(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("境", now=DAY0)
    before = next(a for a in eng.home(c.child_id, now=DAY0)["abilities"] if a["ability"] == "logic")["realm"]
    for d in range(8):
        _answer_all(eng, c.child_id, DAY0 + timedelta(days=d), mode="correct")
    after = next(a for a in eng.home(c.child_id, now=DAY0 + timedelta(days=7))["abilities"] if a["ability"] == "logic")["realm"]
    assert before != after                              # 境界随真实等级晋升


def test_persistence_roundtrip(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("存档", now=DAY0)
    _answer_all(eng, c.child_id, DAY0)
    gv = eng.home(c.child_id, now=DAY0)["pet"]["growth_value"]
    eng2 = _eng(tmp_path)                          # 同目录、全新引擎实例
    h = eng2.home(c.child_id, now=DAY0)
    assert h["pet"]["growth_value"] == gv
    assert h["streak"] == 1


def test_report_structure_and_honest_accuracy(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("报告", now=DAY0)
    _answer_all(eng, c.child_id, DAY0)
    rep = eng.report(c.child_id, now=DAY0)
    assert len(rep["abilities"]) == 5
    assert "this_week" in rep and "honest_note" in rep
    by = {a["ability"]: a for a in rep["abilities"]}
    assert by["logic"]["accuracy"] is not None     # 客观能力：有正确率
    assert by["expression"]["accuracy"] is None     # 主观能力：如实标 None（练习量）
