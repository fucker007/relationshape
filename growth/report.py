"""家长报告：看见"成长"，而不是"答对多少题"。

诚实原则落到这里：能力趋势用真实采样点画；但最打动家长、也最难造假的，是**具体证据**
——孩子本周最精彩的一段表达、最有想象力的一次回答。报告把"片段"放在"分数"之前。
"""

from __future__ import annotations

from growth.types import ABILITY_ZH, Ability


def _week_ago_level(history: list, current: float) -> float:
    """从能力采样里取'约一周前'的等级；样本不足就用最早的样本。"""
    if not history:
        return current
    if len(history) >= 8:
        return float(history[-8][1])
    return float(history[0][1])


def build_report(child) -> dict:
    abilities = []
    best = None
    for a in Ability:
        t = child.abilities.track(a)
        now = round(t.level)
        ago = round(_week_ago_level(t.history, t.level))
        delta = now - ago
        acc = (round(100 * t.objective_correct / t.objective_seen)
               if t.objective_seen else None)
        item = {
            "ability": a.value,
            "ability_zh": ABILITY_ZH[a],
            "now": now,
            "week_ago": ago,
            "delta": delta,
            "practiced": t.practiced,
            "accuracy": acc,        # 仅客观能力有；表达/创造为 None（如实标注"练习量"）
        }
        abilities.append(item)
        if best is None or delta > best["delta"]:
            best = item

    week = child.history[-7:]
    days_active = len(week)
    completed_days = sum(1 for h in week if h.get("completed"))
    challenges_done = sum(int(h.get("answered", 0)) for h in week)

    return {
        "child_id": child.child_id,
        "name": child.name,
        "abilities": abilities,
        "this_week": {
            "days_active": days_active,
            "completed_days": completed_days,
            "challenges_done": challenges_done,
            "streak": child.streak,
            "best_streak": child.best_streak,
            "stars": child.stars,
            "badges": list(child.badges),
        },
        "biggest_improvement": (
            {"ability_zh": best["ability_zh"], "delta": best["delta"]}
            if best and best["delta"] > 0 else None
        ),
        "highlights": list(reversed(child.highlights[-5:])),   # 最近 5 条亮点，新的在前
        "honest_note": (
            "成长是努力的累积，不是一张分数表。这里给你看的是孩子的练习趋势，"
            "以及最真实的几个片段——表达力 / 创造力是练习活跃度，不做对错评分。"
        ),
    }
