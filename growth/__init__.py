"""growth —— AI 最强大脑挑战机的"成长挑战"系统逻辑（headless 引擎）。

和 relationshape（陪伴/情商引擎）并列、共享同一种气质：纯标准库、零外部依赖、
时间由参数注入、每个对象 to_dict/from_dict、判分与状态演进全在服务端。

  能力评估(abilities) → 个性化挑战(challenges/bank_data) → 努力奖励(rewards)
  → 养成宠物(pet) → 碰一碰对战(battle) → 家长报告(report)

唯一入口是 GrowthEngine；server/ 只是把它的方法翻成 JSON，client/ 只渲染 JSON。
"""

from growth.engine import GrowthEngine
from growth.challenges import ChallengeBank
from growth.types import Ability, ChallengeKind

__all__ = ["GrowthEngine", "ChallengeBank", "Ability", "ChallengeKind"]
