"""孵化/养成层：每天 5 题是唯一数据入口，这里把它派生成"喂养灵材 → 孵蛋 → 破壳成专属宠"。

设计（游戏规划师视角）：
- 蛋是饿的、想破壳的。每道题 = 它表达一种**知识渴求**，作答 = 喂它一颗"灵材"。
- 知识域 3 类：理科(晶尘) / 文科(韵露) / 博物(灵芝)；5 道题按 kind 各归其域。
- 养满 7 天（7 个"喂饱日"）才破壳；**破壳成哪种宠，看这 7 天哪个域的"喂养质量"最高**
  ——用"平均质量"判定（机会公平，1 道题的域不吃亏），所以不同孩子领养到不同的宠。

这里只读"5 题"的作答信号，绝不另开任务。纯标准库、可测、时间由上层注入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from growth.types import ChallengeKind

DAYS_TO_HATCH = 7

DOMAINS = ["li", "wen", "bo"]
DOMAIN_ZH = {"li": "理科", "wen": "文科", "bo": "博物"}
DOMAIN_MATERIAL = {"li": "晶尘", "wen": "韵露", "bo": "灵芝"}
DOMAIN_ELEM = {"li": "晶", "wen": "声", "bo": "风"}
DOMAIN_COLOR = {"li": "#4aa3ff", "wen": "#b07bff", "bo": "#46c98b"}

# 每道题(kind) → 知识域
KIND_DOMAIN = {
    ChallengeKind.WARMUP: "li", ChallengeKind.LOGIC: "li",
    ChallengeKind.EXPRESSION: "wen",
    ChallengeKind.OBSERVATION: "bo", ChallengeKind.CREATION: "bo",
}

# 宠物物种（按主导域）：破壳后领养到的专属宠
SPECIES = {
    "li": {"name": "晶角龙", "emoji": "🐲", "elem": "晶",
           "blurb": "它从逻辑与数字里诞生，偏爱一切有规律的东西。"},
    "wen": {"name": "言灵狐", "emoji": "🦊", "elem": "声",
            "blurb": "它由语言与故事孕育，最爱听你把世界讲给它听。"},
    "bo": {"name": "观澜枭", "emoji": "🦉", "elem": "风",
           "blurb": "它生于好奇与观察，对万物都睁着好奇的眼睛。"},
}

# 渴求文案：宠物用来"表达"某一道题
CRAVINGS = {
    "li": ["我闻到逻辑的香味了…帮我找一颗「晶尘」好吗？", "有规律的东西最好吃啦！", "数字和推理，是我的最爱~"],
    "wen": ["好想听你讲点什么…喂我一滴「韵露」吧。", "用你的话，把世界说给我听好不好？", "文字里藏着魔法，我饿啦~"],
    "bo": ["外面的世界好神奇…帮我采一株「灵芝」？", "睁大眼睛，你看到了什么？", "好奇的东西，最有营养！"],
}
DAILY_LINES = ["你来啦！我等好久了~", "我能感觉到，今天又要长大一点。", "今天也一起加油哦！", "我饿啦，喂我吃今天的灵材吧~"]


def domain_for(kind: ChallengeKind) -> str:
    return KIND_DOMAIN.get(kind, "bo")


def quality(correct: Optional[bool], credit: float) -> float:
    """喂养质量：客观对=1，客观错=0.4（仍喂到，只是淡），主观看认真程度。"""
    if correct is True:
        return 1.0
    if correct is False:
        return 0.4
    return max(0.4, credit)


def craving_for(domain: str, idx: int) -> str:
    arr = CRAVINGS.get(domain, CRAVINGS["bo"])
    return arr[idx % len(arr)]


def species_info(domain: Optional[str]) -> Optional[dict]:
    return SPECIES.get(domain) if domain else None


@dataclass
class Incubation:
    stage: str = "egg"                  # egg | youth
    egg_day: int = 0                    # 已"喂饱"的天数 0..7
    last_fed_day: Optional[str] = None  # 防止同一天重复 +1
    materials: dict = field(default_factory=lambda: {"li": 0, "wen": 0, "bo": 0})  # 灵材累计
    aff_sum: dict = field(default_factory=lambda: {"li": 0.0, "wen": 0.0, "bo": 0.0})
    aff_n: dict = field(default_factory=lambda: {"li": 0, "wen": 0, "bo": 0})
    species: Optional[str] = None       # 破壳后的主导域
    hatched_day: Optional[str] = None

    # ---- 喂养（每答一题，蛋阶段才调）----
    def feed(self, domain: str, correct: Optional[bool], credit: float) -> None:
        self.materials[domain] = self.materials.get(domain, 0) + 1
        self.aff_sum[domain] = self.aff_sum.get(domain, 0.0) + quality(correct, credit)
        self.aff_n[domain] = self.aff_n.get(domain, 0) + 1

    def avg_affinity(self) -> dict:
        return {d: (self.aff_sum.get(d, 0.0) / self.aff_n[d]) if self.aff_n.get(d) else 0.0
                for d in DOMAINS}

    def norm_affinity(self) -> dict:
        avg = self.avg_affinity()
        m = max(avg.values()) or 1.0
        return {d: round(avg[d] / m, 3) for d in DOMAINS}

    def dominant(self) -> str:
        avg = self.avg_affinity()
        return max(DOMAINS, key=lambda d: (avg[d], self.aff_n.get(d, 0)))

    # ---- 每日"喂饱"推进孵化；满 7 天破壳 ----
    def advance_day(self, day: str) -> Optional[str]:
        """返回破壳的物种域（若本次破壳），否则 None。只在"今日 5 题喂饱"时调。"""
        if self.stage != "egg" or self.last_fed_day == day:
            return None
        self.egg_day += 1
        self.last_fed_day = day
        if self.egg_day >= DAYS_TO_HATCH:
            self.stage = "youth"
            self.species = self.dominant()
            self.hatched_day = day
            return self.species
        return None

    def to_dict(self) -> dict:
        return {
            "stage": self.stage, "egg_day": self.egg_day, "last_fed_day": self.last_fed_day,
            "materials": self.materials, "aff_sum": self.aff_sum, "aff_n": self.aff_n,
            "species": self.species, "hatched_day": self.hatched_day,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Incubation":
        d = d or {}
        return cls(
            stage=d.get("stage", "egg"), egg_day=d.get("egg_day", 0),
            last_fed_day=d.get("last_fed_day"),
            materials=d.get("materials", {"li": 0, "wen": 0, "bo": 0}),
            aff_sum=d.get("aff_sum", {"li": 0.0, "wen": 0.0, "bo": 0.0}),
            aff_n=d.get("aff_n", {"li": 0, "wen": 0, "bo": 0}),
            species=d.get("species"), hatched_day=d.get("hatched_day"),
        )
