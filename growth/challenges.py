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
_DIGITS_RE = re.compile(r"\d+")
_MASH_RE = re.compile(r"[a-z0-9\s.,!?~-]+", re.I)   # 纯字母数字 = 乱敲键盘
_NEGATION = ("不", "没", "别", "非")


def _norm(s: Optional[str]) -> str:
    return _NORM_RE.sub("", (s or "").strip()).lower()


def _hit(key: str, ans: str) -> bool:
    """单个关键词是否命中（key/ans 均已规范化）。

    三条规则堵住"子串瞎蒙"：纯数字整词比对（答'19'不算命中'9'）；
    关键词单向出现在答案里（不再反向）；紧邻否定词的出现不算（"不是黄色"不算答对"黄"）。
    """
    if key.isdigit():
        return key in _DIGITS_RE.findall(ans)
    if ans == key:
        return True
    i = ans.find(key)
    while i != -1:
        if not any(n in ans[max(0, i - 2):i] for n in _NEGATION):
            return True
        i = ans.find(key, i + 1)
    return False


def match_answer(keys: list, answer: str) -> bool:
    """客观题匹配入口——题库判分与书籍测验共用同一口径。"""
    ans = _norm(answer)
    if not ans:
        return False
    return any(_hit(_norm(k), ans) for k in keys if _norm(k))


def effort_credit(answer: str) -> tuple[float, str]:
    """表达/创造题的努力学分：不判对错，但挡住无效输入（乱敲不给分、不进高光）。"""
    s = (answer or "").strip()
    if not s:
        return 0.0, "这一关想到什么都可以说，再试试看？"
    if len(set(s)) <= 2 or _MASH_RE.fullmatch(s):
        return 0.2, "嗯…用你自己的话，认真说说看好吗？"
    if len(s) < 6:
        return 0.6, "开了个头啦！能再多说一两句吗？"
    if len(set(s)) / len(s) < 0.4:
        return 0.4, "再说点不一样的内容，会更精彩哦！"
    return 1.0, "说得真好，你的想法很特别！"


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

        - EFFORT（表达/创造）：无对错，effort_credit 度量参与质量（乱敲挡在门外）。
        - OBJECTIVE：match_answer 命中标准答案或任一 accept 关键词即算对；
          答错也给 0.5 的"你尝试了"学分（努力内核：参与就有价值）。
        """
        if challenge.score_mode == ScoreMode.EFFORT:
            credit, feedback = effort_credit(answer)
            return None, credit, feedback

        if not _norm(answer):
            return False, 0.0, "先别急，读一遍题，把你想到的答案说出来。"
        if match_answer([challenge.answer or "", *challenge.accept], answer):
            return True, 1.0, "答对啦，思路很清楚！"
        return False, 0.5, "差一点点——别灰心，我们一起看看。"
