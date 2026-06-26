"""GrowthEngine：成长挑战系统的编排（headless，纯逻辑，时间由参数注入）。

一次作答流水：判分 → 能力评估（含滚动正确率）→ 努力奖励 → 喂养宠物 → 徽章 →
卡牌掉落（答对藏品卡 / 突破境界卡）→（满 5 关）连续天数 + 完成奖励 + 坚持卡。
碰一碰 → 战斗属性换算 → 回合制对战(battle_log) → 奖励 + 对战卡。

数值双轨：努力→体魄(HP/养成，不罚)；正确率→暴击(锋芒，有上限)。战力 = 战斗属性加权和 + 藏卡(封顶)。
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Optional

from growth import cards as cardmod
from growth import cultivation as cult
from growth.battle import combat_stats, is_friendly, simulate
from growth.cards import realm_index, realm_progress
from growth.challenges import ChallengeBank
from growth.persistence import ChildStore
from growth.report import build_report
from growth.rewards import (
    DAILY_COMPLETE_GROWTH,
    DAILY_COMPLETE_STARS,
    ability_badge_for,
    reward_for_answer,
    streak_badge_for,
)
from growth.state import ChildState
from growth.types import ABILITY_ZH, Ability, KIND_ZH, ScoreMode


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
    def _roll_day(self, child: ChildState, day: str) -> None:
        if child.pet.last_day != day:
            if child.pet.last_day is not None:
                child.pet.daily_decay()
            child.pet.last_day = day
        child.abilities.sample_day(day)

    def _ensure_today(self, child: ChildState, day: str) -> None:
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

    def _update_dominant(self, child: ChildState) -> None:
        child.pet.dominant = child.abilities.strongest().value

    def _touch_history(self, child: ChildState, day: str) -> dict:
        entry = next((h for h in child.history if h.get("day") == day), None)
        if entry is None:
            entry = {"day": day, "answered": 0, "correct": 0, "completed": False, "kinds": []}
            child.history.append(entry)
            child.history = child.history[-200:]
        entry["answered"] = len(child.today_answered)
        entry["correct"] = sum(1 for v in child.today_answered.values() if v.get("correct") is True)
        kinds = []
        for cid in child.today_answered:
            c = self.bank.get(cid)
            if c and c.ability.value not in kinds:
                kinds.append(c.ability.value)
        entry["kinds"] = kinds
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
            events.append({"kind": "card", "label": label, "rarity": c.rarity,
                           "card": c.public(1)})
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
        self._update_dominant(child)
        self._cache[child_id] = child
        self._roll_day(child, day)
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
                "stage_name": child.pet.stage()[1],
                "power": cs["battle_power"], "rank": cs["rank"],
                "streak": child.streak, "stars": child.stars,
                "badges": len(child.badges), "cards": len(child.cards),
                "today_done": len(child.today_answered),
                "today_total": len(child.today_cids),
            })
        return out

    # ------------------------------------------------------------------ 视图
    def _abilities_view(self, child: ChildState) -> list:
        from growth.types import ABILITY_ELEMENT
        out = []
        for a in Ability:
            t = child.abilities.track(a)
            rp = realm_progress(t.level)
            acc = t.accuracy()
            out.append({
                "ability": a.value, "ability_zh": ABILITY_ZH[a],
                "level": round(t.level), "practiced": t.practiced,
                "element": ABILITY_ELEMENT[a],
                "realm": rp["name"], "realm_next": rp["next"], "realm_pct": rp["pct"],
                "accuracy": None if acc is None else round(acc * 100),
            })
        return out

    def _home_view(self, child: ChildState, now: datetime) -> dict:
        cs = combat_stats(child)
        sp = child.pet.stage_progress()
        dom = child.pet.dominant or child.abilities.strongest().value
        from growth.types import ABILITY_ELEMENT
        pet = {
            "species": child.pet.species,
            "stage_index": sp["index"], "stage_name": sp["name"],
            "next_stage": sp["next"], "stage_pct": sp["pct"],
            "growth_value": child.pet.growth_value, "vitality": child.pet.vitality,
            "dominant": dom, "dominant_zh": ABILITY_ZH[Ability(dom)],
            "element": ABILITY_ELEMENT.get(Ability(dom), ""),
            "equipped": dict(child.pet.equipped), "unlocked": list(child.pet.unlocked),
        }
        challenges = []
        for idx, cid in enumerate(child.today_cids):
            c = self.bank.get(cid)
            if not c:
                continue
            pub = c.public()
            pub["answered"] = cid in child.today_answered
            pub["result"] = child.today_answered.get(cid)
            dom = cult.domain_for(c.kind)   # 把每道题包成宠物的"渴求"
            pub["domain"] = dom
            pub["domain_zh"] = cult.DOMAIN_ZH[dom]
            pub["material"] = cult.DOMAIN_MATERIAL[dom]
            pub["color"] = cult.DOMAIN_COLOR[dom]
            pub["craving"] = cult.craving_for(dom, idx)
            challenges.append(pub)
        done, total = len(child.today_answered), len(child.today_cids)

        cv = child.cultivation
        cult_block = {
            "stage": cv.stage, "egg_day": cv.egg_day, "days_to_hatch": cult.DAYS_TO_HATCH,
            "fed_today": done, "feed_total": total or 5,
            "materials": dict(cv.materials), "affinity": cv.norm_affinity(),
            "dominant": cv.dominant(), "dominant_zh": cult.DOMAIN_ZH[cv.dominant()],
            "craving": cult.DAILY_LINES[cv.egg_day % len(cult.DAILY_LINES)],
            "species": cult.species_info(cv.species),
        }
        return {
            "child_id": child.child_id, "name": child.name,
            "age": child.age, "grade": child.grade,
            "pet": pet, "abilities": self._abilities_view(child),
            "cultivation": cult_block,
            "combat": cs, "power": cs["battle_power"],
            "rank": cs["rank"], "rank_index": cs["rank_index"],
            "stars": child.stars, "badges": list(child.badges),
            "streak": child.streak, "best_streak": child.best_streak,
            "cards": cardmod.collection_summary(child.cards),
            "today": {"day": child.today_day, "total": total, "done": done,
                      "all_done": done >= total and total > 0, "challenges": challenges},
            "events": list(reversed(child.events[-8:])),
        }

    def home(self, child_id: str, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        child = self._get(child_id)
        day = _day(now)
        self._roll_day(child, day)
        self._ensure_today(child, day)
        self._save(child)
        return self._home_view(child, now)

    def today(self, child_id: str, now: Optional[datetime] = None) -> dict:
        return self.home(child_id, now)["today"]

    def album(self, child_id: str) -> dict:
        """卡册：整个目录 + 拥有数量，按稀有度降序、域分组排。"""
        child = self._get(child_id)
        owned = child.cards
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
        self._roll_day(child, day)
        self._ensure_today(child, day)

        if cid not in child.today_cids:
            return {"error": "not_today", "home": self._home_view(child, now)}
        if cid in child.today_answered:
            return {"already": True, "home": self._home_view(child, now)}

        c = self.bank.get(cid)
        correct, credit, feedback = self.bank.score(c, answer_text)
        old, new = child.abilities.register(c.ability, correct, credit, c.difficulty)
        child.abilities.sample_day(day)
        is_effort = c.score_mode == ScoreMode.EFFORT
        stars, growth = reward_for_answer(correct, credit, is_effort, child.streak)
        child.stars += stars
        child.pet.nourish(growth)
        child.today_answered[cid] = {"correct": correct, "credit": credit}
        self._update_dominant(child)

        # ---- 孵化层：每道题 = 喂蛋一颗灵材（仅蛋阶段）----
        fed = None
        if child.cultivation.stage == "egg":
            dom = cult.domain_for(c.kind)
            child.cultivation.feed(dom, correct, credit)
            fed = {"domain": dom, "domain_zh": cult.DOMAIN_ZH[dom],
                   "material": cult.DOMAIN_MATERIAL[dom], "color": cult.DOMAIN_COLOR[dom]}

        events: list[dict] = []

        # ---- 卡牌：答对累积藏品卡 + 突破境界卡 ----
        new_cards: list[str] = []
        if correct:
            before = child.card_progress.get(c.ability.value, 0)
            after = before + 1
            child.card_progress[c.ability.value] = after
            new_cards += cardmod.on_correct(before, after, c.ability, set(child.cards))
        new_cards += cardmod.on_realm_up(c.ability, old, new, set(child.cards))
        if realm_index(new) > realm_index(old):
            events.append({"kind": "realm",
                           "label": f"{ABILITY_ZH[c.ability]}突破到「{realm_progress(new)['name']}」境界！"})
        self._award_cards(child, day, new_cards, events)

        # ---- 高光 / 徽章 ----
        is_highlight = False
        if is_effort and credit >= 1.0 and len((answer_text or "").strip()) >= 8:
            child.add_highlight(day, c.kind.value, c.ability.value, c.prompt, (answer_text or "").strip())
            is_highlight = True
        ab_badge = ability_badge_for(old, new, c.ability)
        if ab_badge and child.add_badge(ab_badge):
            self._on_badge(child, day, ab_badge, events, ability=c.ability)

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
            "stars_earned": stars, "growth_earned": growth, "is_highlight": is_highlight,
            "new_cards": [cardmod.CATALOG[x].public(1) for x in new_cards if x in cardmod.CATALOG],
            "fed": fed,
        }
        self._save(child)
        return {"outcome": outcome, "events": events, "home": self._home_view(child, now)}

    def _on_badge(self, child, day, badge_name, events, ability: Optional[Ability] = None) -> None:
        events.append({"kind": "badge", "label": f"获得{badge_name}"})
        child.log_event("badge", f"获得{badge_name}", day)
        if len(child.badges) == 1:
            u = child.pet.unlock("badge_first")
            if u:
                events.append({"kind": "item", "label": f"解锁装扮：{u[1]}"})
                child.log_event("item", f"解锁装扮：{u[1]}", day)
        if ability is not None:
            u = child.pet.unlock(f"badge_{ability.value}")
            if u:
                events.append({"kind": "item", "label": f"解锁装扮：{u[1]}"})
                child.log_event("item", f"解锁装扮：{u[1]}", day)

    def _complete_day(self, child, day, events) -> None:
        prev = child.last_completed_day
        if prev == day:
            return
        if prev is None:
            child.streak = 1
        elif self._is_consecutive(prev, day):
            child.streak += 1
        else:
            child.streak = 1
        child.last_completed_day = day
        child.best_streak = max(child.best_streak, child.streak)
        child.pet.nourish(DAILY_COMPLETE_GROWTH)
        child.stars += DAILY_COMPLETE_STARS
        events.append({"kind": "daily_complete", "label": f"今日挑战全部完成！连续 {child.streak} 天"})
        child.log_event("daily_complete", f"完成今日全部挑战（连续 {child.streak} 天）", day)

        sb = streak_badge_for(child.streak)
        if sb and child.add_badge(sb):
            self._on_badge(child, day, sb, events)
        for thr, key in [(3, "streak3"), (7, "streak7"), (30, "streak30")]:
            if child.streak >= thr:
                u = child.pet.unlock(key)
                if u:
                    events.append({"kind": "item", "label": f"解锁装扮：{u[1]}"})
                    child.log_event("item", f"解锁装扮：{u[1]}", day)
        self._award_cards(child, day, cardmod.on_streak(child.streak, set(child.cards)), events)

        # ---- 孵化推进：今日喂饱 → 蛋长一天；满 7 天破壳成专属宠 ----
        hatched = child.cultivation.advance_day(day)
        if hatched:
            sp = cult.species_info(hatched)
            events.append({"kind": "hatch", "label": f"破壳啦！你领养到「{sp['name']}」",
                           "species": hatched, "species_info": sp})
            child.log_event("hatch", f"破壳 · 领养到「{sp['name']}」", day)

        entry = self._touch_history(child, day)
        entry["completed"] = True

    # ------------------------------------------------------------------ 碰一碰对战
    def battle(self, a_id: str, b_id: str, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        if a_id == b_id:
            return {"error": "same_child"}
        A, B = self._get(a_id), self._get(b_id)
        day = _day(now)
        for c in (A, B):
            self._roll_day(c, day)
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
        Ad = {**csa, "id": A.child_id, "name": A.name}
        Bd = {**csb, "id": B.child_id, "name": B.name}
        sim = simulate(Ad, Bd, seed, friendly)
        winner = sim["winner"]

        a_rw, b_rw = {"stars": 1, "growth": 8}, {"stars": 1, "growth": 8}
        if winner == "a":
            a_rw = {"stars": 3, "growth": 14}
        elif winner == "b":
            b_rw = {"stars": 3, "growth": 14}
        if friendly:
            weak = "a" if csa["battle_power"] <= csb["battle_power"] else "b"
            (a_rw if weak == "a" else b_rw)["stars"] += 1
        A.stars += a_rw["stars"]
        A.pet.nourish(a_rw["growth"])
        B.stars += b_rw["stars"]
        B.pet.nourish(b_rw["growth"])

        ev_a, ev_b = [], []
        self._award_cards(A, day, cardmod.on_battle(A.battles, friendly, set(A.cards)), ev_a)
        self._award_cards(B, day, cardmod.on_battle(B.battles, friendly, set(B.cards)), ev_b)

        a_res = "赢了" if winner == "a" else ("友谊赛" if friendly else "惜败")
        b_res = "赢了" if winner == "b" else ("友谊赛" if friendly else "惜败")
        A.log_event("battle", f"碰一碰 vs {B.name}：{a_res}", day)
        B.log_event("battle", f"碰一碰 vs {A.name}：{b_res}", day)
        self._save(A)
        self._save(B)

        wname = A.name if winner == "a" else B.name
        if friendly:
            narration = f"{A.name} 和 {B.name} 来了一场友谊赛——切磋一下，谁都有收获！"
        elif sim["ko"]:
            narration = f"{wname} 一击制胜，KO！（平时的努力，全打在这一下上）"
        else:
            narration = f"{wname} 笑到了最后，凭的是平时一点一滴的努力。"

        return {
            "a_id": A.child_id, "b_id": B.child_id, "a_name": A.name, "b_name": B.name,
            "a_power": csa["battle_power"], "b_power": csb["battle_power"],
            "a_rank": csa["rank"], "b_rank": csb["rank"],
            "a_stats": csa, "b_stats": csb,
            "winner": winner, "friendly": friendly, "ko": sim["ko"],
            "log": sim["events"], "maxhp_a": sim["maxhp_a"], "maxhp_b": sim["maxhp_b"],
            "a_reward": a_rw, "b_reward": b_rw, "narration": narration,
            "a_new_cards": [e["card"] for e in ev_a if e["kind"] == "card"],
            "b_new_cards": [e["card"] for e in ev_b if e["kind"] == "card"],
            "a_home": self._home_view(A, now), "b_home": self._home_view(B, now),
        }

    # ------------------------------------------------------------------ 报告 / 原始态
    def report(self, child_id: str, now: Optional[datetime] = None) -> dict:
        return build_report(self._get(child_id))

    def raw_state(self, child_id: str) -> dict:
        return self._get(child_id).to_dict()
