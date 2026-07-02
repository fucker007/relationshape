"""家长报告：看见"成长"，而且是**真实数值**的体现——不是糊一个分数。

诚实落地：
- 每能力给真实的滚动正确率(近 20 题) + 修为境界 + 一周趋势；表达/创造正确率如实为 None。
- 战力给**拆解**，让家长看清因果：体魄来自坚持、暴击来自正确率……
- 最打动人的仍是"具体片段"(本周高光)，放在分数前面。
"""

from __future__ import annotations

from datetime import date, timedelta

from growth.battle import combat_stats
from growth.cards import collection_summary, mastery_progress
from growth.cultivation import REALM_ZH
from growth.types import ABILITY_ZH, Ability


def _level_on(history: list, cutoff_day: str, fallback: float) -> float:
    """cutoff_day（含）之前最近一次采样；此前无采样则取最早样本。按真实日历，跳天不失真。"""
    level = None
    for d, v in history:
        if d <= cutoff_day:
            level = v
        else:
            break
    if level is None:
        level = history[0][1] if history else fallback
    return float(level)


def build_report(child, day: str) -> dict:
    week_cutoff = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
    abilities, best = [], None
    for a in Ability:
        t = child.abilities.track(a)
        now = round(t.level)
        ago = round(_level_on(t.history, week_cutoff, t.level))
        acc = t.accuracy()
        mp = mastery_progress(t.level)
        item = {
            "ability": a.value, "ability_zh": ABILITY_ZH[a],
            "now": now, "week_ago": ago, "delta": now - ago,
            "practiced": t.practiced,
            "accuracy": None if acc is None else round(acc * 100),   # 近期真实正确率
            "mastery": mp["name"], "mastery_next": mp["next"], "mastery_pct": mp["pct"],
        }
        abilities.append(item)
        if best is None or item["delta"] > best["delta"]:
            best = item

    week = child.history[-7:]
    cs = combat_stats(child)
    return {
        "child_id": child.child_id, "name": child.name,
        "abilities": abilities,
        "combat": {"power": cs["battle_power"], "realm_zh": REALM_ZH[child.cultivation.realm],
                   "breakdown": cs["breakdown"], "accuracy": round(cs["accuracy"] * 100)},
        "cards": collection_summary(child.cards),
        "this_week": {
            "days_active": len(week),
            "completed_days": sum(1 for h in week if h.get("completed")),
            "challenges_done": sum(int(h.get("answered", 0)) for h in week),
            "streak": child.streak, "best_streak": child.best_streak,
            "stars": child.stars,
        },
        "biggest_improvement": (
            {"ability_zh": best["ability_zh"], "delta": best["delta"]}
            if best and best["delta"] > 0 else None
        ),
        "highlights": list(reversed(child.highlights[-5:])),
        "honest_note": (
            "成长是努力的累积，不是一张分数表。战力里的「体魄」来自坚持、「暴击」来自正确率——"
            "都是孩子真实做出来的。表达力 / 创造力看的是练习活跃度，不做对错评分。"
        ),
    }
