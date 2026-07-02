"""宠物之魂：孵化与养成的唯一真源（境界 · 灵材 · 亲和 · 活力）。

每天 5 题是唯一数据入口，这里把它派生成"喂养 → 孵蛋 → 破壳成专属宠 → 一路修行"。
- 每道题按知识域喂一颗灵材；破壳物种 = 7 天里喂养质量平均最高的域（机会公平）。
- 活力与云游是"最近何时来过"的**纯派生**，不存冗余状态：来了就满，久不来它会想你。
- 词汇约定：宠物本体的阶段叫「境界」（蛋→幼年→…）；能力条上的蒙童→一代宗师
  叫「修为」（见 cards.py）。两套阶梯各说各话，不再打架。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from growth.types import ChallengeKind

# ---- 境界（宠物本体）：蛋 7 天破壳，往上每一阶都要 时间×丹药×法器×全面修为 ----
REALMS = ["egg", "youth", "zhuji", "jiedan", "yuanying"]
REALM_ZH = {"egg": "蛋", "youth": "幼年期", "zhuji": "筑基期",
            "jiedan": "结丹期", "yuanying": "元婴期"}
DAYS_TO_HATCH = 7
AWAY_AFTER_DAYS = 3                            # 3 天没来，它就出门云游/沉睡了

# ---- 丹药：灵材按跨域配方炼成，是突破的钥匙之一 ----
PILLS = {
    "zhuji_dan": {"name": "筑基丹", "recipe": {"li": 6, "wen": 6, "bo": 6},
                  "flavor": "三系灵材各六，方能筑起道基。"},
    "jieyuan_dan": {"name": "结元丹", "recipe": {"li": 10, "wen": 10, "bo": 10},
                    "flavor": "灵材凝练成元，一颗抵千日之功。"},
    "huaying_dan": {"name": "化婴丹", "recipe": {"li": 15, "wen": 15, "bo": 15},
                    "flavor": "脱胎换骨之丹，非大毅力者不可得。"},
}


@dataclass(frozen=True)
class RealmGate:
    """突破到某境界的门槛：时间 × 丹药 × 法器 × 全面修为（五能力最低值）。"""

    days: int          # 当前境界内的"喂饱日"数
    pill: str          # 需要炼成并服下的丹药
    artifacts: int     # 需要的法器数（读书过测解锁）
    min_ability: int   # 五能力最低等级（逼均衡，不许偏科硬冲）


REALM_GATES = {
    "zhuji": RealmGate(days=10, pill="zhuji_dan", artifacts=1, min_ability=25),
    "jiedan": RealmGate(days=25, pill="jieyuan_dan", artifacts=2, min_ability=45),
    "yuanying": RealmGate(days=50, pill="huaying_dan", artifacts=3, min_ability=60),
}


def next_realm(realm: str) -> Optional[str]:
    i = REALMS.index(realm)
    return REALMS[i + 1] if i + 1 < len(REALMS) else None


def gate_for_next(realm: str) -> Optional[RealmGate]:
    nxt = next_realm(realm)
    return REALM_GATES.get(nxt) if nxt and nxt != "youth" else None

# ---- 知识域与灵材 ----
DOMAINS = ["li", "wen", "bo"]
DOMAIN_ZH = {"li": "理科", "wen": "文科", "bo": "博物"}
DOMAIN_MATERIAL = {"li": "晶尘", "wen": "韵露", "bo": "灵芝"}
DOMAIN_ELEM = {"li": "晶", "wen": "声", "bo": "风"}
DOMAIN_COLOR = {"li": "#4aa3ff", "wen": "#b07bff", "bo": "#46c98b"}

KIND_DOMAIN = {
    ChallengeKind.WARMUP: "li", ChallengeKind.LOGIC: "li",
    ChallengeKind.EXPRESSION: "wen",
    ChallengeKind.OBSERVATION: "bo", ChallengeKind.CREATION: "bo",
}
# 域 → 该域可出的题型（渴求驱动选题的原料）
DOMAIN_KINDS = {
    "li": [ChallengeKind.WARMUP, ChallengeKind.LOGIC],
    "wen": [ChallengeKind.EXPRESSION],
    "bo": [ChallengeKind.OBSERVATION, ChallengeKind.CREATION],
}

# ---- 物种（按主导域破壳） ----
SPECIES = {
    "li": {"name": "晶角龙", "emoji": "🐲", "elem": "晶",
           "blurb": "它从逻辑与数字里诞生，偏爱一切有规律的东西。"},
    "wen": {"name": "言灵狐", "emoji": "🦊", "elem": "声",
            "blurb": "它由语言与故事孕育，最爱听你把世界讲给它听。"},
    "bo": {"name": "观澜枭", "emoji": "🦉", "elem": "风",
           "blurb": "它生于好奇与观察，对万物都睁着好奇的眼睛。"},
}

# ---- 每日渴求：宠物的嘴 = 当日 5 题的域配比 ----
BASE_QUOTA = {"li": 2, "wen": 1, "bo": 2}


def quota_for(craving: Optional[str]) -> dict:
    """主渴求域 +1 题，从配额最多的其他域让出 1 题——总数恒为 5。"""
    q = dict(BASE_QUOTA)
    if craving in q:
        donor = max((d for d in q if d != craving), key=lambda d: q[d])
        q[donor] -= 1
        q[craving] += 1
    return q


DAILY_CRAVING_LINES = {
    "li": "今天丹炉里最缺「晶尘」…多陪我做几道理科题好吗？",
    "wen": "今天特别渴望「韵露」…多给我讲讲你的想法吧！",
    "bo": "今天想要「灵芝」…带我多看看这个世界！",
}

# ---- 拟人话 ----
CRAVINGS = {
    "li": ["我闻到逻辑的香味了…帮我找一颗「晶尘」好吗？", "有规律的东西最好吃啦！", "数字和推理，是我的最爱~"],
    "wen": ["好想听你讲点什么…喂我一滴「韵露」吧。", "用你的话，把世界说给我听好不好？", "文字里藏着魔法，我饿啦~"],
    "bo": ["外面的世界好神奇…帮我采一株「灵芝」？", "睁大眼睛，你看到了什么？", "好奇的东西，最有营养！"],
}
STATUS_LINES = {
    "normal": ["你来啦！我等好久了~", "我能感觉到，今天又要长大一点。", "今天也一起加油哦！", "我饿啦，喂我吃今天的灵材吧~"],
    "sleeping": ["蛋沉沉睡着了…喂它今天的灵材，把它唤醒吧。"],
    "away": ["它出门云游历练去了…完成今天的修炼，就能召回它！"],
    "recalled": ["被唤回来啦！它在等你一起修炼~"],
}


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


def _days_between(a: Optional[str], b: str) -> int:
    if not a:
        return 0
    try:
        return max(0, (date.fromisoformat(b) - date.fromisoformat(a)).days)
    except ValueError:
        return 0


@dataclass
class Cultivation:
    realm: str = "egg"
    realm_day: int = 0                   # 当前境界内已"喂饱"的天数
    last_fed_day: Optional[str] = None   # 上次喂饱（5/5）的日子：境界推进的节拍
    last_active_day: Optional[str] = None  # 上次来过（答过任一题）：活力/云游的依据
    recall_day: Optional[str] = None     # 家长召回日：当天云游立刻结束
    materials: dict = field(default_factory=lambda: {d: 0 for d in DOMAINS})
    aff_sum: dict = field(default_factory=lambda: {d: 0.0 for d in DOMAINS})
    aff_n: dict = field(default_factory=lambda: {d: 0 for d in DOMAINS})
    species: Optional[str] = None
    hatched_day: Optional[str] = None
    pills: dict = field(default_factory=dict)       # pill_id -> 数量（炼成待服）
    artifacts: list = field(default_factory=list)   # 已解锁的法器（book id）

    # ---- 喂养（每答一题；任何境界都在积累） ----
    def feed(self, domain: str, correct: Optional[bool], credit: float) -> None:
        self.materials[domain] = self.materials.get(domain, 0) + 1
        self.aff_sum[domain] = self.aff_sum.get(domain, 0.0) + quality(correct, credit)
        self.aff_n[domain] = self.aff_n.get(domain, 0) + 1

    def feed_total(self) -> float:
        """质量加权的喂养总量——体魄（HP）的养料。"""
        return sum(self.aff_sum.values())

    def touch_active(self, day: str) -> bool:
        """记一次到场。若它正云游/沉睡，则被这次到场唤回——返回 True 让上层庆祝。"""
        was_away = self.status(day)["mode"] in ("away", "sleeping")
        self.last_active_day = day
        return was_away

    # ---- 活力/云游：纯派生 ----
    def status(self, day: str) -> dict:
        gap = _days_between(self.last_active_day, day)
        if self.last_active_day is None:
            vitality, mode = 70, "normal"
        else:
            vitality = max(15, 100 - 22 * gap)
            mode = "normal" if gap < AWAY_AFTER_DAYS else ("sleeping" if self.realm == "egg" else "away")
        if self.recall_day == day and mode != "normal":
            vitality, mode = max(vitality, 60), "recalled"
        lines = STATUS_LINES.get(mode, STATUS_LINES["normal"])
        return {"vitality": vitality, "mode": mode,
                "line": lines[self.realm_day % len(lines)]}

    # ---- 亲和与物种 ----
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

    # ---- 每日渴求：修行需求说话，其次补短板 ----
    def daily_craving(self, day: str) -> str:
        """今天最想要的域。优先：下一颗突破丹还缺的灵材；否则亲和最弱的域（补短板）。
        平局按日轮换——不许任何一个域被永久冷落（否则对应能力会停滞）。"""
        gate = gate_for_next(self.realm)
        if gate is not None and self.pills.get(gate.pill, 0) == 0:
            recipe = PILLS[gate.pill]["recipe"]
            deficit = {d: recipe.get(d, 0) - self.materials.get(d, 0) for d in DOMAINS}
            if max(deficit.values()) > 0:
                return max(DOMAINS, key=lambda d: deficit[d])
        avg = self.avg_affinity()
        weakest = min(avg.values())
        cands = [d for d in DOMAINS if avg[d] <= weakest + 1e-9]
        return cands[date.fromisoformat(day).toordinal() % len(cands)]

    # ---- 炼丹：一炉丹只为眼前这道门（不许囤积，灵材留给下一境界） ----
    def craft_check(self, pill_id: str) -> Optional[str]:
        """能否开炉。返回 None=可以，否则给出温柔的拒绝理由（错误码）。"""
        gate = gate_for_next(self.realm)
        if gate is None or gate.pill != pill_id:
            return "wrong_pill"
        if self.pills.get(pill_id, 0) >= 1:
            return "already_have"
        recipe = PILLS[pill_id]["recipe"]
        if any(self.materials.get(d, 0) < n for d, n in recipe.items()):
            return "not_enough_materials"
        return None

    def craft(self, pill_id: str) -> bool:
        """配方凑齐 → 消耗灵材，炼成一颗丹。确定性，无失败率（不做赌博）。"""
        if self.craft_check(pill_id) is not None:
            return False
        for d, n in PILLS[pill_id]["recipe"].items():
            self.materials[d] -= n
        self.pills[pill_id] = self.pills.get(pill_id, 0) + 1
        return True

    # ---- 境界推进：每个喂饱日 +1；蛋满 7 天破壳 ----
    def advance_day(self, day: str) -> Optional[str]:
        """返回破壳的物种域（若本次破壳），否则 None。只在"今日 5 题喂饱"时调。"""
        if self.last_fed_day == day:
            return None
        self.realm_day += 1
        self.last_fed_day = day
        if self.realm == "egg" and self.realm_day >= DAYS_TO_HATCH:
            self.realm = "youth"
            self.realm_day = 0
            self.species = self.dominant()
            self.hatched_day = day
            return self.species
        return None

    def to_dict(self) -> dict:
        return {
            "realm": self.realm, "realm_day": self.realm_day,
            "last_fed_day": self.last_fed_day, "last_active_day": self.last_active_day,
            "recall_day": self.recall_day,
            "materials": self.materials, "aff_sum": self.aff_sum, "aff_n": self.aff_n,
            "species": self.species, "hatched_day": self.hatched_day,
            "pills": self.pills, "artifacts": self.artifacts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cultivation":
        d = d or {}
        return cls(
            realm=d.get("realm", "egg"), realm_day=d.get("realm_day", 0),
            last_fed_day=d.get("last_fed_day"), last_active_day=d.get("last_active_day"),
            recall_day=d.get("recall_day"),
            materials=d.get("materials", {x: 0 for x in DOMAINS}),
            aff_sum=d.get("aff_sum", {x: 0.0 for x in DOMAINS}),
            aff_n=d.get("aff_n", {x: 0 for x in DOMAINS}),
            species=d.get("species"), hatched_day=d.get("hatched_day"),
            pills=d.get("pills", {}), artifacts=d.get("artifacts", []),
        )
