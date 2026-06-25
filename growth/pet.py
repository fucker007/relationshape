"""养成宠物：孩子成长的可见化身。

宠物随"成长值"进化、可装扮（装扮由徽章 / 坚持解锁），并有"活力"——每天会落一点，
做挑战回补，体现"它需要你来照顾"的陪伴感。宠物的成长值只进不出（努力的累积），
活力可起落（陪伴的节奏）。主元素取自孩子最强的能力，给对战添一点"属性"味道。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 进化阶段：累计成长值门槛 → 形态名
STAGES = [
    (0, "蛋"),
    (40, "幼崽"),
    (140, "少年体"),
    (360, "成长体"),
    (800, "成熟体"),
    (1600, "觉醒体"),
]

# 装扮：解锁键 -> (部位, 名称)。解锁键由引擎在徽章/坚持达成时发放。
ITEM_CATALOG = {
    "streak3": ("hat", "探索者帽"),
    "streak7": ("cloak", "坚持披风"),
    "streak30": ("shoes", "疾风跑鞋"),
    "badge_first": ("medal", "初心勋章"),
    "badge_logic": ("medal", "智慧宝石"),
    "badge_expression": ("medal", "声波话筒"),
    "badge_focus": ("medal", "专注之冠"),
    "badge_observation": ("medal", "鹰眼徽记"),
    "badge_creation": ("medal", "灵感火花"),
}


@dataclass
class PetState:
    species: str = "Spark"
    growth_value: int = 0
    vitality: int = 70                 # 0..100
    dominant: Optional[str] = None     # 最强能力的 value（决定主元素）
    equipped: dict = field(default_factory=dict)   # slot -> item name
    unlocked: list = field(default_factory=list)   # 已解锁的 unlock_key
    last_day: Optional[str] = None

    def stage(self) -> tuple[int, str]:
        idx, name = 0, STAGES[0][1]
        for i, (thr, nm) in enumerate(STAGES):
            if self.growth_value >= thr:
                idx, name = i, nm
        return idx, name

    def stage_progress(self) -> dict:
        idx, name = self.stage()
        nxt = STAGES[idx + 1] if idx + 1 < len(STAGES) else None
        if nxt is None:
            return {"index": idx, "name": name, "next": None, "pct": 100}
        cur_thr = STAGES[idx][0]
        pct = round(100 * (self.growth_value - cur_thr) / (nxt[0] - cur_thr))
        return {"index": idx, "name": name, "next": nxt[1], "pct": max(0, min(100, pct))}

    def nourish(self, growth: int) -> None:
        self.growth_value += max(0, growth)
        self.vitality = min(100, self.vitality + 6)   # 陪它玩，它更有活力

    def daily_decay(self) -> None:
        self.vitality = max(0, self.vitality - 12)     # 一天没见，活力会落一点

    def unlock(self, key: str) -> Optional[tuple[str, str]]:
        """解锁一件装扮并自动穿上（同部位覆盖）。返回 (部位, 名称) 或 None。"""
        if key in self.unlocked or key not in ITEM_CATALOG:
            return None
        self.unlocked.append(key)
        slot, name = ITEM_CATALOG[key]
        self.equipped[slot] = name
        return slot, name

    def to_dict(self) -> dict:
        return {
            "species": self.species,
            "growth_value": self.growth_value,
            "vitality": self.vitality,
            "dominant": self.dominant,
            "equipped": self.equipped,
            "unlocked": self.unlocked,
            "last_day": self.last_day,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PetState":
        return cls(
            species=d.get("species", "Spark"),
            growth_value=d.get("growth_value", 0),
            vitality=d.get("vitality", 70),
            dominant=d.get("dominant"),
            equipped=d.get("equipped", {}),
            unlocked=d.get("unlocked", []),
            last_day=d.get("last_day"),
        )
