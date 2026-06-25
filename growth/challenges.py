"""个性化挑战：从题库按能力 / 难度 / 年级自适应抽题，并在服务端判分。

抽题是确定性的（按 seed 派生），便于回放与测试——和引擎"时间由参数注入"同样的可测性。
判分全在服务端：客户端拿不到答案，自然作弊不了，也无法把判分逻辑漏进浏览器。
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from growth.bank_data import load_bank
from growth.types import (
    Ability,
    Challenge,
    ChallengeKind,
    DAILY_ORDER,
    KIND_ABILITY,
    ScoreMode,
)

_NORM_RE = re.compile(r"[\s，,。.、!！?？;；:：\"'·`（）()\[\]【】]+")


def _norm(s: Optional[str]) -> str:
    return _NORM_RE.sub("", (s or "").strip()).lower()


def _target_difficulty(level: float, grade: int) -> int:
    """按能力等级定目标难度，并用年级封顶（一年级不丢三年级的题）。"""
    if level < 35:
        d = 1
    elif level < 70:
        d = 2
    else:
        d = 3
    return max(1, min(d, grade))


def _seed_int(*parts) -> int:
    raw = "|".join(str(p) for p in parts)
    return int(hashlib.md5(raw.encode()).hexdigest(), 16)


class ChallengeBank:
    def __init__(self, items: Optional[list[Challenge]] = None) -> None:
        self.items = items if items is not None else load_bank()
        self._by_id = {c.cid: c for c in self.items}

    def get(self, cid: str) -> Optional[Challenge]:
        return self._by_id.get(cid)

    def by_kind(self, kind: ChallengeKind) -> list[Challenge]:
        return [c for c in self.items if c.kind == kind]

    def pick_daily(
        self,
        ability_levels: dict,
        grade: int,
        seen_ids: set,
        seed: int,
    ) -> list[Challenge]:
        """每日 5 关，每关一种能力，难度按该能力等级自适应；尽量不重复历史题。"""
        chosen: list[Challenge] = []
        for kind in DAILY_ORDER:
            ab = KIND_ABILITY[kind]
            level = float(ability_levels.get(ab.value, 12.0))
            target = _target_difficulty(level, grade)
            pool = self.by_kind(kind)

            # 优先：目标难度 & 没做过；逐步放宽，最后允许重复（小题库兜底）
            tiers = [
                [c for c in pool if c.difficulty == target and c.cid not in seen_ids],
                [c for c in pool if abs(c.difficulty - target) <= 1 and c.cid not in seen_ids],
                [c for c in pool if c.cid not in seen_ids],
                pool,
            ]
            picked = None
            for tier in tiers:
                if tier:
                    idx = _seed_int(seed, kind.value, len(tier)) % len(tier)
                    picked = sorted(tier, key=lambda c: c.cid)[idx]
                    break
            if picked is not None:
                chosen.append(picked)
        return chosen

    def score(self, challenge: Challenge, answer: str) -> tuple[Optional[bool], float, str]:
        """判分。返回 (correct|None, credit 0..1, feedback)。

        - EFFORT（表达/创造）：无对错，只看是否认真参与；哪怕一句也给参与分。
        - OBJECTIVE：标准答案规范化比较，或命中任一 accept 关键词即算对；
          答错也给 0.5 的"你尝试了"学分（努力内核：参与就有价值）。
        """
        ans = answer or ""
        if challenge.score_mode == ScoreMode.EFFORT:
            n = len(ans.strip())
            if n == 0:
                return None, 0.0, "这一关想到什么都可以说，再试试看？"
            if n < 6:
                return None, 0.6, "开了个头啦！能再多说一两句吗？"
            return None, 1.0, "说得真好，你的想法很特别！"

        na = _norm(ans)
        if not na:
            return False, 0.0, "先别急，读一遍题，把你想到的答案说出来。"
        keys = [challenge.answer or ""] + list(challenge.accept)
        hit = any(_norm(k) and (_norm(k) in na or na in _norm(k)) for k in keys)
        if hit:
            return True, 1.0, "答对啦，思路很清楚！"
        return False, 0.5, "差一点点——别灰心，我们一起看看。"
