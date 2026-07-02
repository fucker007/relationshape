"""战斗内核:把孩子的真实数值换算成战斗属性,跑一场可回放的回合制对战 → battle_log。

双轨数值哲学(已和产品确认):
- **努力 → HP(体魄)**:出勤/坚持/活跃,是地板,只涨不罚。勤奋吃力的娃血厚、能缠斗。
- **正确率 → 暴击(锋芒)**:答得准 = 打得狠,有上限,是优势不是碾压。

五能力各管一项战斗属性(孩子最强的能力=他的流派):
  逻辑→攻击 ATK｜观察+正确率→暴击 CRIT｜专注→速度 SPD｜表达→绝招威力｜创造→变招。

所有判定确定性(seed 派生),可回放、可测。下面一段常量就是"旋钮表",随时可调。
"""

from __future__ import annotations

import hashlib
import random

from growth.types import ABILITY_ELEMENT, ABILITY_ZH, Ability

# ===================== 旋钮表 =====================
HP_BASE, HP_STREAK, HP_STREAK_CAP, HP_ACT, HP_ACT_CAP, HP_FEED = 80, 5, 60, 3, 35, 0.9
ATK_BASE, ATK_K = 12, 0.35           # 逻辑
CRIT_BASE, CRIT_K, CRIT_MIN, CRIT_MAX = 0.05, 0.35, 0.05, 0.50   # 观察/正确率
SPD_BASE, SPD_K = 8, 0.10            # 专注
SPECIAL_BASE, SPECIAL_K = 1.4, 0.006  # 表达(绝招倍率)
CREATION_TWIST_K, CREATION_TWIST_CAP = 1 / 400, 0.25  # 创造(变招概率)
CRIT_MULT = 1.8
ELEM_ADV = 0.25
FORM_HOT_ATK, FORM_HOT_CRIT = 0.12, 0.08
# 战力权重(让 breakdown 之和=战力,家长能看清因果)
W_HP, W_ATK, W_SPD, W_CRIT, W_SPECIAL = 0.5, 7, 4, 3, 25
MAX_TURNS = 18

# 对战段位(按战力,用"星"系命名,与境界区分开)
RANKS = [(0, "萌芽"), (200, "铜星"), (380, "银星"), (560, "金星"), (780, "钻星"), (1050, "星耀")]

# 五元素相克(五边形):每元素克接下来两个
_ELEM_ORDER = ["晶", "焰", "声", "风", "光"]   # 逻辑/创造/表达/观察/专注


def rank_for(power: int) -> tuple[int, str]:
    idx, name = 0, RANKS[0][1]
    for i, (thr, nm) in enumerate(RANKS):
        if power >= thr:
            idx, name = i, nm
    return idx, name


def _elem_adv(a: str, b: str) -> int:
    if a not in _ELEM_ORDER or b not in _ELEM_ORDER or a == b:
        return 0
    ia, ib = _ELEM_ORDER.index(a), _ELEM_ORDER.index(b)
    return 1 if (ib - ia) % 5 in (1, 2) else -1


def combat_stats(child) -> dict:
    """从孩子状态算出战斗属性 + 战力拆解(breakdown 之和=战力)。"""
    from growth.cards import card_power_bonus
    from growth.cultivation import DOMAIN_ELEM, species_info

    lv = lambda a: child.abilities.track(a).level
    cv = child.cultivation
    hp = round(HP_BASE
               + min(child.streak, HP_STREAK_CAP) * HP_STREAK
               + min(child.recent_activity(), HP_ACT_CAP) * HP_ACT
               + cv.feed_total() * HP_FEED)
    atk = round(ATK_BASE + lv(Ability.LOGIC) * ATK_K, 1)
    acc = child.abilities.overall_accuracy()
    crit = max(CRIT_MIN, min(CRIT_MAX, CRIT_BASE + CRIT_K * acc))
    spd = round(SPD_BASE + lv(Ability.FOCUS) * SPD_K, 1)
    special = round(SPECIAL_BASE + lv(Ability.EXPRESSION) * SPECIAL_K, 2)
    creation = lv(Ability.CREATION)
    dom = child.abilities.strongest()
    sp = species_info(cv.species)
    element = sp["elem"] if sp else DOMAIN_ELEM[cv.dominant()]   # 元素随物种（未破壳看主导域）
    form = child.today_form()
    card_bonus = card_power_bonus(child.cards)

    parts = [
        {"label": "体魄", "source": "坚持/活跃", "value": round(hp * W_HP)},
        {"label": "攻击", "source": "逻辑", "value": round(atk * W_ATK)},
        {"label": "速度", "source": "专注", "value": round(spd * W_SPD)},
        {"label": "暴击", "source": "正确率", "value": round(crit * 100 * W_CRIT)},
        {"label": "绝招", "source": "表达", "value": round(special * W_SPECIAL)},
        {"label": "藏卡", "source": "卡牌收藏", "value": card_bonus},
    ]
    power = sum(p["value"] for p in parts)
    ridx, rname = rank_for(power)
    return {
        "hp": hp, "atk": atk, "crit": round(crit, 3), "spd": spd,
        "special": special, "creation": creation, "element": element,
        "dominant": dom.value, "dominant_zh": ABILITY_ZH[dom], "form": form,
        "card_bonus": card_bonus, "battle_power": power,
        "rank_index": ridx, "rank": rname, "breakdown": parts,
        "accuracy": round(acc, 2),
    }


def is_friendly(a_power: int, b_power: int, a_rank: int, b_rank: int) -> bool:
    hi, lo = max(a_power, b_power), max(1, min(a_power, b_power))
    return abs(a_rank - b_rank) >= 2 or hi >= 2.2 * lo


def _seed_int(*parts) -> int:
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def simulate(A: dict, B: dict, seed: int, friendly: bool) -> dict:
    """回合制对战 → battle_log(事件时间线)。A/B 含 id/name/hp/atk/crit/spd/special/creation/element/form。"""
    rng = random.Random(seed)
    hp = {"a": A["hp"], "b": B["hp"]}
    maxhp = {"a": A["hp"], "b": B["hp"]}
    if friendly:                       # 友谊赛:同一血池,抹平差距,点到为止
        m = max(A["hp"], B["hp"])
        hp["a"] = maxhp["a"] = m
        hp["b"] = maxhp["b"] = m
    stats = {"a": A, "b": B}
    charge = {"a": 0, "b": 0}
    order = ["a", "b"] if A["spd"] >= B["spd"] else ["b", "a"]
    events: list[dict] = []
    winner = None

    for _turn in range(MAX_TURNS):
        for who in order:
            foe = "b" if who == "a" else "a"
            if hp[foe] <= 0:
                break
            s, fs = stats[who], stats[foe]
            charge[who] += 1
            special = charge[who] % 3 == 0

            dmg = s["atk"] * (0.85 + rng.random() * 0.30)
            adv = _elem_adv(s["element"], fs["element"])
            if adv > 0:
                dmg *= (1 + ELEM_ADV)
            elif adv < 0:
                dmg *= (1 - ELEM_ADV * 0.5)
            if s.get("form") == "hot":
                dmg *= (1 + FORM_HOT_ATK)
            crit_chance = s["crit"] + (FORM_HOT_CRIT if s.get("form") == "hot" else 0)
            is_crit = rng.random() < crit_chance
            if is_crit:
                dmg *= CRIT_MULT
            move = "normal"
            if special:
                dmg *= s["special"]
                move = "special"
            twist = rng.random() < min(CREATION_TWIST_CAP, s["creation"] * CREATION_TWIST_K)
            if twist:
                dmg *= 1.3
            if friendly:
                dmg = min(dmg, maxhp[foe] * 0.18)   # 友谊赛单击封顶,打不出秒杀
            dmg = max(1, round(dmg))
            hp[foe] = max(0, hp[foe] - dmg)
            events.append({
                "i": len(events), "actor": who, "foe": foe, "move": move,
                "dmg": dmg, "crit": is_crit, "adv": adv, "twist": twist,
                "hp_a": hp["a"], "hp_b": hp["b"],
                "pct_a": round(100 * hp["a"] / maxhp["a"]),
                "pct_b": round(100 * hp["b"] / maxhp["b"]),
            })
            if hp[foe] <= 0:
                winner = who
                break
        if winner:
            break
        if friendly and (hp["a"] <= maxhp["a"] * 0.35 or hp["b"] <= maxhp["b"] * 0.35):
            break

    ko = winner is not None
    if winner is None:
        winner = "a" if (hp["a"] / maxhp["a"]) >= (hp["b"] / maxhp["b"]) else "b"
    return {"events": events, "winner": winner, "ko": ko,
            "hp_a": hp["a"], "hp_b": hp["b"], "maxhp_a": maxhp["a"], "maxhp_b": maxhp["b"]}
