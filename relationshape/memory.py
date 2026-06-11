"""记忆系统：让"它记得我"成立的地方。

- 情景记忆：带情绪显著度的事件。显著度按遗忘曲线衰减（艾宾浩斯），
  被召回时获得复习强化（间隔效应）；高唤起时刻记得更牢（闪光灯记忆）。
- 语义记忆：偏好、厌恶、用户身边的人物。
- 承诺：完整生命周期 open → due → kept/missed。
  说到做到是信任的第一来源；接不住的承诺必须主动认账，不能假装没说过。
- 敏感记忆：危机披露只进封存区，永不被闲聊召回、永不成为玩笑素材。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

from relationshape import zh
from relationshape.types import MemoryRecall, PromiseView


def _mid(text: str, ts: str) -> str:
    return hashlib.md5(f"{text}|{ts}".encode()).hexdigest()[:10]


def _strip_particles(item: str) -> str:
    """去掉抽取结果尾部的语气词（"恐龙了" → "恐龙"）。"""
    return re.sub(r"[了的呢啊呀哦吧啦嘛]+$", "", item or "")


@dataclass
class Episode:
    mid: str
    text: str
    created_at: str
    valence: float = 0.0
    arousal: float = 0.2
    salience: float = 0.4
    recall_count: int = 0
    last_recalled: str = ""
    sensitive: bool = False
    vulnerability: int = 0   # 表露深度：3=秘密级，不做开场钩子
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Episode":
        return cls(**d)


@dataclass
class Promise:
    pid: str
    text: str
    made_by: str            # character / user
    created_at: str
    session_made: int
    status: str = "open"    # open / kept / missed
    surfaced: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Promise":
        return cls(**d)


_PREFERENCE_RE = re.compile(r"我(最|特别|超|很)?(喜欢|爱|想学|在学|迷上)([^，。！？!?\s]{1,12})")
_AVERSION_RE = re.compile(r"我(最|特别|超|很)?(讨厌|怕|害怕|受不了)([^，。！？!?\s]{1,12})")
_NAME_RE = re.compile(r"我叫([^\s，。！？!?]{1,8})")
_PERSON_RE = re.compile(r"([^\s，。！？!?]{1,6})是我(最好)?的?(朋友|同桌|同学|老师|哥|姐|弟|妹|闺蜜)")

# 角色承诺的口头模式："下次我给你讲…" "明天我们…"
_CHAR_PROMISE_RE = re.compile(
    r"((下次|明天|以后|等你回来|下回)[^，。！？!?]{0,12}(我|我们|咱们)[^，。！？!?]{1,18}|我(答应你|保证)[^，。！？!?]{1,18})"
)


class MemoryBank:
    def __init__(self) -> None:
        self.episodes: list[Episode] = []
        self.preferences: list[str] = []
        self.aversions: list[str] = []
        self.people: dict[str, dict] = {}     # 名字/角色 -> {"relation":…, "mentions":n}
        self.user_name: str | None = None
        self.promises: list[Promise] = []

    # ------------------------------------------------------------------ 写入

    def add_episode(
        self, text: str, valence: float, arousal: float, now: datetime,
        sensitive: bool = False, vulnerability: int = 0, tags: list[str] | None = None,
    ) -> Episode:
        # 显著度：情绪越强记得越牢（闪光灯记忆的工程近似）
        salience = min(1.0, 0.3 + 0.4 * abs(valence) + 0.3 * arousal)
        ep = Episode(
            mid=_mid(text, now.isoformat()), text=text[:80], created_at=now.isoformat(),
            valence=valence, arousal=arousal, salience=salience,
            sensitive=sensitive, vulnerability=vulnerability, tags=tags or [],
        )
        self.episodes.append(ep)
        return ep

    def extract_facts(self, text: str, mentioned_actors: list[str]) -> list[str]:
        """从用户原话提取语义事实，返回"新学到的事"列表（供奖励判断）。"""
        learned: list[str] = []
        m = _NAME_RE.search(text)
        if m and self.user_name != m.group(1):
            self.user_name = m.group(1)
            learned.append(f"名字：{self.user_name}")
        for m in _PREFERENCE_RE.finditer(text):
            item = _strip_particles(m.group(3))
            if item and item not in self.preferences:
                self.preferences.append(item)
                learned.append(f"喜欢：{item}")
        for m in _AVERSION_RE.finditer(text):
            item = _strip_particles(m.group(3))
            if item and item not in self.aversions:
                self.aversions.append(item)
                learned.append(f"不喜欢：{item}")
        for m in _PERSON_RE.finditer(text):
            name, relation = m.group(1), m.group(3)
            if name not in self.people:
                self.people[name] = {"relation": relation, "mentions": 0}
                learned.append(f"身边的人：{name}（{relation}）")
        for actor in mentioned_actors:
            entry = self.people.setdefault(actor, {"relation": actor, "mentions": 0})
            entry["mentions"] += 1
        return learned

    # ------------------------------------------------------------------ 遗忘

    def decay_and_prune(self, now: datetime, half_life_days: float, threshold: float, cap: int) -> None:
        kept: list[Episode] = []
        for ep in self.episodes:
            created = datetime.fromisoformat(ep.created_at)
            days = max(0.0, (now - created).total_seconds() / 86400.0)
            # 复习强化：召回次数延长记忆寿命
            effective_half_life = half_life_days * (1 + 0.8 * ep.recall_count)
            current = ep.salience * (0.5 ** (days / effective_half_life))
            if ep.sensitive or current >= threshold:
                kept.append(ep)
        kept = kept[-cap:]
        self.episodes = kept

    # ------------------------------------------------------------------ 召回

    def recall(self, query_text: str, now: datetime, k: int, half_life_days: float) -> list[MemoryRecall]:
        """相关性 = 主题重叠 × 当前显著度 × 新近度。敏感记忆不参与闲聊召回。"""
        qb = zh.bigrams(query_text)
        scored: list[tuple[float, Episode, int]] = []
        for ep in self.episodes:
            if ep.sensitive:
                continue
            created = datetime.fromisoformat(ep.created_at)
            days = max(0.0, (now - created).total_seconds() / 86400.0)
            overlap = zh.jaccard(qb, zh.bigrams(ep.text))
            if overlap <= 0.02:
                continue
            effective_half_life = half_life_days * (1 + 0.8 * ep.recall_count)
            current_salience = ep.salience * (0.5 ** (days / effective_half_life))
            recency = 1.0 / (1.0 + days / 7.0)
            score = overlap * (0.5 + current_salience) * (0.6 + 0.4 * recency)
            scored.append((score, ep, int(days)))
        scored.sort(key=lambda x: -x[0])
        out: list[MemoryRecall] = []
        for score, ep, days in scored[:k]:
            ep.recall_count += 1
            ep.last_recalled = now.isoformat()
            out.append(MemoryRecall(
                text=ep.text, kind="episode", score=round(score, 3), days_ago=days,
                hint="相关就自然带一句，不相关就别硬塞",
            ))
        return out

    def recent_highlight(self, now: datetime) -> MemoryRecall | None:
        """开场用：最近 10 天里显著度最高的一件事（问候语没有话题词可匹配）。

        秘密级表露（vulnerability>=3）不做开场白：朋友会在话题相关时
        温柔接住秘密，但不会拿它打招呼。
        """
        best: tuple[float, Episode] | None = None
        for ep in self.episodes:
            if ep.sensitive or ep.vulnerability >= 3:
                continue
            days = (now - datetime.fromisoformat(ep.created_at)).total_seconds() / 86400.0
            if days > 10:
                continue
            score = ep.salience * (1.0 / (1.0 + days / 5.0))
            if best is None or score > best[0]:
                best = (score, ep)
        if best is None:
            return None
        ep = best[1]
        days_ago = int((now - datetime.fromisoformat(ep.created_at)).total_seconds() / 86400.0)
        return MemoryRecall(
            text=ep.text, kind="episode", score=round(best[0], 3), days_ago=days_ago,
            hint="开场可以像朋友惦记一样问起这件事的后续",
        )

    def recall_preferences(self, query_text: str, k: int = 2) -> list[MemoryRecall]:
        qb = zh.bigrams(query_text)
        out = []
        for pref in self.preferences:
            if zh.jaccard(qb, zh.bigrams(pref)) > 0.05:
                out.append(MemoryRecall(
                    text=f"用户喜欢{pref}", kind="preference", score=0.5, days_ago=0,
                    hint="可以用对方的喜好接话",
                ))
        return out[:k]

    # ------------------------------------------------------------------ 承诺

    def detect_character_promise(self, assistant_text: str, now: datetime, session_index: int) -> Promise | None:
        m = _CHAR_PROMISE_RE.search(assistant_text)
        if not m:
            return None
        text = m.group(0)[:40]
        # 同文已存在就不重复记
        for p in self.promises:
            if p.text == text and p.status == "open":
                return None
        promise = Promise(
            pid=_mid(text, now.isoformat()), text=text, made_by="character",
            created_at=now.isoformat(), session_made=session_index,
        )
        self.promises.append(promise)
        return promise

    def due_promises(self, session_index: int) -> list[PromiseView]:
        """下一次会话起，未兑现的承诺就到期：要么兑现，要么主动认账。"""
        views = []
        for p in self.promises:
            if p.status == "open" and session_index > p.session_made:
                note = "主动提起并兑现它" if p.surfaced == 0 else "已经提过还没兑现：认账+补救，别再拖"
                views.append(PromiseView(pid=p.pid, text=p.text, made_by=p.made_by, status="due", note=note))
        return views

    def mark_promise(self, pid: str, status: str) -> None:
        for p in self.promises:
            if p.pid == pid:
                p.status = status
                return

    def touch_surfaced(self, pids: list[str]) -> None:
        for p in self.promises:
            if p.pid in pids:
                p.surfaced += 1

    # ------------------------------------------------------------------ serde

    def to_dict(self) -> dict:
        return {
            "episodes": [e.to_dict() for e in self.episodes],
            "preferences": self.preferences,
            "aversions": self.aversions,
            "people": self.people,
            "user_name": self.user_name,
            "promises": [p.to_dict() for p in self.promises],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryBank":
        bank = cls()
        bank.episodes = [Episode.from_dict(e) for e in d.get("episodes", [])]
        bank.preferences = d.get("preferences", [])
        bank.aversions = d.get("aversions", [])
        bank.people = d.get("people", {})
        bank.user_name = d.get("user_name")
        bank.promises = [Promise.from_dict(p) for p in d.get("promises", [])]
        return bank
