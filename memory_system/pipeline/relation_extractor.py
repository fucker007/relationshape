"""
pipeline/relation_extractor.py — 从对话中提取人物名 + 关联到 PersonNode

职责：
  1. 从文本中识别人物名（使用 LLM person_name_extractor）
  2. 确保每个人物在 person_nodes 中有节点
  3. 推断关系类型（friend/family/classmate/teacher/...）
  4. 返回 participant_ids 用于写入 Event
"""
from __future__ import annotations

import re
from storage.pg_store import GraphStore
from llm.client import get_llm_client
from llm.skills.person_name_extractor import extract_person_names


# 关系类型推断规则
_RELATION_INFER: list[tuple[re.Pattern, str]] = [
    (re.compile(r'妈妈|母亲|妈咪'), "family"),
    (re.compile(r'爸爸|父亲|爸比'), "family"),
    (re.compile(r'爷爷|奶奶|姥姥|姥爷|外公|外婆'), "family"),
    (re.compile(r'哥哥|姐姐|弟弟|妹妹'), "family"),
    (re.compile(r'老师|班主任|教练'), "teacher"),
    (re.compile(r'同学|同桌'), "classmate"),
    (re.compile(r'好朋友|朋友|死党|闺蜜|铁哥们|兄弟'), "friend"),
]

# 家庭成员固定称谓（直接作为名字）
_FAMILY_NAMES = {"妈妈", "爸爸", "爷爷", "奶奶", "姥姥", "姥爷", "外公", "外婆",
                  "哥哥", "姐姐", "弟弟", "妹妹"}

# 绝对不是人名的词（代词/副词/句子碎片）
_NOT_PERSON_NAMES = {
    "他", "她", "它", "我", "我们", "他们", "她们", "大家", "有人", "别人",
    "你", "自己", "本人", "咱", "谁", "什么", "怎么", "哪个",
    "今天", "昨天", "前天", "上周", "最近", "明天", "下周",
    "是的", "好的", "对的", "嗯嗯", "还好", "没有", "对啊", "好吧",
    "老师", "同学", "朋友", "同桌", "班长",  # 泛称不是具体人名
    "助手", "机器人", "小嗨", "用户",  # AI/系统身份
}

# 以这些字开头的一定不是人名
_BAD_PREFIX = ("我", "你", "他", "她", "它", "为", "但", "而", "也", "都",
               "把", "被", "让", "给", "对", "向", "从", "在", "到",
               "会", "能", "要", "想", "该", "应", "可")

# 包含这些词的一定不是人名（动词/助词/副词片段）
_BAD_CONTAINS = [
    "知道", "比较", "更偏", "还有", "谈恋", "一起", "之前", "之后",
    "觉得", "认为", "希望", "可以", "应该", "喜欢", "讨厌",
    "然后", "因为", "所以", "如果", "虽然", "不过",
    "怎么", "什么", "为什么", "比如",
]

_TIME_PREFIX_PAT = re.compile(r'^(今天|昨天|前天|上周|最近|明天|下午|上午|早上|晚上)')


def _is_valid_person_name(name: str) -> bool:
    """验证是否是合法人名"""
    if not name or len(name) > 4:
        return False
    if name in _NOT_PERSON_NAMES:
        return False
    if name in _FAMILY_NAMES:
        return True  # 家庭称谓总是合法
    # 单字不是人名
    if len(name) < 2:
        return False
    # 以代词/介词/连词开头 → 句子片段，不是人名
    if name[0] in _BAD_PREFIX:
        return False
    # 含动词/介词/副词/助词/代词 → 句子片段，不是人名。
    # 代词绝不出现在人名里（"告诉我你"）；"里最""一点"等虚词组合是明显碎片。
    # 注意不加会出现在真名里的字（如"可"→李可、"点"…），避免误伤。
    if any(w in name for w in ["在", "是", "了", "的", "不", "没", "很", "和",
                                "有时", "总是", "经常", "说他", "说她",
                                "我们", "他们", "我", "你", "他", "她", "它", "咱", "您",
                                "告诉", "让", "把", "被", "给", "里最", "一点", "然后"]):
        return False
    if any(w in name for w in _BAD_CONTAINS):
        return False
    # 以时间词开头 → 不是人名
    if _TIME_PREFIX_PAT.match(name):
        return False
    # 纯数字
    if name.isdigit():
        return False
    return True


def _infer_relation(name: str, text: str) -> str:
    """推断人物关系类型"""
    if name in _FAMILY_NAMES:
        return "family"
    for pat, rtype in _RELATION_INFER:
        if pat.search(text):
            return rtype
    return "friend"  # 默认 friend


class RelationExtractor:
    """从文本提取人物，关联到 PersonNode，返回 participant_ids"""

    def __init__(self, graph_store: GraphStore):
        self._gs = graph_store
        self._llm = get_llm_client()

    async def extract_and_link(
        self, owner_id: str, primary_person_id: str,
        participant_names: list[str], text: str,
    ) -> list[str]:
        """
        确保每个参与者在 person_nodes 中有节点，
        并创建/更新与 primary 的关系。

        返回 participant_ids 列表（包含 primary）。
        """
        pids = [primary_person_id]

        # 使用 LLM 提取人名（替代正则）
        persons = await extract_person_names(text, self._llm)

        # 合并传入的 participant_names 和 LLM 提取的人名
        all_names = set(participant_names)
        for p in persons:
            if p.get("name"):
                all_names.add(p["name"])

        # 为每个人名创建节点和关系
        for name in all_names:
            if not _is_valid_person_name(name):
                continue

            # 从 LLM 结果中查找关系类型
            rtype = "friend"  # 默认
            for p in persons:
                if p.get("name") == name:
                    rtype = p.get("type", "friend")
                    break

            # upsert PersonNode
            node = await self._gs.upsert_person_node(owner_id, name, "secondary")
            pid = str(node["person_id"])
            pids.append(pid)

            # 创建关系
            await self._gs.upsert_relationship(
                owner_id, primary_person_id, pid,
                relation_type=rtype, sentiment_delta=0.0,
            )

        return pids

    async def extract_family_mentions(
        self, owner_id: str, primary_person_id: str, text: str,
    ) -> list[str]:
        """
        额外检测文本中提到的家庭成员称谓。
        用于 event_extractor 可能漏掉的家庭称谓。
        """
        # 使用 LLM 提取（会自动识别家庭成员）
        persons = await extract_person_names(text, self._llm)

        found = []
        for p in persons:
            name = p.get("name")
            if not name or p.get("type") != "family":
                continue

            node = await self._gs.upsert_person_node(owner_id, name, "secondary")
            pid = str(node["person_id"])
            await self._gs.upsert_relationship(
                owner_id, primary_person_id, pid,
                relation_type="family", sentiment_delta=0.0,
            )
            found.append(pid)
        return found
