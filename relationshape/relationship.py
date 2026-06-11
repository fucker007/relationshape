"""关系系统：信任账本、阶段推进、裂痕与修复、仪式。

理论骨架：
- Knapp 阶段模型：关系按阶段生长，每阶段有不同的表达尺度。
- 社会渗透理论：自我表露的深度是亲密度的真实货币，比聊天轮数重要。
- Gottman 情感邀请：被"转向回应"的邀请逐笔记入信任。
- 裂痕-修复（attachment 研究）：修复了的冲突反而加深信任；
  修不好的裂痕累积才是关系倒退的原因。
- 峰终定律 & 仪式（互动仪式链）：重逢、收尾、纪念日是关系的固定节拍。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from relationshape.config import EngineConfig
from relationshape.types import STAGE_ORDER, Stage


@dataclass
class Ledger:
    trust: float = 0.0            # 0..100：可靠 + 善意 + 诚实的累计
    closeness: float = 0.0        # 0..100：暴露时长 + 表露互惠 + 共同文化
    substantive_turns: int = 0
    disclosures: int = 0          # 深度>=1 的表露次数
    deep_disclosures: int = 0     # 深度>=3
    bids_toward: int = 0
    promises_kept: int = 0
    promises_broken: int = 0
    ruptures_open: int = 0
    ruptures_repaired: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Ledger":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class RelationshipCore:
    stage: Stage = Stage.STRANGER
    first_met: str = ""           # ISO
    last_seen: str = ""           # ISO
    sessions: int = 0
    milestones_done: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value,
            "first_met": self.first_met,
            "last_seen": self.last_seen,
            "sessions": self.sessions,
            "milestones_done": self.milestones_done,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RelationshipCore":
        return cls(
            stage=Stage(d.get("stage", "stranger")),
            first_met=d.get("first_met", ""),
            last_seen=d.get("last_seen", ""),
            sessions=d.get("sessions", 0),
            milestones_done=d.get("milestones_done", []),
        )


# ---------------------------------------------------------------------------
# 账本记账
# ---------------------------------------------------------------------------


def record_substantive_turn(ledger: Ledger, disclosure_depth: int) -> None:
    ledger.substantive_turns += 1
    ledger.bids_toward += 1
    ledger.trust = min(100.0, ledger.trust + 0.5)
    ledger.closeness = min(100.0, ledger.closeness + 0.4)
    if disclosure_depth >= 1:
        ledger.disclosures += 1
        ledger.trust = min(100.0, ledger.trust + 1.2 * disclosure_depth)
        ledger.closeness = min(100.0, ledger.closeness + 0.8 * disclosure_depth)
    if disclosure_depth >= 3:
        ledger.deep_disclosures += 1


def record_session(ledger: Ledger) -> None:
    ledger.trust = min(100.0, ledger.trust + 0.3)
    ledger.closeness = min(100.0, ledger.closeness + 1.0)


def record_rupture(ledger: Ledger) -> None:
    ledger.ruptures_open += 1
    ledger.trust = max(0.0, ledger.trust - 4.0)


def record_repair(ledger: Ledger) -> None:
    if ledger.ruptures_open > 0:
        ledger.ruptures_open -= 1
        ledger.ruptures_repaired += 1
        # 修复成功的冲突净增信任：吵过并和好的朋友更结实
        ledger.trust = min(100.0, ledger.trust + 6.0)


def record_promise(ledger: Ledger, kept: bool) -> None:
    if kept:
        ledger.promises_kept += 1
        ledger.trust = min(100.0, ledger.trust + 6.0)
    else:
        ledger.promises_broken += 1
        ledger.trust = max(0.0, ledger.trust - 8.0)


def apply_absence(ledger: Ledger, gap_days: float, cfg: EngineConfig) -> None:
    """长期缺席时亲密度缓慢降温（不惩罚正常忙碌，绝不在对话中指责）。"""
    over = gap_days - cfg.absence_grace_days
    if over <= 0:
        return
    k = 0.5 ** (over / cfg.closeness_half_life_days)
    ledger.closeness *= k


# ---------------------------------------------------------------------------
# 阶段推进 / 倒退
# ---------------------------------------------------------------------------


def try_progress(
    core: RelationshipCore, ledger: Ledger, culture_size: int, now: datetime, cfg: EngineConfig,
) -> Stage | None:
    idx = STAGE_ORDER.index(core.stage)
    if idx >= len(STAGE_ORDER) - 1:
        return None
    nxt = STAGE_ORDER[idx + 1]
    gate = cfg.stage_gates[nxt]
    days = (now - datetime.fromisoformat(core.first_met)).days if core.first_met else 0
    ok = (
        days >= gate.min_days
        and core.sessions >= gate.min_sessions
        and ledger.substantive_turns >= gate.min_substantive
        and ledger.trust >= gate.min_trust
        and ledger.disclosures >= gate.min_disclosures
        and ledger.deep_disclosures >= gate.min_deep_disclosures
        and culture_size >= gate.min_culture
        and ledger.ruptures_open == 0
    )
    if ok:
        core.stage = nxt
        return nxt
    return None


def maybe_demote(core: RelationshipCore, ledger: Ledger, cfg: EngineConfig) -> Stage | None:
    """只有未修复裂痕的累积才让关系倒退；缺席不降阶。"""
    if ledger.ruptures_open >= cfg.rupture_demote_threshold:
        idx = STAGE_ORDER.index(core.stage)
        if idx > 0:
            core.stage = STAGE_ORDER[idx - 1]
            ledger.ruptures_open = 0
            return core.stage
    return None


# ---------------------------------------------------------------------------
# 阶段表达策略
# ---------------------------------------------------------------------------


@dataclass
class StagePolicy:
    max_self_disclosure: int       # 角色自我表露上限（0-3）
    nickname_allowed: bool
    tease_allowed: bool
    callbacks_allowed: bool
    question_intimacy: str         # low / medium / high：可以问多私密的问题
    politeness: str                # negative=不冒犯式礼貌 / positive=热络式亲近
    wistful_allowed: bool          # 是否允许表达"一点点失落"


_POLICIES: dict[Stage, StagePolicy] = {
    Stage.STRANGER: StagePolicy(0, False, False, False, "low", "negative", False),
    Stage.ACQUAINTANCE: StagePolicy(1, False, False, False, "low", "negative", False),
    Stage.FAMILIAR: StagePolicy(1, True, False, True, "medium", "positive", True),
    Stage.COMPANION: StagePolicy(2, True, True, True, "medium", "positive", True),
    Stage.CONFIDANT: StagePolicy(3, True, True, True, "high", "positive", True),
}


def policy_for(stage: Stage) -> StagePolicy:
    return _POLICIES[stage]


def stage_label(stage: Stage) -> str:
    return {
        Stage.STRANGER: "初识（礼貌克制，不过度熟络）",
        Stage.ACQUAINTANCE: "相识（开始记住对方，轻度好奇）",
        Stage.FAMILIAR: "熟悉（可以玩笑，可引用共同记忆）",
        Stage.COMPANION: "同伴（有默契和约定，可轻度打趣）",
        Stage.CONFIDANT: "知己（可有限脆弱，最深的默契）",
    }[stage]


def milestone_due(core: RelationshipCore, now: datetime, cfg: EngineConfig) -> int | None:
    """只在里程碑日的 7 天窗口内庆祝；错过就让它过去，不补庆祝。"""
    if not core.first_met:
        return None
    days = (now - datetime.fromisoformat(core.first_met)).days
    for m in cfg.milestone_days:
        if m not in core.milestones_done and m <= days <= m + 7:
            return m
    return None
