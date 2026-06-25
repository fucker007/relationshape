"""成长奖励：稀缺、来自努力、不制造依赖（与 relationshape/reward.py 一脉相承）。

红线：宠物成长值与智慧星来自**努力**——认真参与就给，答对只额外多一点点鼓励，
答错绝不扣、绝不让宠物挨饿。坚持（连续天数）是最大的乘数，因为产品的终极目标是
"每天 5 分钟，坚持一年"。刻意不用变率（老虎机式）随机奖励。
"""

from __future__ import annotations

from typing import Optional

from growth.types import ABILITY_BADGE, Ability

DAILY_COMPLETE_GROWTH = 20
DAILY_COMPLETE_STARS = 3

_STREAK_BADGES = {3: "坚持三天", 7: "一周不断", 14: "两周达人", 30: "月度坚持", 100: "百日坚持"}
_ABILITY_BADGE_LEVEL = 60.0   # 能力跨过 60 → 颁该能力徽章（稀缺，值得一颁）


def reward_for_answer(
    correct: Optional[bool], credit: float, is_effort: bool, streak: int,
) -> tuple[int, int]:
    """一次作答给多少 (智慧星, 成长值)。全部以努力为本。"""
    growth = round(6 * credit)
    stars = 1 if credit >= 0.5 else 0
    if correct:                       # 答对的小小额外鼓励——不是大头
        stars += 1
        growth += 2
    if is_effort and credit >= 0.8:   # 表达/创造认真说了一大段
        stars += 1
        growth += 2
    streak_mult = 1.0 + min(streak, 12) * 0.05   # 坚持是最大乘数
    growth = round(growth * streak_mult)
    return stars, growth


def ability_badge_for(level_before: float, level_after: float, ability: Ability) -> Optional[str]:
    if level_before < _ABILITY_BADGE_LEVEL <= level_after:
        return ABILITY_BADGE[ability]
    return None


def streak_badge_for(streak: int) -> Optional[str]:
    return _STREAK_BADGES.get(streak)
