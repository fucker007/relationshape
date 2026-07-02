"""共享类型：能力维度、挑战、作答结果、对战的公共数据语言。

这里只有数据与枚举，没有业务逻辑——和 relationshape/types.py 同一种气质。
整套"成长挑战"系统的模块都说这套语言：
  能力评估 → 个性化挑战 → 成长奖励 → 养成宠物 → 碰一碰对战 → 家长报告。

设计红线（贯穿所有模块，与 relationshape 的伦理一致）：
- 宠物"被滋养"与对战"战力"只来自**努力**（出勤 / 尝试 / 坚持 / 表达），
  不来自答对率，更不来自充值。答对错只喂"能力评估"，不喂"宠物情感"。
  → 落后的孩子不会因为答错就把宠物饿死；这既是儿童伦理，也是对家长的卖点。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class Ability(str, enum.Enum):
    LOGIC = "logic"             # 逻辑力
    EXPRESSION = "expression"   # 表达力
    FOCUS = "focus"             # 专注力
    OBSERVATION = "observation" # 观察力
    CREATION = "creation"       # 创造力


ABILITY_ZH = {
    Ability.LOGIC: "逻辑力",
    Ability.EXPRESSION: "表达力",
    Ability.FOCUS: "专注力",
    Ability.OBSERVATION: "观察力",
    Ability.CREATION: "创造力",
}

# 能力 → 元素（洛克王国式"属性"，给对战加点味道；纯展示，不改变努力内核）
ABILITY_ELEMENT = {
    Ability.LOGIC: "晶",
    Ability.EXPRESSION: "声",
    Ability.FOCUS: "光",
    Ability.OBSERVATION: "风",
    Ability.CREATION: "焰",
}


class ChallengeKind(str, enum.Enum):
    """每日 5 关，一关对应一种能力（产品：脑力热身 / 逻辑 / 表达 / 观察 / 创造）。"""

    WARMUP = "warmup"           # 脑力热身（30秒）→ 专注
    LOGIC = "logic"             # 逻辑挑战
    EXPRESSION = "expression"   # 表达挑战
    OBSERVATION = "observation" # 观察挑战
    CREATION = "creation"       # 创造挑战


KIND_ZH = {
    ChallengeKind.WARMUP: "脑力热身",
    ChallengeKind.LOGIC: "逻辑挑战",
    ChallengeKind.EXPRESSION: "表达挑战",
    ChallengeKind.OBSERVATION: "观察挑战",
    ChallengeKind.CREATION: "创造挑战",
}

# 每关主要锻炼的能力
KIND_ABILITY = {
    ChallengeKind.WARMUP: Ability.FOCUS,
    ChallengeKind.LOGIC: Ability.LOGIC,
    ChallengeKind.EXPRESSION: Ability.EXPRESSION,
    ChallengeKind.OBSERVATION: Ability.OBSERVATION,
    ChallengeKind.CREATION: Ability.CREATION,
}

# 每日挑战的固定顺序（产品：5 个挑战的出场顺序）
DAILY_ORDER = [
    ChallengeKind.WARMUP,
    ChallengeKind.LOGIC,
    ChallengeKind.EXPRESSION,
    ChallengeKind.OBSERVATION,
    ChallengeKind.CREATION,
]


class ScoreMode(str, enum.Enum):
    OBJECTIVE = "objective"  # 有标准答案：可判对错（热身 / 逻辑 / 观察）
    EFFORT = "effort"        # 无标准答案：表达 / 创造，只看参与，不判对错


@dataclass
class Challenge:
    cid: str
    kind: ChallengeKind
    ability: Ability
    difficulty: int                    # 1..3（对应小学 1-3 年级）
    prompt: str
    score_mode: ScoreMode
    answer: Optional[str] = None       # 客观题标准答案（规范化后比较）
    accept: list[str] = field(default_factory=list)   # 可接受的等价/关键词答案
    options: list[str] = field(default_factory=list)  # 选择题选项（可空）
    explain: str = ""                  # 答错 / 答完后的讲解
    extend: str = ""                   # 引申一步（讲解引申）

    def public(self) -> dict:
        """发给客户端的题面——**绝不含答案/讲解**。

        判分逻辑完全在服务端：客户端连正确答案都拿不到，自然无法把业务逻辑
        漏到浏览器里。这是"服务端/客户端完全分离"的一道物理保证。
        """
        return {
            "cid": self.cid,
            "kind": self.kind.value,
            "kind_zh": KIND_ZH[self.kind],
            "ability": self.ability.value,
            "ability_zh": ABILITY_ZH[self.ability],
            "difficulty": self.difficulty,
            "prompt": self.prompt,
            "score_mode": self.score_mode.value,
            "options": list(self.options),
        }
