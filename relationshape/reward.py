"""奖励系统：稀缺、具体、奖励行为而非人格。

行为科学依据：
- 廉价的泛化表扬会迅速贬值，且培养"讨表扬"而不是真实交流
  （表扬研究的共识：表扬具体行为/过程，不表扬'你真棒'式人格标签）。
- 这里刻意不用变率强化（老虎机式随机奖励会制造成瘾性依赖，
  与"不制造依赖"的边界冲突）：奖励是确定性的、有据可查的。
- 峰终定律：里程碑庆祝是关系记忆里的"峰"，值得保留且稀有。

奖励的不是"你这个人"，而是：说出来这个行为（trust）、
让我记住了一件事（memory_seed）、把话题推进了一层（progress）、
我们一起走到了某天（milestone）。
"""

from __future__ import annotations

import hashlib

from relationshape.types import (
    ConversationFrame,
    InputType,
    RewardPlan,
    RewardType,
)


def _key(rtype: RewardType, material: str) -> str:
    return hashlib.md5(f"{rtype.value}|{material}".encode()).hexdigest()[:8]


def plan_reward(
    frame: ConversationFrame,
    learned_hint: list[str],
    milestone: int | None,
    turns_since_reward: int,
    cooldown: int,
    session_reward_count: int,
    session_cap: int,
    recent_keys: list[str],
) -> tuple[RewardPlan | None, str | None]:
    """返回 (奖励计划, 去重键)。普通轮、低信息轮一律不奖励。"""

    # 里程碑无视冷却：它本身就极稀有
    if milestone is not None:
        key = _key(RewardType.MILESTONE, str(milestone))
        if key not in recent_keys:
            return RewardPlan(
                rtype=RewardType.MILESTONE,
                reason=f"认识满{milestone}天",
                guidance=f"轻轻提一句你们认识{milestone}天了，带一个真实的共同细节，不煽情不拖长",
            ), key

    if session_reward_count >= session_cap or turns_since_reward < cooldown:
        return None, None

    # 信任奖励：对方把难说的事说出来了——奖励"说出来"这个行为本身
    if frame.disclosure_depth >= 2 and frame.input_type in (
        InputType.SELF_DISTRESS, InputType.SELF_BLAME, InputType.EXTERNAL_COMPLAINT,
    ):
        key = _key(RewardType.TRUST, frame.input_type.value)
        if key not in recent_keys:
            return RewardPlan(
                rtype=RewardType.TRUST,
                reason="对方表露了真实感受",
                guidance="认真接住：'谢谢你愿意跟我说这个'的意思，用自己的话讲，不夸对方人格",
            ), key

    # 记忆种子：学到了对方的新事实，轻轻确认
    if learned_hint:
        key = _key(RewardType.MEMORY_SEED, learned_hint[0])
        if key not in recent_keys:
            return RewardPlan(
                rtype=RewardType.MEMORY_SEED,
                reason=f"新记住：{learned_hint[0]}",
                guidance=f"轻轻确认记住了（{learned_hint[0]}），一句就够，不要连环追问隐私",
            ), key

    # 进展奖励：创作/想法话题被实质推进
    if frame.input_type == InputType.CREATIVE_TOPIC and frame.substantive and frame.topic_tokens:
        key = _key(RewardType.PROGRESS, frame.topic_tokens[0])
        if key not in recent_keys:
            return RewardPlan(
                rtype=RewardType.PROGRESS,
                reason=f"想法「{frame.topic_tokens[0]}」被推进了",
                guidance="点明这个想法比刚才更具体了（具体到哪一层），然后继续聊问题本身",
            ), key

    return None, None
