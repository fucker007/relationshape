"""
retrieval/decider.py — Phase 1: 记忆使用决策层

解决核心问题：「召回到了 ≠ 应该用」

三个子模块：
  1. IntentClassifier  — 将 query 分类为 7 种意图
  2. TypeGate          — 意图 → 允许使用的 MemoryType 集合（硬约束）
  3. RelevanceJudge    — (query, memory) → relevance_score + use/skip

组合入口：
  MemoryUsageDecider.filter(query, memories) → list[MemoryEntry]（只保留应该用的）
  MemoryUsageDecider.judge(query, memories)  → list[JudgeResult]（带分数）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from models.enums import MemoryType
from models.memory import MemoryEntry


# ---------------------------------------------------------------------------
# 意图类型
# ---------------------------------------------------------------------------

Intent = Literal[
    "identity",      # 问身份：你叫什么、你多大、你住哪
    "preference",    # 问喜好：你喜欢什么、你爱吃什么
    "emotion",       # 问情绪/情感：你开心吗、你难过了吗
    "relationship",  # 问人际：你有朋友吗、谁是你好友
    "behavior",      # 问习惯：你每天怎么过、你有什么习惯
    "personality",   # 问性格：你是什么性格、你外向吗
    "general",       # 通用/无法分类
]


# ---------------------------------------------------------------------------
# TypeGate：意图 → 允许的 MemoryType 集合
# None 表示无限制（general）
# ---------------------------------------------------------------------------

_TYPE_GATE: dict[str, set[MemoryType] | None] = {
    "identity":     {MemoryType.IDENTITY},
    "preference":   {MemoryType.PREFERENCE, MemoryType.AVERSION},
    "emotion":      {MemoryType.JOY, MemoryType.PAIN},
    "relationship": {MemoryType.RELATIONSHIP},
    "behavior":     {MemoryType.BEHAVIOR},
    "personality":  {MemoryType.PERSONALITY},
    "general":      None,  # 不限制类型
}


# ---------------------------------------------------------------------------
# 意图分类规则
# ---------------------------------------------------------------------------

# 每条规则：(pattern_list, intent)
# 先匹配先得，顺序重要
_INTENT_RULES: list[tuple[list[str], str]] = [
    # ── identity（先匹配，防止被 preference/emotion 抢走）──
    ([r'你叫什么', r'你的名字', r'叫啥', r'叫什么名字', r'认识我吗', r'知道我是谁',
      r'我叫什么', r'我的名字', r'你知道我吗', r'几岁', r'多大了', r'你多大',
      r'住哪', r'住在哪', r'在哪上学', r'读几年级', r'上几年级', r'在哪里住',
      r'哪里人', r'老家', r'家在哪'], "identity"),

    # ── relationship ──
    ([r'好朋友', r'好友', r'朋友叫什么', r'朋友是谁', r'死党', r'闺蜜',
      r'谁是.{0,4}朋友', r'朋友有哪些', r'老师是谁', r'老师叫什么', r'哪个老师',
      r'班主任', r'家人', r'爸爸妈妈', r'兄弟姐妹', r'喜欢哪个老师',
      r'和谁关系', r'跟谁最好', r'有什么朋友', r'有哪些朋友',
      r'认识哪些[人朋友]', r'跟谁[最好关系]'], "relationship"),

    # ── preference ──
    ([r'喜欢吃', r'爱吃', r'最爱', r'喜欢什么', r'爱好是什么', r'喜欢哪', r'最喜欢',
      r'不喜欢吃', r'讨厌吃', r'不爱吃', r'帮我.{0,6}决定', r'点什么', r'吃什么好',
      r'想吃', r'推荐.{0,4}(食物|菜|吃)', r'玩什么好', r'喜欢玩',
      r'爱玩', r'喜欢哪个游戏', r'兴趣爱好', r'有什么爱好', r'喜欢什么运动'], "preference"),

    # ── emotion ──
    ([r'开心吗', r'快乐吗', r'高兴吗', r'难过吗', r'心情怎么样', r'伤心吗',
      r'最开心', r'最难过',
      r'什么让[你我]开心', r'什么让[你我]难过', r'什么让[你我]高兴',
      r'让[你我][开心高兴快乐]', r'让[你我][难过伤心]',
      r'有没有让[你我][高兴快乐开心]', r'有没有让[你我][难受难过]',
      r'情绪', r'心情好不好', r'最近心情', r'开心的事', r'难过的事'], "emotion"),

    # ── behavior ──
    ([r'习惯', r'每天怎么', r'作息', r'几点起床', r'几点睡', r'日常',
      r'平时怎么', r'规律', r'生活习惯', r'有什么习惯', r'放学后',
      r'睡前', r'饭前', r'早上第一件事', r'固定的习惯'], "behavior"),

    # ── personality ──
    ([r'性格', r'外向还是内向', r'内向吗', r'外向吗', r'什么样的人',
      r'你是那种', r'乐观吗', r'悲观吗', r'敏感吗', r'认真吗',
      r'粗心吗', r'你的特点', r'你是个怎样的'], "personality"),
]

_COMPILED_INTENT_RULES: list[tuple[list[re.Pattern], str]] = [
    ([re.compile(p) for p in pats], intent)
    for pats, intent in _INTENT_RULES
]


# ---------------------------------------------------------------------------
# IntentClassifier
# ---------------------------------------------------------------------------

class IntentClassifier:
    """基于规则的 query 意图分类器"""

    def classify(self, query: str) -> Intent:  # type: ignore[return]
        for patterns, intent in _COMPILED_INTENT_RULES:
            for pat in patterns:
                if pat.search(query):
                    return intent  # type: ignore[return-value]
        return "general"


# ---------------------------------------------------------------------------
# 相关性评分工具
# ---------------------------------------------------------------------------

def _char_bigrams(text: str) -> set[str]:
    """提取字符 bigram 集合"""
    t = re.sub(r'\s+', '', text)
    return {t[i:i+2] for i in range(len(t) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _keyword_overlap(query: str, content: str) -> float:
    """基于字符 bigram 的 Jaccard 相似度"""
    return _jaccard(_char_bigrams(query), _char_bigrams(content))


# ---------------------------------------------------------------------------
# RelevanceJudge
# ---------------------------------------------------------------------------

# 使用阈值
_USE_THRESHOLD = 0.05   # 低于此分数的记忆 skip
_HIGH_THRESHOLD = 0.12  # 高于此分数视为强相关

class RelevanceJudge:
    """
    评估 (query, memory) 的相关性。

    相关性分数 = 0.6 * char_bigram_jaccard(query, content)
               + 0.25 * importance_score
               + 0.15 * (1 if type_allowed else 0)
    """

    def score(self, query: str, memory: MemoryEntry,
              allowed_types: set[MemoryType] | None) -> float:
        kw = _keyword_overlap(query, memory.content)
        imp = getattr(memory, 'importance_score', 0.5)
        type_bonus = 1.0 if (allowed_types is None or
                              memory.memory_type in allowed_types) else 0.0
        return 0.6 * kw + 0.25 * imp + 0.15 * type_bonus

    def should_use(self, query: str, memory: MemoryEntry,
                   allowed_types: set[MemoryType] | None) -> tuple[bool, float]:
        """返回 (use, score)"""
        # 硬约束：类型不在允许列表则直接 skip（general 除外）
        if allowed_types is not None and memory.memory_type not in allowed_types:
            return False, 0.0
        s = self.score(query, memory, allowed_types)
        return s >= _USE_THRESHOLD, s


# ---------------------------------------------------------------------------
# JudgeResult
# ---------------------------------------------------------------------------

@dataclass
class JudgeResult:
    memory: MemoryEntry
    relevance_score: float
    use: bool
    reason: str = ""

    @property
    def memory_type(self) -> MemoryType:
        return self.memory.memory_type


# ---------------------------------------------------------------------------
# MemoryUsageDecider — 组合入口
# ---------------------------------------------------------------------------

class MemoryUsageDecider:
    """
    Phase 1 记忆使用决策层：

    输入：query + list[MemoryEntry]（已召回）
    输出：经过过滤的 list[MemoryEntry]（应该用的）

    流程：
      1. IntentClassifier  → intent
      2. TypeGate          → allowed_types（硬约束）
      3. RelevanceJudge    → per-memory use/skip
    """

    def __init__(self) -> None:
        self._classifier = IntentClassifier()
        self._judge = RelevanceJudge()

    # ── public API ──────────────────────────────────────────────────────────

    def classify_intent(self, query: str) -> Intent:
        return self._classifier.classify(query)

    def allowed_types(self, intent: str) -> set[MemoryType] | None:
        return _TYPE_GATE.get(intent)

    def judge(self, query: str,
              memories: list[MemoryEntry]) -> list[JudgeResult]:
        """返回所有记忆的判断结果（含 use=False 的）"""
        intent = self._classifier.classify(query)
        allowed = self.allowed_types(intent)

        results: list[JudgeResult] = []
        for mem in memories:
            use, score = self._judge.should_use(query, mem, allowed)
            reason = f"intent={intent}"
            if allowed is not None and mem.memory_type not in allowed:
                reason += f" type_blocked({mem.memory_type.value})"
            results.append(JudgeResult(memory=mem, relevance_score=score,
                                        use=use, reason=reason))
        return results

    def filter(self, query: str,
               memories: list[MemoryEntry]) -> list[MemoryEntry]:
        """只返回应该使用的记忆"""
        return [r.memory for r in self.judge(query, memories) if r.use]
