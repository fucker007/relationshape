"""relationshape —— 随时间生长的关系-人格-情商引擎。

引擎不调用大模型，只做两件事：
1. prepare_turn(): 在用户说完话后，产出本轮的结构化指令 TurnDirective
   （角色情绪、关系阶段、对话动作、记忆召回、幽默与奖励决策、风格与禁止项）。
2. commit(): 在助手回复完成后，让关系状态向前演进
   （记忆写入、信任账本、人格适应、阶段推进、承诺生命周期）。
"""

from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.identity import BigFive, CharacterIdentity
from relationshape.types import Stage, TurnDirective

__all__ = [
    "CompanionEngine",
    "CharacterIdentity",
    "BigFive",
    "EngineConfig",
    "Stage",
    "TurnDirective",
]
