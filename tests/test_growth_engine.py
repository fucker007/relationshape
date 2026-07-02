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


def _hatch(eng, cid, start, mode="correct"):
    """喂满 7 天让蛋破壳（对战前置：蛋不可战）。"""
    for d in range(7):
        _answer_all(eng, cid, start + timedelta(days=d), mode)


def _item(eng, prompt_sub):
    """按题面子串取题——不依赖 cid 编号，题库扩容后依然稳定。"""
    return next(c for c in eng.bank.items if prompt_sub in c.prompt)


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


def test_egg_cannot_battle(tmp_path):
    eng = _eng(tmp_path)
    a = eng.create_child("蛋一", now=DAY0)
    b = eng.create_child("蛋二", now=DAY0)
    assert eng.battle(a.child_id, b.child_id, now=DAY0)["error"] == "egg_cannot_battle"


def test_battle_loser_still_rewarded(tmp_path):
    eng = _eng(tmp_path)
    strong = eng.create_child("强", now=DAY0)
    weak = eng.create_child("弱", now=DAY0)
    _hatch(eng, strong.child_id, DAY0)
    _hatch(eng, weak.child_id, DAY0)
    for d in range(7, 20):                          # 强者继续练，拉开差距
        _answer_all(eng, strong.child_id, DAY0 + timedelta(days=d))
    res = eng.battle(strong.child_id, weak.child_id, now=DAY0 + timedelta(days=20))
    assert "error" not in res
    loser = "b" if res["winner"] == "a" else "a"
    assert res[f"{loser}_reward"]["growth"] > 0     # 输了不被罚
    assert res[f"{loser}_reward"]["stars"] > 0


def test_battle_log_is_playable(tmp_path):
    eng = _eng(tmp_path)
    a = eng.create_child("甲", now=DAY0)
    b = eng.create_child("乙", now=DAY0)
    _hatch(eng, a.child_id, DAY0)
    _hatch(eng, b.child_id, DAY0)
    res = eng.battle(a.child_id, b.child_id, now=DAY0 + timedelta(days=7))
    assert res["log"] and len(res["log"]) >= 2
    ev = res["log"][0]
    for k in ("actor", "foe", "move", "dmg", "crit", "hp_a", "hp_b", "pct_a", "pct_b"):
        assert k in ev
    assert res["log"][-1]["pct_a"] < 100 or res["log"][-1]["pct_b"] < 100   # 有人掉血了


def test_scoring_rejects_superstring_and_negation(tmp_path):
    """P0 判分红线：'19' 不算答对 '9'；'不是黄色' 不算答对 '黄'。"""
    eng = _eng(tmp_path)
    seq = _item(eng, "1, 3, 5, 7")
    assert eng.bank.score(seq, "19")[0] is False
    assert eng.bank.score(seq, "9")[0] is True
    color = _item(eng, "红、黄")
    assert eng.bank.score(color, "不是黄色")[0] is False
    assert eng.bank.score(color, "我觉得是黄色")[0] is True


def test_effort_garbage_gets_low_credit_and_no_highlight(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("效", now=DAY0)
    expr = [ch for ch in eng.home(c.child_id, now=DAY0)["today"]["challenges"]
            if ch["kind"] == "expression"][0]
    r = eng.answer(c.child_id, expr["cid"], "aaaaaaaaaa", now=DAY0)
    assert r["outcome"]["credit"] <= 0.2
    assert eng._get(c.child_id).highlights == []    # 乱敲不进家长周报"亮点"


def test_hot_form_latches_for_the_day(tmp_path):
    """上午打出火热，下午失误不回落（当日锁存）。"""
    eng = _eng(tmp_path)
    c = eng.create_child("热", now=DAY0)
    by_kind = {ch["kind"]: ch for ch in eng.home(c.child_id, now=DAY0)["today"]["challenges"]}
    for kind in ("warmup", "logic"):
        item = eng.bank.get(by_kind[kind]["cid"])
        eng.answer(c.child_id, item.cid, item.answer, now=DAY0)
    assert eng.home(c.child_id, now=DAY0)["combat"]["form"] == "hot"
    eng.answer(c.child_id, by_kind["observation"]["cid"], "完全不对的胡乱回答", now=DAY0)
    assert eng.home(c.child_id, now=DAY0)["combat"]["form"] == "hot"


def test_report_week_ago_uses_calendar(tmp_path):
    """练了 7 天后停 13 天再回来：'周初'应取日历上 7 天前的水平，而非倒数第 8 个采样。"""
    from growth.types import Ability
    eng = _eng(tmp_path)
    c = eng.create_child("历", now=DAY0)
    for d in range(7):
        _answer_all(eng, c.child_id, DAY0 + timedelta(days=d))
    _answer_all(eng, c.child_id, DAY0 + timedelta(days=20))
    rep = eng.report(c.child_id, now=DAY0 + timedelta(days=20))
    tr = eng._get(c.child_id).abilities.track(Ability.LOGIC)
    day6 = (DAY0 + timedelta(days=6)).date().isoformat()
    expect = round(dict((d, v) for d, v in tr.history)[day6])
    logic = next(a for a in rep["abilities"] if a["ability"] == "logic")
    assert logic["week_ago"] == expect


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


def test_egg_frames_5_questions_as_cravings(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("蛋", now=DAY0)
    h = eng.home(c.child_id, now=DAY0)
    assert h["cultivation"]["stage"] == "egg" and h["cultivation"]["egg_day"] == 0
    for ch in h["today"]["challenges"]:
        assert ch["domain"] in ("li", "wen", "bo")
        assert ch["material"] and ch["craving"]     # 每题都被宠物包成"渴求"


def test_feeding_produces_material(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("蛋", now=DAY0)
    ch = eng.home(c.child_id, now=DAY0)["today"]["challenges"][0]
    o = eng.answer(c.child_id, ch["cid"], "随便", now=DAY0)["outcome"]
    assert o["fed"] and o["fed"]["material"]
    assert sum(eng.home(c.child_id, now=DAY0)["cultivation"]["materials"].values()) >= 1


def test_hatches_after_7_days(tmp_path):
    eng = _eng(tmp_path)
    c = eng.create_child("蛋", now=DAY0)
    for d in range(7):
        _answer_all(eng, c.child_id, DAY0 + timedelta(days=d))
    h = eng.home(c.child_id, now=DAY0 + timedelta(days=6))
    assert h["cultivation"]["stage"] == "youth"
    assert h["cultivation"]["species"]            # 第 7 天领养到一只专属宠


def test_species_personalized_by_affinity(tmp_path):
    """客观题全错、表达/创造认真 → 文科亲和最高 → 破壳成「言灵狐」。"""
    eng = _eng(tmp_path)
    c = eng.create_child("文", now=DAY0)
    for d in range(7):
        now = DAY0 + timedelta(days=d)
        for ch in eng.home(c.child_id, now=now)["today"]["challenges"]:
            cobj = eng.bank.get(ch["cid"])
            ans = ("错误答案" if cobj.score_mode == ScoreMode.OBJECTIVE
                   else "我想讲一个很长的故事，关于一只会飞的猫和它的奇妙冒险")
            eng.answer(c.child_id, ch["cid"], ans, now=now)
    sp = eng.home(c.child_id, now=DAY0 + timedelta(days=6))["cultivation"]["species"]
    assert sp["name"] == "言灵狐"


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
