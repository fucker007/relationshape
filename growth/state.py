"""每个孩子一份的成长状态文档（与 relationshape 的 UserRelationState 同构思路）。

一个自洽的 JSON 文档：能力、宠物、徽章、坚持、今日会话、历史、亮点、事件流。
整存整取，便于 JSON / 未来 JSONB 落库。所有"业务"都在 engine 里，这里只装数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from growth.abilities import AbilityState
from growth.pet import PetState


@dataclass
class ChildState:
    child_id: str
    name: str = ""
    age: int = 8
    grade: int = 2
    created_day: Optional[str] = None

    abilities: AbilityState = field(default_factory=AbilityState.fresh)
    pet: PetState = field(default_factory=PetState)

    stars: int = 0
    badges: list = field(default_factory=list)
    streak: int = 0
    best_streak: int = 0
    last_completed_day: Optional[str] = None
    battles: int = 0
    friendly_battles: int = 0

    cards: dict = field(default_factory=dict)          # card_id -> 拥有数量
    card_progress: dict = field(default_factory=dict)  # ability.value -> 累计答对数（掉藏品卡用）

    # 今日会话
    today_day: Optional[str] = None
    today_cids: list = field(default_factory=list)
    today_answered: dict = field(default_factory=dict)   # cid -> {"correct":bool|None, "credit":float}

    seen_cids: list = field(default_factory=list)         # 出过的题（抽题去重）
    history: list = field(default_factory=list)           # [{day,answered,correct,completed,kinds}]
    highlights: list = field(default_factory=list)        # [{day,kind,ability,prompt,answer}]
    events: list = field(default_factory=list)            # [{kind,label,detail,day}]

    # ---- 派生量 ----
    def recent_activity(self, window: int = 7) -> int:
        return sum(int(h.get("answered", 0)) for h in self.history[-window:])

    def ability_levels(self) -> dict:
        return {k: v.level for k, v in self.abilities.tracks.items()}

    def today_form(self) -> str:
        """今日状态:当天客观题正确率高 → 'hot'(只上不下的当日 buff)，否则 'normal'。"""
        objs = [v for v in self.today_answered.values() if v.get("correct") in (True, False)]
        if len(objs) >= 2 and sum(1 for v in objs if v.get("correct")) / len(objs) >= 0.8:
            return "hot"
        return "normal"

    def own_card(self, cid: str) -> None:
        self.cards[cid] = self.cards.get(cid, 0) + 1

    # ---- 变更助手 ----
    def add_badge(self, name: str) -> bool:
        if name in self.badges:
            return False
        self.badges.append(name)
        return True

    def log_event(self, kind: str, label: str, day: str, detail: str = "") -> None:
        self.events.append({"kind": kind, "label": label, "detail": detail, "day": day})
        self.events = self.events[-40:]

    def add_highlight(self, day: str, kind: str, ability: str, prompt: str, answer: str) -> None:
        self.highlights.append({"day": day, "kind": kind, "ability": ability,
                                "prompt": prompt, "answer": answer})
        self.highlights = self.highlights[-30:]

    # ---- 持久化 ----
    def to_dict(self) -> dict:
        return {
            "child_id": self.child_id,
            "name": self.name,
            "age": self.age,
            "grade": self.grade,
            "created_day": self.created_day,
            "abilities": self.abilities.to_dict(),
            "pet": self.pet.to_dict(),
            "stars": self.stars,
            "badges": self.badges,
            "streak": self.streak,
            "best_streak": self.best_streak,
            "last_completed_day": self.last_completed_day,
            "battles": self.battles,
            "friendly_battles": self.friendly_battles,
            "cards": self.cards,
            "card_progress": self.card_progress,
            "today_day": self.today_day,
            "today_cids": self.today_cids,
            "today_answered": self.today_answered,
            "seen_cids": self.seen_cids,
            "history": self.history,
            "highlights": self.highlights,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChildState":
        return cls(
            child_id=d["child_id"],
            name=d.get("name", ""),
            age=d.get("age", 8),
            grade=d.get("grade", 2),
            created_day=d.get("created_day"),
            abilities=AbilityState.from_dict(d.get("abilities", {})),
            pet=PetState.from_dict(d.get("pet", {})),
            stars=d.get("stars", 0),
            badges=d.get("badges", []),
            streak=d.get("streak", 0),
            best_streak=d.get("best_streak", 0),
            last_completed_day=d.get("last_completed_day"),
            battles=d.get("battles", 0),
            friendly_battles=d.get("friendly_battles", 0),
            cards=d.get("cards", {}),
            card_progress=d.get("card_progress", {}),
            today_day=d.get("today_day"),
            today_cids=d.get("today_cids", []),
            today_answered=d.get("today_answered", {}),
            seen_cids=d.get("seen_cids", []),
            history=d.get("history", []),
            highlights=d.get("highlights", []),
            events=d.get("events", []),
        )
