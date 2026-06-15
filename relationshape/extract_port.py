"""记忆抽取端口：把"从一句话里抽结构化事实"做成可替换契约。

默认实现是 memory.py 里的规则启发式（关键词正则，精确但口语覆盖有限）。本模块定义一个
**可选的 LLM 抽取层**接口：上层把自己的大模型接进来补齐口语/语义句式的召回，引擎仍然
零外部依赖、默认纯规则（不接就退化成原行为）。

设计要点（与本库一贯精确率优先一致）：
  · 端口只产出**结构化事实**，由 MemoryBank.apply_extracted 统一过门槛落地（去重/撤回/
    角色词拦截/问句拦截），LLM 不直接写记忆。
  · 落地时强制**子串接地**：抽出的值必须是原文的子串，LLM 只能"框选/归一"原文片段，
    不能凭空造词——即便模型乱编，也污染不进记忆（精确率不变量的最后一道闸）。
  · 提示词强约束：只要第一人称自述、排除第三方、排除问句、区分喜欢/讨厌/不再喜欢。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple


@dataclass
class ExtractedFacts:
    """一句话里抽出的用户自述事实（结构化、稳定契约）。"""
    name: Optional[str] = None
    likes: List[str] = field(default_factory=list)
    dislikes: List[str] = field(default_factory=list)
    friends: List[Tuple[str, str]] = field(default_factory=list)   # (人名, 关系)
    events: List[str] = field(default_factory=list)
    cared: List[str] = field(default_factory=list)
    retract_likes: List[str] = field(default_factory=list)          # 不再喜欢/玩腻了

    def is_empty(self) -> bool:
        return not (self.name or self.likes or self.dislikes or self.friends
                    or self.events or self.cared or self.retract_likes)


class MemoryExtractorPort(Protocol):
    """上层把自己的 LLM 实现成这个接口即可接入（见 eval/llm_extractor.py 参考实现）。"""

    def extract(self, text: str) -> Optional[ExtractedFacts]:
        ...


# ── 抽取提示词：强约束精确率。值要干净名词短语、第一人称、排除第三方与问句 ──
EXTRACT_SYSTEM = (
    "你是儿童陪伴对话系统的事实抽取器。只从用户这一句话里抽取【用户关于自己】的稳定事实，"
    "输出严格 JSON。绝对规则：\n"
    "1. 只要第一人称自述。第三方（妈妈/爸爸/老师/朋友/他/她…）喜欢什么、做什么，一律不抽。\n"
    "2. 问句/反问/检索（如「我叫什么来着」「你还记得我爱玩啥吗」）不是陈述，全部留空。\n"
    "3. 闲聊、情绪、设备抱怨、对AI的评价都不是事实，留空。\n"
    "4. 区分：喜欢→likes；不喜欢/讨厌/怕/不爱吃→dislikes；不再喜欢/玩腻了/没兴趣了→retract_likes。\n"
    "5. 名字只在明确自报时给（我叫/小名/大名/喊我X）；学生/好人等泛称不是名字。\n"
    "6. friends 只收「有具体人名+关系」的身边人，元素为 [人名, 关系]。\n"
    "7. 每个值都必须是原话里出现过的干净名词或短语：去掉动词/否定词/语气词"
    "（「不喜欢吃苦瓜」→苦瓜；「喜欢搭乐高积木」→乐高积木 或 搭乐高积木）。\n"
    "8. 宁缺毋滥：拿不准就不写。没有就给空数组/null。\n"
    "只输出 JSON，键："
    "{\"name\":null,\"likes\":[],\"dislikes\":[],\"friends\":[],\"events\":[],"
    "\"cared\":[],\"retract_likes\":[]}"
)

# 少量示例锚定精确率（尤其第三方与问句两个高频陷阱）
EXTRACT_SHOTS = [
    ("我妈妈总喜欢给我煮面条，但我不喜欢吃面条",
     {"name": None, "likes": [], "dislikes": ["面条"], "friends": [],
      "events": [], "cared": [], "retract_likes": []}),
    ("你还记得我最喜欢什么吗",
     {"name": None, "likes": [], "dislikes": [], "friends": [],
      "events": [], "cared": [], "retract_likes": []}),
    ("大伙儿都喊我团子，我跟小满是一对死党，我现在不爱搭城堡了",
     {"name": "团子", "likes": [], "dislikes": [], "friends": [["小满", "死党"]],
      "events": [], "cared": [], "retract_likes": ["搭城堡"]}),
]


def build_messages(text: str) -> List[dict]:
    """组装 chat 接口的 messages（OpenAI/DeepSeek 兼容）。"""
    msgs: List[dict] = [{"role": "system", "content": EXTRACT_SYSTEM}]
    for u, a in EXTRACT_SHOTS:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": json.dumps(a, ensure_ascii=False)})
    msgs.append({"role": "user", "content": text})
    return msgs


def _as_str_list(v) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if isinstance(x, (str, int)) and str(x).strip()]


def parse_extraction(raw: str) -> ExtractedFacts:
    """把模型返回（可能裹着多余文本）解析成 ExtractedFacts；任何异常都安全退化为空。"""
    if not raw:
        return ExtractedFacts()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return ExtractedFacts()
    try:
        d = json.loads(m.group(0))
    except (ValueError, TypeError):
        return ExtractedFacts()
    if not isinstance(d, dict):
        return ExtractedFacts()
    name = d.get("name")
    name = str(name).strip() if isinstance(name, str) and name.strip() else None
    friends: List[Tuple[str, str]] = []
    for f in d.get("friends", []) if isinstance(d.get("friends"), list) else []:
        if isinstance(f, (list, tuple)) and len(f) >= 2 and str(f[0]).strip() and str(f[1]).strip():
            friends.append((str(f[0]).strip(), str(f[1]).strip()))
        elif isinstance(f, dict) and str(f.get("name", "")).strip():
            friends.append((str(f["name"]).strip(), str(f.get("relation", "")).strip() or "朋友"))
    return ExtractedFacts(
        name=name,
        likes=_as_str_list(d.get("likes")),
        dislikes=_as_str_list(d.get("dislikes")),
        friends=friends,
        events=_as_str_list(d.get("events")),
        cared=_as_str_list(d.get("cared")),
        retract_likes=_as_str_list(d.get("retract_likes")),
    )
