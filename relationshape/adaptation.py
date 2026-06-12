"""适应层：人格随用户演进的全部位置。

理论依据：
- 沟通适应理论（Giles）：关系好的双方说话方式会缓慢趋同
  （正式度、能量、口头禅）。这里用小步长 EMA 实现"慢"。
- 关系文化（Baxter）：每段关系会长出私有词汇、内部梗、固定仪式——
  这是"我们俩"区别于"任何人"的证据。
- 幽默接收度：对方笑了就多来一点这种风格，冷场就收
  （对幽默的强化学习，按风格分桶）。

约束：演进只发生在表达层，绝不触碰 identity 里的内核。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from relationshape import zh
from relationshape.types import HumorStyle

_EMA_ALPHA = 0.15
_BOUND = (0.05, 0.95)


def _clip(v: float) -> float:
    return max(_BOUND[0], min(_BOUND[1], v))


@dataclass
class InsideJoke:
    jid: str
    label: str           # 一句话标签，如"上次跑调事件"
    origin: str          # 出处摘要
    created_at: str
    used_count: int = 0
    last_used_turn: int = -99

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "InsideJoke":
        return cls(**d)


class AdaptationState:
    def __init__(self) -> None:
        self.formality: float = 0.45
        self.energy: float = 0.5
        self.humor_receptivity: dict[str, float] = {s.value: 0.5 for s in HumorStyle}
        self.address_form: str | None = None       # 用户许可的对自己的称呼
        self.liked_topics: dict[str, int] = {}
        self.inside_jokes: list[InsideJoke] = []
        self.lessons: list[str] = []               # 角色自我记忆：和这位用户磨合出的教训
        self.self_claims: list[str] = []           # 自述账本：对TA说过的自我事实（防矛盾，Yes-and）
        self.pending_humor: dict | None = None     # 上一轮用的幽默，等下一轮看反应

    # ------------------------------------------------------------- 风格趋同

    def observe_user_style(self, text: str) -> None:
        f = zh.formality_signal(text)
        if f is not None:
            self.formality = _clip(self.formality + _EMA_ALPHA * (f - self.formality))
        e = zh.energy_signal(text)
        self.energy = _clip(self.energy + _EMA_ALPHA * 0.6 * (e - self.energy))

    def observe_topics(self, tokens: list[str]) -> None:
        for tok in tokens[:3]:
            self.liked_topics[tok] = self.liked_topics.get(tok, 0) + 1

    # ------------------------------------------------------------- 幽默学习

    def set_pending_humor(self, style: str, material: str, turn: int) -> None:
        self.pending_humor = {"style": style, "material": material, "turn": turn}

    def react_to_pending_humor(self, user_text: str, turn: int) -> bool | None:
        """看用户对上一轮幽默的反应。返回 True=笑了 / False=冷场 / None=无悬挂幽默。"""
        if not self.pending_humor:
            return None
        # 只看紧邻的下一轮反应
        if turn - self.pending_humor["turn"] > 1:
            self.pending_humor = None
            return None
        style = self.pending_humor["style"]
        material = self.pending_humor["material"]
        self.pending_humor = None
        cur = self.humor_receptivity.get(style, 0.5)
        if zh.is_laughter(user_text):
            self.humor_receptivity[style] = _clip(cur + 0.12)
            self._register_inside_joke(material)
            return True
        self.humor_receptivity[style] = _clip(cur - 0.08)
        return False

    def _register_inside_joke(self, material: str) -> None:
        if not material:
            return
        jid = hashlib.md5(material.encode()).hexdigest()[:8]
        for j in self.inside_jokes:
            if j.jid == jid:
                return
        self.inside_jokes.append(InsideJoke(
            jid=jid, label=material, origin=f"一起笑过的：{material}",
            created_at=datetime.now().isoformat(),
        ))
        self.inside_jokes = self.inside_jokes[-12:]

    # ------------------------------------------------------------- 称呼与教训

    def maybe_learn_address(self, text: str) -> str | None:
        import re

        m = re.search(r"(你可以)?叫我([^\s，。！？!?]{1,6})(吧|哦|呀)?", text)
        if m and m.group(2) not in ("什么", "啥"):
            self.address_form = m.group(2)
            return self.address_form
        return None

    def add_lesson(self, lesson: str) -> None:
        if lesson in self.lessons:
            return
        self.lessons.append(lesson)
        self.lessons = self.lessons[-10:]

    def add_self_claims(self, assistant_text: str) -> list[str]:
        """身世轮后登记角色的自我表述（连续性管理：之后不可矛盾）。"""
        import re

        new: list[str] = []
        for m in re.finditer(
            r"我(?:也)?(?:是|不是|没有|有|会|不会|喜欢|讨厌|怕|不怕|住在|记得|叫)[^，。！？!?\s]{1,10}",
            assistant_text,
        ):
            claim = re.sub(r"[了的呢啊呀哦吧啦嘛~～]+$", "", m.group(0))
            if claim and claim not in self.self_claims:
                self.self_claims.append(claim)
                new.append(claim)
        self.self_claims = self.self_claims[-20:]
        return new

    def culture_size(self) -> int:
        """关系文化的规模：内部梗 + 专属称呼。阶段晋升的条件之一。"""
        return len(self.inside_jokes) + (1 if self.address_form else 0)

    def persona_notes(self) -> list[str]:
        """渲染给提示词的"人格演进"摘要：这段关系把角色磨合成了什么样。"""
        notes: list[str] = []
        if self.formality < 0.35:
            notes.append("和这位用户已经磨合成随意轻松的语气")
        elif self.formality > 0.65:
            notes.append("这位用户偏好稍正式的语气，保持分寸")
        if self.energy > 0.6:
            notes.append("对方能量高，跟上节奏")
        elif self.energy < 0.35:
            notes.append("对方节奏偏静，放轻放缓")
        best_styles = [s for s, v in self.humor_receptivity.items() if v >= 0.62]
        if best_styles:
            zh_names = {"affiliative": "一起笑的玩笑", "self_enhancing": "自嘲小糗事",
                        "callback": "内部梗", "wordplay": "谐音梗", "playful_tease": "轻度打趣"}
            notes.append("对方吃这套幽默：" + "、".join(zh_names.get(s, s) for s in best_styles))
        for lesson in self.lessons[-2:]:
            notes.append(f"相处教训：{lesson}")
        return notes

    # ------------------------------------------------------------- serde

    def to_dict(self) -> dict:
        return {
            "formality": self.formality,
            "energy": self.energy,
            "humor_receptivity": self.humor_receptivity,
            "address_form": self.address_form,
            "liked_topics": self.liked_topics,
            "inside_jokes": [j.to_dict() for j in self.inside_jokes],
            "lessons": self.lessons,
            "self_claims": self.self_claims,
            "pending_humor": self.pending_humor,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AdaptationState":
        st = cls()
        st.formality = d.get("formality", 0.45)
        st.energy = d.get("energy", 0.5)
        st.humor_receptivity = {**st.humor_receptivity, **d.get("humor_receptivity", {})}
        st.address_form = d.get("address_form")
        st.liked_topics = d.get("liked_topics", {})
        st.inside_jokes = [InsideJoke.from_dict(j) for j in d.get("inside_jokes", [])]
        st.lessons = d.get("lessons", [])
        st.self_claims = d.get("self_claims", [])
        st.pending_humor = d.get("pending_humor")
        return st
