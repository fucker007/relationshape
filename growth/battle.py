"""碰一碰对战 + 段位匹配。

两条红线（呼应营销讨论里"别把弱孩子当众打输"）：
1. 战力 = 努力。能力（练出来的）+ 坚持 + 近期活跃 + 养成，没有任何充值项。
   一句对家长的卖点：别人家游戏充钱变强，这里只有努力能变强。
2. 段位差过大 → 友谊赛：给弱者回合加成，让他也能赢下几个回合、不被碾压，
   且双方都拿参与奖励，输的一方绝不被惩罚（输了不掉成长值）。

判定确定性来自 (a_id,b_id,day,n) 的哈希派生，不用 wall-clock 随机——可回放、可测试。
"""

from __future__ import annotations

import hashlib

from growth.types import (
    ABILITY_ELEMENT,
    ABILITY_ZH,
    Ability,
    BattleResult,
    BattleRound,
)

RANKS = [(0, "萌芽"), (120, "小试身手"), (320, "进阶"), (640, "高手"), (1100, "大师")]

# 五元素相克（五边形）：每个元素克接下来两个。纯展示味道，不动努力内核。
_ELEM_ORDER = ["晶", "焰", "声", "风", "光"]   # 逻辑/创造/表达/观察/专注


def rank_for(power: int) -> tuple[int, str]:
    idx, name = 0, RANKS[0][1]
    for i, (thr, nm) in enumerate(RANKS):
        if power >= thr:
            idx, name = i, nm
    return idx, name


def compute_power(ability_mean: float, streak: int, recent_activity: int, pet_growth: int) -> int:
    """战力——全部是努力项。"""
    return round(
        ability_mean * 1.5
        + min(streak, 30) * 4
        + min(recent_activity, 60) * 1.6
        + pet_growth * 0.05
    )


def _elem_advantage(a_elem: str, b_elem: str) -> int:
    """+1 a 克 b / -1 b 克 a / 0 平。"""
    if a_elem not in _ELEM_ORDER or b_elem not in _ELEM_ORDER or a_elem == b_elem:
        return 0
    ia, ib = _ELEM_ORDER.index(a_elem), _ELEM_ORDER.index(b_elem)
    if (ib - ia) % 5 in (1, 2):
        return 1
    return -1


def _roll(seed_parts, base: int, bonus: float) -> int:
    h = int(hashlib.md5("|".join(str(p) for p in seed_parts).encode()).hexdigest(), 16)
    noise = h % 100  # 0..99
    return round(base * (0.75 + noise / 200.0) * bonus)   # base × [0.75..1.245] × bonus


def battle(a: dict, b: dict, day: str, n: int) -> BattleResult:
    """a/b：{id,name,power,dominant(ability value),element}。三回合定胜负。"""
    a_idx, a_rank = rank_for(a["power"])
    b_idx, b_rank = rank_for(b["power"])
    hi, lo = max(a["power"], b["power"]), max(1, min(a["power"], b["power"]))
    # 友谊赛触发：段位差 ≥2，或战力悬殊（强者 ≥2.2 倍）——后者保护"刚买的新孩子"
    friendly = abs(a_idx - b_idx) >= 2 or hi >= 2.2 * lo

    # 友谊赛：双方按**同一基准**比拼（抹平战力差），弱者再得一点小优势——
    # 让弱的孩子也能赢下几个回合，体验是"切磋"而不是"被碾压"。
    # 注意：a_power/b_power 仍按真实值展示（UI 据此说明为何是友谊赛），只有判定用基准值。
    a_bonus, b_bonus = 1.0, 1.0
    a_base, b_base = a["power"], b["power"]
    if friendly:
        a_base = b_base = hi
        if a["power"] <= b["power"]:
            a_bonus = 1.06
        else:
            b_bonus = 1.06

    a_elem = ABILITY_ELEMENT.get(Ability(a["dominant"]), "") if a.get("dominant") else ""
    b_elem = ABILITY_ELEMENT.get(Ability(b["dominant"]), "") if b.get("dominant") else ""
    adv = _elem_advantage(a_elem, b_elem)

    rounds: list[BattleRound] = []
    a_wins = b_wins = 0
    round_labels = ["速度回合", "力量回合", "默契回合"]
    for i, label in enumerate(round_labels):
        a_factor = a_bonus * (1.0 + (0.12 if adv > 0 else 0.0))
        b_factor = b_bonus * (1.0 + (0.12 if adv < 0 else 0.0))
        ar = _roll((a["id"], b["id"], day, n, "a", i), a_base, a_factor)
        br = _roll((a["id"], b["id"], day, n, "b", i), b_base, b_factor)
        if ar > br:
            w, _x = "a", a_wins
            a_wins += 1
        elif br > ar:
            w = "b"
            b_wins += 1
        else:
            w = "tie"
        rounds.append(BattleRound(label=label, a_roll=ar, b_roll=br, winner=w))

    if a_wins > b_wins:
        winner = "a"
    elif b_wins > a_wins:
        winner = "b"
    else:
        winner = "a" if a["power"] >= b["power"] else "b"

    # 奖励：双方都拿"参与"（社交也是努力）；赢家多一点；输家绝不被罚。
    base = {"stars": 1, "growth": 8}
    a_reward = dict(base)
    b_reward = dict(base)
    if winner == "a":
        a_reward = {"stars": 3, "growth": 14}
    elif winner == "b":
        b_reward = {"stars": 3, "growth": 14}
    if friendly:   # 友谊赛里"弱者"额外鼓励，输了也开心
        weak = "a" if a_idx < b_idx else "b"
        (a_reward if weak == "a" else b_reward)["stars"] += 1

    wname = a["name"] if winner == "a" else b["name"]
    if friendly:
        narration = f"{a['name']} 和 {b['name']} 来了一场友谊赛——切磋一下，谁都有收获！"
    else:
        narration = f"{wname} 凭借平时的努力赢下了这场对决！（{a['name']} {a_wins}:{b_wins} {b['name']}）"

    return BattleResult(
        a_id=a["id"], b_id=b["id"], a_name=a["name"], b_name=b["name"],
        a_power=a["power"], b_power=b["power"], a_rank=a_rank, b_rank=b_rank,
        winner=winner, friendly=friendly, rounds=rounds,
        a_reward=a_reward, b_reward=b_reward, narration=narration,
    )
