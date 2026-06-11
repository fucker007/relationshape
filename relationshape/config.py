"""引擎可调参数。

所有时间常数、阶段门槛、冷却节奏集中在这里，方便按产品形态整体调整。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from relationshape.types import Stage


@dataclass
class StageGate:
    """晋升到某阶段需要同时满足的条件（时间 × 互动量 × 信任 × 关系文化）。

    理论依据：关系发展是时间和实质互动共同作用的结果（Knapp 阶段模型、
    社会渗透理论），不能只靠轮次刷出来。
    """

    min_days: int
    min_sessions: int
    min_substantive: int
    min_trust: float
    min_disclosures: int = 0
    min_deep_disclosures: int = 0
    min_culture: int = 0


def _default_stage_gates() -> dict[Stage, StageGate]:
    return {
        Stage.ACQUAINTANCE: StageGate(
            min_days=1, min_sessions=2, min_substantive=6, min_trust=4
        ),
        Stage.FAMILIAR: StageGate(
            min_days=5, min_sessions=5, min_substantive=20, min_trust=15,
            min_disclosures=2,
        ),
        Stage.COMPANION: StageGate(
            min_days=18, min_sessions=10, min_substantive=55, min_trust=35,
            min_disclosures=5, min_deep_disclosures=1, min_culture=1,
        ),
        Stage.CONFIDANT: StageGate(
            min_days=50, min_sessions=25, min_substantive=150, min_trust=70,
            min_disclosures=10, min_deep_disclosures=4, min_culture=3,
        ),
    }


@dataclass
class EngineConfig:
    # 持久化目录：每个用户一份 JSON 状态
    state_dir: str = "runtime/relationshape"

    # 会话切分：超过这个间隔视为新会话
    session_gap_minutes: int = 30

    # 心境（PAD）向基线回归的半衰期：晚上被骂一句，第二天早上不应该还在记仇
    mood_half_life_hours: float = 8.0

    # 情景记忆显著度的遗忘半衰期（艾宾浩斯曲线的指数近似）；被召回会获得复习强化
    memory_half_life_days: float = 14.0
    memory_prune_threshold: float = 0.05
    episodic_cap: int = 400

    # 亲密度在长期缺席下的缓慢衰减（关系需要维护，但不惩罚正常忙碌）
    closeness_half_life_days: float = 45.0
    absence_grace_days: int = 7

    # 久别重逢的判定：超过这个间隔，开场走"重逢"仪式而不是普通问候
    reunion_gap_days: int = 7

    # 幽默与奖励的稀缺节奏：太频繁都会贬值
    humor_cooldown_turns: int = 4
    reward_cooldown_turns: int = 6
    reward_session_cap: int = 2

    # 每轮最多召回的记忆条数
    max_recall: int = 3

    # 关系里程碑（认识天数），到达时轻量庆祝一次
    milestone_days: tuple[int, ...] = (7, 30, 100, 365)

    # 未修复的裂痕累计到该值，关系降一阶（裂痕-修复模型：修复了的冲突反而加深信任）
    rupture_demote_threshold: int = 3

    # 危机场景下给模型的引导语，部署方按所在地区资源覆盖
    crisis_guidance: str = (
        "认真接住，不评判；感谢用户愿意说出来；明确这不是用户的错；"
        "鼓励告诉信任的大人或寻求专业帮助；不要追问细节，不要转移话题。"
    )

    stage_gates: dict[Stage, StageGate] = field(default_factory=_default_stage_gates)
