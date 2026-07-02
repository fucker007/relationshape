"""GrowthEngine：成长挑战系统的编排（headless，纯逻辑，时间由参数注入）。

每天 5 题是唯一数据入口，其余全是派生：
  作答 → 判分 → 能力评估（修为）→ 智慧星 → 喂养宠物（灵材/亲和）→ 卡牌 → 高光
  满 5 关 → 连续天数 + 境界推进（孵化→破壳）
  碰一碰 → 战斗属性 → 回合制 battle_log → 双方有所得（输了不罚）

宠物唯一真源是 state.cultivation；活力/云游是派生态。HTTP 层只是把这些方法翻成 JSON。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Optional

from growth import cards as cardmod
from growth import cultivation as cult
from growth.battle import combat_stats, is_friendly, simulate
from growth.cards import mastery_index, mastery_progress
from growth.challenges import ChallengeBank
from growth.persistence import ChildStore
from growth.report import build_report
from growth.rewards import DAILY_COMPLETE_STARS, stars_for_answer
from growth.state import ChildState
from growth.types import ABILITY_ELEMENT, ABILITY_ZH, Ability, KIND_ZH, ScoreMode


def _day(now: datetime) -> str:
    return now.date().isoformat()


def _seed_int(*parts) -> int:
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest(), 16)


class GrowthEngine:
    def __init__(
        self,
        store: Optional[ChildStore] = None,
        bank: Optional[ChallengeBank] = None,
        state_dir: str = "runtime/growth",
    ) -> None:
        self.store = store or ChildStore(state_dir)
        self.bank = bank or ChallengeBank()
        self._cache: dict[str, ChildState] = {}

    # ------------------------------------------------------------------ 状态
    def _get(self, child_id: str) -> ChildState:
        if child_id not in self._cache:
            st = self.store.load(child_id)
            if st is None:
                raise KeyError(child_id)
            self._cache[child_id] = st
        return self._cache[child_id]

    def _save(self, child: ChildState) -> None:
        self.store.save(child)

    # ------------------------------------------------------------------ 每日节拍
    def _ensure_today(self, child: ChildState, day: str) -> None:
        child.abilities.sample_day(day)
        if child.today_day == day and child.today_cids:
            return
        seed = _seed_int(child.child_id, day, child.grade)
        chosen = self.bank.pick_daily(child.ability_levels(), child.grade,
                                      set(child.seen_cids), seed)
        child.today_day = day
        child.today_cids = [c.cid for c in chosen]
        child.today_answered = {}
        for cid in child.today_cids:
            if cid not in child.seen_cids:
                child.seen_cids.append(cid)
        child.seen_cids = child.seen_cids[-300:]

    def _touch_history(self, child: ChildState, day: str) -> dict:
        entry = next((h for h in child.history if h.get("day") == day), None)
        if entry is None:
            entry = {"day": day, "answered": 0, "correct": 0, "completed": False}
            child.history.append(entry)
            child.history = child.history[-200:]
        entry["answered"] = len(child.today_answered)
        entry["correct"] = sum(1 for v in child.today_answered.values() if v.get("correct") is True)
        return entry

    @staticmethod
    def _is_consecutive(prev_iso: str, day_iso: str) -> bool:
        try:
            return (date.fromisoformat(day_iso) - date.fromisoformat(prev_iso)).days == 1
        except ValueError:
            return False

    def _award_cards(self, child: ChildState, day: str, cids: list, events: list) -> None:
        for cid in cids:
            if cid in child.cards:
                continue
            child.own_card(cid)
            c = cardmod.CATALOG.get(cid)
            if not c:
                continue
            label = f"获得卡牌「{c.name}」· {cardmod.RARITIES[c.rarity]}"
            events.append({"kind": "card", "label": label, "card": c.public(1)})
            child.log_event("card", label, day)

    # ------------------------------------------------------------------ 创建 / 列表
    def create_child(
        self, name: str, age: int = 8, grade: int = 2,
        child_id: Optional[str] = None, now: Optional[datetime] = None,
    ) -> ChildState:
        now = now or datetime.now()
        day = _day(now)
        if not child_id:
            n = len(self.store.list_ids()) + 1
            child_id = f"kid{n}"
            while self.store.exists(child_id):
                n += 1
                child_id = f"kid{n}"
        child = ChildState(child_id=child_id, name=name or child_id,
                           age=age, grade=grade, created_day=day)
        self._cache[child_id] = child
        self._ensure_today(child, day)
        child.log_event("created", f"{child.name} 的成长伙伴诞生啦", day)
        self._save(child)
        return child

    def list_children(self, now: Optional[datetime] = None) -> list[dict]:
        out = []
        for fid in self.store.list_ids():
            try:
                child = self._get(fid)
            except KeyError:
                continue
            cs = combat_stats(child)
            out.append({
                "child_id": child.child_id, "name": child.name,
                "age": child.age, "grade": child.grade,
                "realm": child.cultivation.realm,
                "realm_zh": cult.REALM_ZH[child.cultivation.realm],
                "power": cs["battle_power"], "rank": cs["rank"],
                "streak": child.streak, "stars": child.stars,
                "cards": len(child.cards),
                "today_done": len(child.today_answered),
                "today_total": len(child.today_cids),
            })
        return out

    # ------------------------------------------------------------------ 视图
    def _pet_view(self, child: ChildState, day: str) -> dict:
        cv = child.cultivation
        sp = cult.species_info(cv.species)
        st = cv.status(day)
        return {
            "realm": cv.realm, "realm_zh": cult.REALM_ZH[cv.realm],
            "realm_day": cv.realm_day, "days_to_hatch": cult.DAYS_TO_HATCH,
            "species": sp,
            "element": sp["elem"] if sp else cult.DOMAIN_ELEM[cv.dominant()],
            "vitality": st["vitality"], "mode": st["mode"], "status_line": st["line"],
            "materials": dict(cv.materials), "affinity": cv.norm_affinity(),
            "dominant": cv.dominant(), "dominant_zh": cult.DOMAIN_ZH[cv.dominant()],
        }

    def _abilities_view(self, child: ChildState) -> list:
        out = []
        for a in Ability:
            t = child.abilities.track(a)
            mp = mastery_progress(t.level)
            acc = t.accuracy()
            out.append({
                "ability": a.value, "ability_zh": ABILITY_ZH[a],
                "level": round(t.level), "practiced": t.practiced,
                "element": ABILITY_ELEMENT[a],
                "mastery": mp["name"], "mastery_next": mp["next"], "mastery_pct": mp["pct"],
                "accuracy": None if acc is None else round(acc * 100),
            })
        return out

    def _home_view(self, child: ChildState, day: str) -> dict:
        cs = combat_stats(child)
        challenges = []
        for idx, cid in enumerate(child.today_cids):
            c = self.bank.get(cid)
            if not c:
                continue
            pub = c.public()
            pub["answered"] = cid in child.today_answered
            pub["result"] = child.today_answered.get(cid)
            dom = cult.domain_for(c.kind)   # 每道题都是宠物的一口"渴求"
            pub.update(domain=dom, domain_zh=cult.DOMAIN_ZH[dom],
                       material=cult.DOMAIN_MATERIAL[dom], color=cult.DOMAIN_COLOR[dom],
                       craving=cult.craving_for(dom, idx))
            challenges.append(pub)
        done, total = len(child.today_answered), len(child.today_cids)
        return {
            "child_id": child.child_id, "name": child.name,
            "age": child.age, "grade": child.grade,
            "pet": self._pet_view(child, day),
            "abilities": self._abilities_view(child),
            "combat": cs, "power": cs["battle_power"],
            "rank": cs["rank"], "rank_index": cs["rank_index"],
            "stars": child.stars, "streak": child.streak, "best_streak": child.best_streak,
            "cards": cardmod.collection_summary(child.cards),
            "today": {"day": child.today_day, "total": total, "done": done,
                      "all_done": done >= total and total > 0, "challenges": challenges},
            "events": list(reversed(child.events[-8:])),
        }

    def home(self, child_id: str, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        child = self._get(child_id)
        day = _day(now)
        self._ensure_today(child, day)
        self._save(child)
        return self._home_view(child, day)

    def today(self, child_id: str, now: Optional[datetime] = None) -> dict:
        return self.home(child_id, now)["today"]

    def album(self, child_id: str) -> dict:
        """卡册：整个目录 + 拥有数量，按域分组、稀有度降序。"""
        owned = self._get(child_id).cards
        cards = [c.public(owned.get(cid, 0)) for cid, c in cardmod.CATALOG.items()]
        cards.sort(key=lambda c: (c["domain"], -c["rarity"], c["cid"]))
        return {"summary": cardmod.collection_summary(owned), "cards": cards,
                "rarities": cardmod.RARITIES, "rarity_color": cardmod.RARITY_COLOR}

    # ------------------------------------------------------------------ 作答
    def answer(
        self, child_id: str, cid: str, answer_text: str, now: Optional[datetime] = None,
    ) -> dict:
        now = now or datetime.now()
        child = self._get(child_id)
        day = _day(now)
        self._ensure_today(child, day)

        if cid not in child.today_cids:
            return {"error": "not_today", "home": self._home_view(child, day)}
        if cid in child.today_answered:
            return {"already": True, "home": self._home_view(child, day)}

        events: list[dict] = []

        # ---- 到场：若宠物正云游/沉睡，这一到场就把它唤回 ----
        if child.cultivation.touch_active(day):
            events.append({"kind": "return", "label": "它回来啦！云游归来，好像更想你了~"})
            child.log_event("return", "云游归来", day)

        # ---- 判分 → 能力（修为）→ 智慧星 → 喂养 ----
        c = self.bank.get(cid)
        correct, credit, feedback = self.bank.score(c, answer_text)
        old, new = child.abilities.register(c.ability, correct, credit, c.difficulty)
        stars = stars_for_answer(correct, credit)
        child.stars += stars
        dom = cult.domain_for(c.kind)
        child.cultivation.feed(dom, correct, credit)
        fed = {"domain": dom, "domain_zh": cult.DOMAIN_ZH[dom],
               "material": cult.DOMAIN_MATERIAL[dom], "color": cult.DOMAIN_COLOR[dom]}
        child.today_answered[cid] = {"correct": correct, "credit": credit}

        # ---- "火热"当日锁存：客观题 ≥2 且正确率 ≥80% 即点燃，当天不回落 ----
        objs = [v["correct"] for v in child.today_answered.values() if v["correct"] is not None]
        if len(objs) >= 2 and sum(objs) / len(objs) >= 0.8:
            child.hot_day = day

        # ---- 卡牌：答对累积藏品卡 + 修为突破卡 ----
        new_cards: list[str] = []
        if correct:
            before = child.card_progress.get(c.ability.value, 0)
            child.card_progress[c.ability.value] = before + 1
            new_cards += cardmod.on_correct(before, before + 1, c.ability, set(child.cards))
        new_cards += cardmod.on_mastery_up(c.ability, old, new, set(child.cards))
        if mastery_index(new) > mastery_index(old):
            events.append({"kind": "mastery",
                           "label": f"{ABILITY_ZH[c.ability]}修为突破到「{mastery_progress(new)['name']}」！"})
        self._award_cards(child, day, new_cards, events)

        # ---- 高光：认真且完整的表达/创造，存进家长周报 ----
        is_highlight = False
        if c.score_mode == ScoreMode.EFFORT and credit >= 1.0 and len((answer_text or "").strip()) >= 8:
            child.add_highlight(day, c.kind.value, c.ability.value, c.prompt, (answer_text or "").strip())
            is_highlight = True

        child.log_event("answer", f"完成{KIND_ZH[c.kind]}", day,
                        detail="答对" if correct else ("已参与" if correct is None else "再接再厉"))
        self._touch_history(child, day)

        if len(child.today_answered) >= len(child.today_cids) and child.today_cids:
            self._complete_day(child, day, events)

        outcome = {
            "cid": cid, "kind": c.kind.value, "kind_zh": KIND_ZH[c.kind],
            "ability": c.ability.value, "ability_zh": ABILITY_ZH[c.ability],
            "correct": correct, "credit": credit, "feedback": feedback,
            "explain": c.explain, "extend": c.extend,
            "stars_earned": stars, "fed": fed, "is_highlight": is_highlight,
            "new_cards": [cardmod.CATALOG[x].public(1) for x in new_cards if x in cardmod.CATALOG],
        }
        self._save(child)
        return {"outcome": outcome, "events": events, "home": self._home_view(child, day)}

    def _complete_day(self, child: ChildState, day: str, events: list) -> None:
        prev = child.last_completed_day
        if prev == day:
            return
        child.streak = child.streak + 1 if (prev and self._is_consecutive(prev, day)) else 1
        child.last_completed_day = day
        child.best_streak = max(child.best_streak, child.streak)
        child.stars += DAILY_COMPLETE_STARS
        events.append({"kind": "daily_complete", "label": f"今日挑战全部完成！连续 {child.streak} 天"})
        child.log_event("daily_complete", f"完成今日全部挑战（连续 {child.streak} 天）", day)
        self._award_cards(child, day, cardmod.on_streak(child.streak, set(child.cards)), events)

        # ---- 境界推进：喂饱一天长一天；蛋满 7 天破壳成专属宠 ----
        hatched = child.cultivation.advance_day(day)
        if hatched:
            sp = cult.species_info(hatched)
            events.append({"kind": "hatch", "label": f"破壳啦！你领养到「{sp['name']}」",
                           "species": hatched, "species_info": sp})
            child.log_event("hatch", f"破壳 · 领养到「{sp['name']}」", day)

        self._touch_history(child, day)["completed"] = True

    # ------------------------------------------------------------------ 碰一碰对战
    def battle(self, a_id: str, b_id: str, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        if a_id == b_id:
            return {"error": "same_child"}
        A, B = self._get(a_id), self._get(b_id)
        if any(c.cultivation.realm == "egg" for c in (A, B)):
            return {"error": "egg_cannot_battle", "detail": "蛋还没破壳，不能出战"}
        day = _day(now)
        for c in (A, B):
            self._ensure_today(c, day)

        csa, csb = combat_stats(A), combat_stats(B)
        friendly = is_friendly(csa["battle_power"], csb["battle_power"],
                               csa["rank_index"], csb["rank_index"])
        A.battles += 1
        B.battles += 1
        if friendly:
            A.friendly_battles += 1
            B.friendly_battles += 1
        seed = _seed_int(a_id, b_id, day, A.battles + B.battles)
        sim = simulate({**csa, "id": A.child_id, "name": A.name},
                       {**csb, "id": B.child_id, "name": B.name}, seed, friendly)
        winner = sim["winner"]

        # 奖励：切磋皆有所得——星 + 一颗自己最缺的灵材；赢家多一点；输了绝不罚
        def spoils(child, won: bool) -> dict:
            dom = min(cult.DOMAINS, key=lambda d: child.cultivation.materials.get(d, 0))
            n = 2 if won else 1
            child.cultivation.materials[dom] = child.cultivation.materials.get(dom, 0) + n
            return {"stars": 3 if won else 1,
                    "material": {"domain": dom, "domain_zh": cult.DOMAIN_ZH[dom],
                                 "name": cult.DOMAIN_MATERIAL[dom], "n": n}}

        a_rw, b_rw = spoils(A, winner == "a"), spoils(B, winner == "b")
        if friendly:
            weak = "a" if csa["battle_power"] <= csb["battle_power"] else "b"
            (a_rw if weak == "a" else b_rw)["stars"] += 1
        A.stars += a_rw["stars"]
        B.stars += b_rw["stars"]

        ev_a, ev_b = [], []
        self._award_cards(A, day, cardmod.on_battle(A.battles, friendly, set(A.cards)), ev_a)
        self._award_cards(B, day, cardmod.on_battle(B.battles, friendly, set(B.cards)), ev_b)
        A.log_event("battle", f"碰一碰 vs {B.name}：{'赢了' if winner == 'a' else ('友谊赛' if friendly else '惜败')}", day)
        B.log_event("battle", f"碰一碰 vs {A.name}：{'赢了' if winner == 'b' else ('友谊赛' if friendly else '惜败')}", day)
        self._save(A)
        self._save(B)

        wname = A.name if winner == "a" else B.name
        if friendly:
            narration = f"{A.name} 和 {B.name} 来了一场友谊赛——切磋一下，谁都有收获！"
        elif sim["ko"]:
            narration = f"{wname} 一击制胜，KO！（平时的努力，全打在这一下上）"
        else:
            narration = f"{wname} 笑到了最后，凭的是平时一点一滴的努力。"

        def emoji(child):
            sp = cult.species_info(child.cultivation.species)
            return sp["emoji"] if sp else "🥚"

        return {
            "a_id": A.child_id, "b_id": B.child_id, "a_name": A.name, "b_name": B.name,
            "a_emoji": emoji(A), "b_emoji": emoji(B),
            "a_power": csa["battle_power"], "b_power": csb["battle_power"],
            "a_rank": csa["rank"], "b_rank": csb["rank"],
            "a_stats": csa, "b_stats": csb,
            "winner": winner, "friendly": friendly, "ko": sim["ko"],
            "log": sim["events"], "maxhp_a": sim["maxhp_a"], "maxhp_b": sim["maxhp_b"],
            "a_reward": a_rw, "b_reward": b_rw, "narration": narration,
            "a_new_cards": [e["card"] for e in ev_a if e["kind"] == "card"],
            "b_new_cards": [e["card"] for e in ev_b if e["kind"] == "card"],
            "a_home": self._home_view(A, day), "b_home": self._home_view(B, day),
        }

    # ------------------------------------------------------------------ 报告 / 原始态
    def report(self, child_id: str, now: Optional[datetime] = None) -> dict:
        return build_report(self._get(child_id), _day(now or datetime.now()))

    def raw_state(self, child_id: str) -> dict:
        return self._get(child_id).to_dict()
