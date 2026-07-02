"""卡牌奖励系统 + 修为阶梯。

词汇约定：宠物本体的阶段叫「境界」（cultivation.py：蛋→幼年→…）；
这里按能力**真实等级**划分的阶梯叫「修为」——逻辑·宗师 / 表达·大师……
靠"答得准→能力涨"晋升，突破时掉一张修为卡。

卡牌(CATALOG)：确定性掉落的收藏品（**不是抽卡**，守住"不做变率强化"的伦理）：
- 答对累积到阈值 → 该能力的"藏品卡"（凡→天，稀有度递增）
- 修为突破 → "修为卡"；连续打卡里程碑 → "坚持卡"；对战 → "对战卡"
稀有度走修仙品阶：凡品/灵品/玄品/地品/天品/仙品。

约束关联：藏卡给一个**有上限**的战力加成(card_power_bonus)，绑进战力但封顶，
延续"技巧是锋芒不是碾压"。卡靠玩（努力）拿，绝不靠充值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from growth.types import ABILITY_ZH, Ability

# ---- 修为（按能力等级 0..100） ----
MASTERY = [
    (0, "蒙童"), (12, "小天才"), (24, "学徒"), (38, "大师"),
    (52, "宗师"), (66, "盟主"), (78, "半圣"), (90, "圣贤"), (97, "一代宗师"),
]


def mastery_index(level: float) -> int:
    idx = 0
    for i, (thr, _n) in enumerate(MASTERY):
        if level >= thr:
            idx = i
    return idx


def mastery_progress(level: float) -> dict:
    i = mastery_index(level)
    name = MASTERY[i][1]
    nxt = MASTERY[i + 1] if i + 1 < len(MASTERY) else None
    if nxt is None:
        return {"index": i, "name": name, "next": None, "pct": 100}
    cur = MASTERY[i][0]
    pct = round(100 * (level - cur) / (nxt[0] - cur)) if nxt[0] > cur else 100
    return {"index": i, "name": name, "next": nxt[1], "pct": max(0, min(100, pct))}


# ---- 稀有度(修仙品阶) ----
RARITIES = ["凡品", "灵品", "玄品", "地品", "天品", "仙品"]
RARITY_COLOR = ["#9aa3b8", "#46c98b", "#4aa3ff", "#b07bff", "#ffb454", "#ff5a6e"]


@dataclass
class Card:
    cid: str
    name: str
    domain: str          # 能力 value 或 "坚持"/"对战"
    rarity: int          # 0..5
    flavor: str
    source: str          # collect / realm / streak / battle

    def public(self, owned: int = 0) -> dict:
        return {
            "cid": self.cid, "name": self.name, "domain": self.domain,
            "domain_zh": ABILITY_ZH.get(_as_ability(self.domain), self.domain),
            "rarity": self.rarity, "rarity_zh": RARITIES[self.rarity],
            "color": RARITY_COLOR[self.rarity],
            "flavor": self.flavor, "source": self.source, "owned": owned,
        }


def _as_ability(domain: str):
    try:
        return Ability(domain)
    except ValueError:
        return None


# ---- 藏品卡名(每能力 5 张,稀有度 0..4) ----
_COLLECT_NAMES = {
    Ability.LOGIC: [("推理石", "初窥因果,万事有其理。"), ("因果链", "顺藤摸瓜,环环相扣。"),
                    ("演绎盘", "由一推百,无所遁形。"), ("缜思晶", "心思缜密,滴水不漏。"),
                    ("天机算", "落子之前,已见结局。")],
    Ability.EXPRESSION: [("巧舌符", "把心里话,说给世界听。"), ("共情铃", "你的话能走进别人心里。"),
                         ("妙语笺", "三言两语,胜过千言。"), ("言灵珠", "言出法随,字字千钧。"),
                         ("万言鼎", "舌灿莲花,辩动八方。")],
    Ability.FOCUS: [("静心符", "心不乱,事自成。"), ("凝神珠", "千扰不动,一念归一。"),
                    ("入定石", "外界喧嚣,与我无关。"), ("止水镜", "波澜不惊,洞照分明。"),
                    ("一念千秋", "一念既起,万难不移。")],
    Ability.OBSERVATION: [("明察镜", "别人看热闹,你看门道。"), ("见微符", "见微知著,一叶知秋。"),
                          ("鹰眼石", "纤毫毕现,无所遁形。"), ("洞察瞳", "一眼看穿,真假立判。"),
                          ("烛照珠", "幽微之处,皆被照亮。")],
    Ability.CREATION: [("灵感火", "脑海里有一簇不灭的火。"), ("奇思种", "别人没想过的,你想到了。"),
                       ("妙想羽", "思绪生翼,飞向远方。"), ("造化笔", "笔落之处,无中生有。"),
                       ("鸿蒙珠", "从虚空里,造出新世界。")],
}
# 答对累积阈值 → 解锁第 i 张藏品卡
_COLLECT_THRESHOLDS = [3, 8, 16, 28, 44]

_STREAK_CARDS = {
    3: ("三日之约", 0, "三天不断,习惯发芽。"), 7: ("一周之约", 1, "七日精进,初见锋芒。"),
    14: ("半月之约", 2, "两周不辍,渐入佳境。"), 30: ("月度之约", 3, "一月坚持,脱胎换骨。"),
    60: ("双月之约", 4, "六十日如一日,心志如铁。"), 100: ("百日之约", 5, "百日筑基,水滴石穿。"),
}


def _build_catalog() -> dict:
    cat: dict[str, Card] = {}
    # 藏品卡
    for ab, names in _COLLECT_NAMES.items():
        for i, (nm, fl) in enumerate(names):
            cid = f"collect-{ab.value}-{i}"
            cat[cid] = Card(cid, nm, ab.value, i, fl, "collect")
    # 修为卡(每能力,修为 idx 1..8)
    for ab in Ability:
        for ri in range(1, len(MASTERY)):
            cid = f"mastery-{ab.value}-{ri}"
            rar = min(5, ri - 1)
            cat[cid] = Card(cid, f"{ABILITY_ZH[ab]}·{MASTERY[ri][1]}", ab.value, rar,
                            f"{ABILITY_ZH[ab]}修为臻至{MASTERY[ri][1]}。", "mastery")
    # 坚持卡
    for thr, (nm, rar, fl) in _STREAK_CARDS.items():
        cid = f"streak-{thr}"
        cat[cid] = Card(cid, nm, "坚持", rar, fl, "streak")
    # 对战卡
    cat["battle-first"] = Card("battle-first", "初战之证", "对战", 0, "第一次出战,虽败犹荣。", "battle")
    cat["battle-friendly"] = Card("battle-friendly", "友谊之证", "对战", 1, "切磋以友会,胜负皆收获。", "battle")
    cat["battle-veteran"] = Card("battle-veteran", "百战雄心", "对战", 2, "战过十场,愈战愈勇。", "battle")
    return cat


CATALOG: dict[str, Card] = _build_catalog()


# ---- 掉落逻辑(确定性) ----
def on_correct(progress_before: int, progress_after: int, ability: Ability, owned: set) -> list[str]:
    """答对后累计进度跨过阈值 → 解锁对应藏品卡(未拥有才发)。"""
    out = []
    for i, thr in enumerate(_COLLECT_THRESHOLDS):
        if progress_before < thr <= progress_after:
            cid = f"collect-{ability.value}-{i}"
            if cid in CATALOG and cid not in owned:
                out.append(cid)
    return out


def on_mastery_up(ability: Ability, old_level: float, new_level: float, owned: set) -> list[str]:
    out = []
    oi, ni = mastery_index(old_level), mastery_index(new_level)
    for ri in range(oi + 1, ni + 1):
        cid = f"mastery-{ability.value}-{ri}"
        if cid in CATALOG and cid not in owned:
            out.append(cid)
    return out


def on_streak(streak: int, owned: set) -> list[str]:
    cid = f"streak-{streak}"
    return [cid] if cid in CATALOG and cid not in owned else []


def on_battle(total_battles: int, friendly: bool, owned: set) -> list[str]:
    out = []
    if "battle-first" not in owned:
        out.append("battle-first")
    if friendly and "battle-friendly" not in owned:
        out.append("battle-friendly")
    if total_battles >= 10 and "battle-veteran" not in owned:
        out.append("battle-veteran")
    return out


# ---- 藏卡战力加成(有上限的"约束关联") ----
CARD_BONUS_CAP = 50


def card_power_bonus(owned: dict) -> int:
    """按拥有卡的稀有度加权求和,封顶。卡是努力的累积、绝不充值,且不碾压平衡。"""
    total = 0.0
    for cid in owned:
        c = CATALOG.get(cid)
        if c:
            total += (c.rarity + 1) * 0.7
    return min(CARD_BONUS_CAP, round(total))


def collection_summary(owned: dict) -> dict:
    """卡册概览:总收集 / 总目录 / 按稀有度计数。"""
    by_rarity = [0] * len(RARITIES)
    for cid in owned:
        c = CATALOG.get(cid)
        if c:
            by_rarity[c.rarity] += 1
    return {"owned": len(owned), "total": len(CATALOG),
            "by_rarity": by_rarity, "bonus": card_power_bonus(owned)}
