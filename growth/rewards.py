"""奖励：稀缺、确定性、奖励行为而非人格（刻意不用老虎机式变率强化）。

智慧星是孩子可见的唯一"点数"；成长本体已由喂养承载（cultivation：灵材/亲和/境界），
徽章的角色由卡牌承担（cards：里程碑标志）。这里只剩最简单的一件事。
"""

from __future__ import annotations

from typing import Optional

DAILY_COMPLETE_STARS = 3


def stars_for_answer(correct: Optional[bool], credit: float) -> int:
    """认真参与 1 星；答对再 +1。答错不为零——努力本身有价值。"""
    stars = 1 if credit >= 0.5 else 0
    if correct:
        stars += 1
    return stars
