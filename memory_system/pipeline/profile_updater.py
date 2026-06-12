"""
pipeline/profile_updater.py — Event 驱动 PersonNode 属性自动更新

职责：
  1. 事件 → 偏好变化（复用 meaning_extractor.extract_preference_change）
  2. 事件 → 关系 sentiment 变化
  3. 属性声明 → 直接更新 PersonNode 字段（identity/preference/aversion/...）
  4. 情绪 → daily_emotions 聚合

使用 LLM Skills 替代正则：
  - attribute_extractor: 提取身份属性
  - preference_extractor: 提取偏好/厌恶
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from utils.time_normalizer import normalize_time_expr

from storage.pg_store import GraphStore
from llm.client import get_llm_client
from llm.skills.attribute_extractor import extract_identity_attributes
from llm.skills.preference_extractor import extract_preferences

logger = logging.getLogger(__name__)


# P0-3: 创伤关键词
TRAUMA_KEYWORDS = {
    "想死", "自杀", "活不下去", "绝望", "崩溃", "抑郁",
    "被羞辱", "被侮辱", "被打", "被骂", "被欺负", "被霸凌",
    "失恋", "分手", "离婚", "去世", "死了", "病危",
    "被强奸", "被性侵", "被猥亵", "被虐待",
    "欺负"  # 添加"欺负"（不仅是"被欺负"）
}


def _is_trauma_event(summary: str, emotion: str) -> bool:
    """判断是否为创伤事件"""
    if emotion in ["sad", "angry", "fearful"]:
        if any(kw in summary for kw in TRAUMA_KEYWORDS):
            return True
    return False


# ---------------------------------------------------------------------------
# 属性声明分类（非事件的静态信息）
# ---------------------------------------------------------------------------

_IDENTITY_PATTERNS: list[tuple[re.Pattern, str, Any]] = [
    # 年龄 — 排除"我儿子X岁"、"我女儿X岁"、"我孩子X岁"
    (re.compile(r'(?<!我)(?:我)(?!儿子|女儿|孩子|朋友|同学|妈妈|爸爸|哥哥|姐姐|弟弟|妹妹)[^。，]*?(\d{1,2})\s*岁'), "age", None),
    # 生日 — 必须有"我"或"我的"开头
    (re.compile(r'(?:我的)?生日.*?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]'), "birthday", None),
    (re.compile(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号].*?(?:我的)?生日'), "birthday", None),
    # 学校
    (re.compile(r'(?:我)?(?:在|上|读)([^\s，。！？]{2,8}(?:小学|中学|学校|学院|大学))'), "school", None),
    # 年级
    (re.compile(r'(?:我)?(?:读|上)?([一二三四五六七八九十]年级|大[一二三四]|初[一二三]|高[一二三])'), "grade", None),
    # 名字 — 排除"我叫你"、"我叫他"、"我叫她"
    (re.compile(r'我叫(?!你|他|她|它|过来|什么)\s*([\u4e00-\u9fff]{2,4})(?:[，。！？\s]|$)'), "name", None),
    (re.compile(r'我的名字(?:是|叫)\s*([\u4e00-\u9fff]{2,4})(?:[，。！？\s]|$)'), "name", None),
    # 性别
    (re.compile(r'我是(男|女)(?:生|孩|的)'), "gender", None),
]

_PREFERENCE_PAT = re.compile(
    r'(?:我)?(?:喜欢|爱|爱吃|爱玩|爱看|爱听|最爱|超爱|特别喜欢)(?:吃|玩|看|听|做)?'
    r'([^\s，。！？]{1,10}?)(?=[，。！？\s了呢吧啊哦]|$)'
)

_AVERSION_PAT = re.compile(
    r'(?:我)?(?:不喜欢|讨厌|不爱|不想|害怕|恐惧|受不了|烦)'
    r'(?:吃|玩|看|听|做)?'
    r'([^\s，。！？]{1,10}?)(?=[，。！？\s了呢吧啊哦]|$)'
)

_BEHAVIOR_PAT = re.compile(
    r'(?:我)?(?:习惯|每天|每周|经常|总是|一直)(?:都)?'
    r'([^\s，。！？]{2,15}?)(?=[，。！？\s了呢吧啊哦]|$)'
)

_PERSONALITY_PAT = re.compile(
    r'(?:我)?(?:性格|比较|有点|其实挺|算是)'
    r'([^\s，。！？]{1,8}?)(?:的|[，。！？\s]|$)'
)

# 关系声明
_RELATION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'([^\s，。！？]{1,4})是我(?:的)?(?:好朋友|朋友|死党|闺蜜|铁哥们)'), "friend"),
    (re.compile(r'(?:我和|跟)([^\s，。！？]{1,4})(?:是|关系很)好(?:朋友|的)'), "friend"),
    (re.compile(r'([^\s，。！？]{1,4})是我(?:的)?(?:同学|同桌)'), "classmate"),
    (re.compile(r'([^\s，。！？]{1,4})(?:老师|教我)'), "teacher"),
    (re.compile(r'(?:妈妈|爸爸|爷爷|奶奶|姥姥|姥爷|哥哥|姐姐|弟弟|妹妹)'), "family"),
]

# sentiment 变化映射
_SENTIMENT_MAP = {
    "conflict": -0.2,
    "social": +0.1,
    "achievement": +0.05,
    "emotional": 0.0,  # 取决于具体情绪
    "change": 0.0,
    "daily": +0.02,
}

_EMOTION_SENTIMENT = {
    "开心": +0.1, "兴奋": +0.1, "自豪": +0.1, "感动": +0.1, "放松": +0.05,
    "难过": -0.1, "生气": -0.15, "害怕": -0.05, "委屈": -0.1,
    "焦虑": -0.05, "孤独": -0.1, "绝望": -0.2, "失望": -0.15,
    "后悔": -0.05, "嫉妒": -0.05, "尴尬": -0.02,
}


def _map_relation_value(value: str) -> str:
    """将关系描述词映射为标准 relation_type"""
    _MAP = {
        "好朋友": "friend", "朋友": "friend", "死党": "friend",
        "闺蜜": "friend", "铁哥们": "friend", "兄弟": "friend",
        "同学": "classmate", "同桌": "classmate",
        "老师": "teacher", "班主任": "teacher",
        "女朋友": "partner", "男朋友": "partner", "对象": "partner",
        "老婆": "partner", "老公": "partner", "爱人": "partner",
        "妈妈": "family", "爸爸": "family", "哥哥": "family",
        "姐姐": "family", "弟弟": "family", "妹妹": "family",
        "爷爷": "family", "奶奶": "family",
        "同事": "colleague", "上司": "colleague", "下属": "colleague",
    }
    for k, v in _MAP.items():
        if k in value:
            return v
    return "other"


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class ProfileUpdater:
    """根据提取结果更新 PersonNode 属性"""

    def __init__(self, graph_store: GraphStore):
        self._gs = graph_store
        self._llm = get_llm_client()

    @staticmethod
    def _normalize_value(value: str) -> str:
        """文本规范化：去空格、全角转半角、去特殊字符"""
        if not value:
            return ""

        # 1. 去除首尾空白（包括全角空格）
        value = value.strip().strip('\u3000').strip('\u200b')

        # 2. 全角转半角
        normalized = []
        for char in value:
            code = ord(char)
            # 全角字母数字转半角
            if 0xFF01 <= code <= 0xFF5E:
                normalized.append(chr(code - 0xFEE0))
            # 全角空格转半角
            elif code == 0x3000:
                normalized.append(' ')
            else:
                normalized.append(char)
        value = ''.join(normalized)

        # 3. 去除所有空白字符
        value = ''.join(value.split())

        # 4. 去除特殊字符（保留中文、字母、数字）
        import re
        value = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', value)

        return value

    @staticmethod
    def _is_garbage_value(value: str) -> bool:
        """检测垃圾值：单字、重复字符、纯数字、前缀污染、纯字母"""
        if not value or len(value) < 2:
            return True

        # 重复字符检测（"吃吃"、"玩玩"）- 但排除有效词（"画画"、"看看"）
        if len(set(value)) == 1 and len(value) == 2:
            # 白名单：有效的重复词
            valid_repeats = {"画画", "看看", "说说", "聊聊", "想想", "试试", "走走", "转转"}
            if value not in valid_repeats:
                return True

        # 纯数字或数字开头
        if value[0].isdigit():
            return True

        # 包含数字（"吃1"、"玩2"）
        if any(c.isdigit() for c in value):
            return True

        # 纯字母（全角转半角后的 "chi", "CHI"）
        if value.isalpha() and all(ord(c) < 128 for c in value):
            return True

        # 前缀污染检测
        garbage_prefixes = ["很", "在", "正", "要", "会", "能", "想", "爱", "不", "别", "少", "多", "常", "总"]
        for prefix in garbage_prefixes:
            if value.startswith(prefix) and len(value) <= 3:
                return True

        # 句子片段检测：包含助词/连词/介词组合 → 不是有效属性值
        _fragment_markers = ["的时候", "就是", "就听", "就看", "不如", "还不如",
                             "脾气", "虽然", "但是", "然后", "因为", "所以",
                             "如果", "不过", "而且", "或者"]
        if any(m in value for m in _fragment_markers):
            return True

        # 以代词/助词开头 → 句子碎片
        if value[0] in ("他", "她", "它", "我", "你", "的", "了", "在", "是", "和", "与"):
            return True

        # 以助词/介词结尾 → 不完整
        if value[-1] in ("和", "与", "的", "了", "在", "是", "很", "都", "也", "就"):
            return True

        # 包含系统角色词 → meta 信息泄露
        if any(w in value for w in ("用户", "助手", "机器人", "系统")):
            return True

        return False

    async def update_from_attributes(
        self, owner_id: str, person_id: str, attributes: list[dict],
    ) -> list[str]:
        """
        从结构化 attributes 直接更新 PersonNode。
        attributes 来自 EventStructurer，格式：[{field, key, value, target}]
        """
        updates: list[str] = []
        node = await self._gs.get_person_node(person_id)
        if not node:
            return updates

        for attr in attributes:
            field = attr.get("field")
            key = attr.get("key")
            value = attr.get("value")

            if not field or not key or not value:
                continue

            # 统一处理 identity 相关字段
            IDENTITY_FIELDS = {"identity", "age", "school", "grade", "birthday", "location", "gender", "name"}

            if field in IDENTITY_FIELDS:
                # 过滤掉关系字段（应该存在 Relationship 表）
                EXCLUDE_IDENTITY = {
                    "关系", "朋友", "女朋友", "男朋友", "relationship",
                    "friend", "girlfriend", "boyfriend"
                }
                if key in EXCLUDE_IDENTITY:
                    updates.append(f"identity.{key}=过滤（应存在Relationship表）")
                    continue

                identity = node["identity"] if isinstance(node["identity"], dict) else json.loads(node["identity"] or "{}")

                # 确定实际的 key（如果 field 本身就是 identity 字段名，使用 field）
                actual_key = field if field in IDENTITY_FIELDS and field != "identity" else key

                # 时间标准化（如果是生日）
                if actual_key == "birthday":
                    normalized = normalize_time_expr(value)
                    if normalized:
                        value = normalized

                identity[actual_key] = value
                await self._gs.update_person_field(person_id, "identity", identity)
                updates.append(f"identity.{actual_key}={value}")

            elif field == "preference":
                # 0. 文本规范化
                value = self._normalize_value(value)

                # 0.5. 垃圾值检测
                if self._is_garbage_value(value):
                    updates.append(f"preference.{value}=过滤（垃圾值）")
                    continue

                # 1. 长度过滤：<2字 或 >10字
                if len(value) < 2 or len(value) > 10:
                    updates.append(f"preference.{value}=过滤（长度）")
                    continue

                # 2. 语气词后缀过滤（"XXX了"/"XXX呢"/"XXX吧"）
                if value.endswith(("了", "呢", "吧", "啊", "哦", "的", "嗯")):
                    updates.append(f"preference.{value}=过滤（语气词后缀）")
                    continue

                # 3. 黑名单过滤
                EXCLUDE_PREFS = {
                    # 家庭关系
                    "女朋友", "男朋友", "朋友", "妈妈", "爸爸", "哥哥", "姐姐",
                    "弟弟", "妹妹", "爷爷", "奶奶", "姥姥", "姥爷", "舅舅", "舅妈",
                    "表哥", "表姐", "表弟", "表妹", "堂哥", "堂姐", "堂弟", "堂妹",
                    "外公", "外婆", "姑姑", "姑父", "叔叔", "婶婶", "阿姨",
                    # 代词
                    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们",
                    "自己", "别人", "大家", "有人", "某人", "谁", "什么", "怎么",
                    "俺", "咱", "咱们",
                    # 语气词/助词
                    "的", "了", "呢", "吧", "啊", "哦", "嗯", "好", "是的", "对",
                    "知道了", "明白了", "好吧", "哇", "天哪", "太好了", "真的", "嗯嗯",
                    "哦哦", "啊啊", "好好", "是是", "对对", "知道", "明白",
                    "哦哦哦", "啊啊啊", "好好好", "是是是", "对对对",
                    "知道知道", "明白明白", "还行吧", "还不错",
                    "呃", "额", "嘛", "么", "呀呀", "啦啦",
                    # 无意义短语
                    "的人", "的人吧", "人", "东西", "事情", "这个", "那个",
                    "打我", "打你",
                    # 调味料、配菜、蘸料
                    "蜂蜜芥末", "芥末", "蜂蜜", "醋", "蒜泥", "酱油", "辣椒油",
                    "番茄酱", "沙拉酱", "蛋黄酱", "黑胡椒", "盐", "糖", "油",
                    "酱", "汤", "汁", "料", "调料", "配菜", "蘸料", "口味",
                    "辣", "甜", "咸", "酸", "苦", "鲜", "香", "麻",
                    # 单字动词
                    "吃", "玩", "看", "听", "做", "说", "想", "去", "来", "走",
                }
                if value in EXCLUDE_PREFS:
                    updates.append(f"preference.{value}=过滤（黑名单）")
                    continue

                # 4. 过滤掉已知人名（人名不能作为喜好存储）
                known_persons = await self._gs.list_person_nodes(owner_id)
                known_names = {p["name"] for p in known_persons}
                if value in known_names:
                    updates.append(f"preference.{value}=过滤（人名）")
                    continue

                # 5. 矛盾检测：检查是否在 aversions 中
                aversions = node.get("aversions", []) or []
                if isinstance(aversions, str):
                    aversions = json.loads(aversions or "[]")
                aversion_items = {a.get("item") for a in aversions}

                if value in aversion_items:
                    # 移除旧的 aversion，保留新的 preference
                    aversions = [a for a in aversions if a.get("item") != value]
                    await self._gs.update_person_field(person_id, "aversions", aversions)
                    updates.append(f"aversion-={value}（矛盾冲突）")

                prefs = node.get("preferences", []) or []
                if isinstance(prefs, str):
                    prefs = json.loads(prefs or "[]")
                prefs = [p for p in prefs if p.get("item") != value]
                prefs.append({"item": value, "category": "general", "strength": 0.8})
                await self._gs.update_person_field(person_id, "preferences", prefs)
                updates.append(f"preference+={value}")

            elif field == "aversion":
                # 0. 文本规范化
                value = self._normalize_value(value)

                # 0.5. 垃圾值检测
                if self._is_garbage_value(value):
                    updates.append(f"aversion.{value}=过滤（垃圾值）")
                    continue

                # 1. 矛盾检测：检查是否在 preferences 中
                prefs = node.get("preferences", []) or []
                if isinstance(prefs, str):
                    prefs = json.loads(prefs or "[]")
                pref_items = {p.get("item") for p in prefs}

                if value in pref_items:
                    # 移除旧的 preference，保留新的 aversion
                    prefs = [p for p in prefs if p.get("item") != value]
                    await self._gs.update_person_field(person_id, "preferences", prefs)
                    updates.append(f"preference-={value}（矛盾冲突）")

                aversions = node.get("aversions", []) or []
                if isinstance(aversions, str):
                    aversions = json.loads(aversions or "[]")
                aversions = [a for a in aversions if a.get("item") != value]
                aversions.append({"item": value, "category": "general", "strength": 0.8})
                await self._gs.update_person_field(person_id, "aversions", aversions)
                updates.append(f"aversion+={value}")

            elif field == "behavior":
                # 0. 文本规范化
                value = self._normalize_value(value)

                # 0.5. 垃圾值检测
                if self._is_garbage_value(value):
                    updates.append(f"behavior.{value}=过滤（垃圾值）")
                    continue

                # 1. 长度过滤：<2字 或 >15字
                if len(value) < 2 or len(value) > 15:
                    updates.append(f"behavior.{value}=过滤（长度）")
                    continue

                # 2. 语气词后缀过滤
                if value.endswith(("了", "呢", "吧", "啊", "哦", "的", "嗯")):
                    updates.append(f"behavior.{value}=过滤（语气词后缀）")
                    continue

                # 3. 黑名单过滤（复用 preference 的黑名单）
                EXCLUDE_BEHAVIORS = {
                    # 语气词
                    "还行吧", "嗯", "哦", "啊", "好", "是的", "对", "知道了",
                    "明白了", "好吧", "哇", "天哪", "太好了", "真的", "嗯嗯",
                    "哦哦", "啊啊", "好好", "是是", "对对", "知道", "明白",
                    "哦哦哦", "啊啊啊", "好好好", "是是是", "对对对",
                    "知道知道", "明白明白", "还不错",
                    "呃", "额", "嘛", "么", "呀呀", "啦啦",
                    # 单字动词
                    "吃", "玩", "看", "听", "做", "说", "想", "去", "来", "走",
                    # 家庭关系
                    "女朋友", "男朋友", "朋友", "妈妈", "爸爸", "哥哥", "姐姐",
                    "弟弟", "妹妹", "爷爷", "奶奶", "姥姥", "姥爷", "舅舅", "舅妈",
                    # 代词
                    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们",
                    "自己", "别人", "大家", "有人", "某人", "谁", "什么", "怎么",
                    "俺", "咱", "咱们",
                    # 无意义短语
                    "的人", "的人吧", "人", "东西", "事情", "这个", "那个",
                    # 调味料
                    "芥末", "蜂蜜", "醋", "蒜泥", "酱油", "辣椒油",
                    "番茄酱", "沙拉酱", "盐", "糖", "油", "酱", "汤", "汁", "料",
                }
                if value in EXCLUDE_BEHAVIORS:
                    updates.append(f"behavior.{value}=过滤（黑名单）")
                    continue

                behaviors = node.get("behaviors", []) or []
                if isinstance(behaviors, str):
                    behaviors = json.loads(behaviors or "[]")
                behaviors = [b for b in behaviors if b.get("pattern") != value]
                behaviors.append({"pattern": value, "frequency": "regular"})
                await self._gs.update_person_field(person_id, "behaviors", behaviors)
                updates.append(f"behavior+={value}")

            elif field == "personality":
                personality = node.get("personality", {}) or {}
                if not isinstance(personality, dict):
                    personality = {}
                personality[key] = value
                await self._gs.update_person_field(person_id, "personality", personality)
                updates.append(f"personality.{key}={value}")

            elif field == "relationship":
                # 关系声明 → 在 Relationship 表中记录
                from pipeline.relation_extractor import _is_valid_person_name
                if target == "self":
                    # "我最近谈了个女朋友" → 无具体人名，暂不处理
                    pass
                elif _is_valid_person_name(target):
                    sec_node = await self._gs.get_person_by_name(owner_id, target)
                    if not sec_node:
                        sec_node = await self._gs.upsert_person_node(owner_id, target, "secondary")
                    sec_pid = str(sec_node["person_id"])
                    rtype = _map_relation_value(value)
                    sentiment = 0.2 if rtype in ("friend", "family") else 0.1
                    await self._gs.upsert_relationship(
                        owner_id, person_id, sec_pid,
                        relation_type=rtype, sentiment_delta=sentiment,
                    )
                    updates.append(f"relationship+={target}({rtype})")

        return updates

    async def update_from_declaration(
        self, owner_id: str, primary_person_id: str, text: str,
    ) -> list[str]:
        """
        从属性声明更新 PersonNode（非事件的静态信息）。
        使用 LLM 替代正则提取。
        返回更新描述列表。
        """
        updates: list[str] = []

        # 检测是否在描述别人的属性（"小华喜欢画画" → 更新小华，不是主用户）
        other_pref = re.search(
            r'([\u4e00-\u9fff]{1,4})(?:说她?他?|说TA)?(?:也)?(?:喜欢|爱|爱吃|爱玩|喜欢吃|喜欢玩)'
            r'([^\s，。！？]{1,8})',
            text
        )
        if other_pref:
            person_name = other_pref.group(1).rstrip("说她他TA也")
            # 排除代词/非人名
            _NOT_PERSON = {"我", "自己", "本人", "咱", "他", "她", "它", "你",
                           "我们", "他们", "她们", "大家", "有人", "别人",
                           "对啊", "是的", "嗯嗯", "真的", "确实", "很",
                           "她更", "他更", "我不", "我最", "最"}
            if person_name and person_name not in _NOT_PERSON and len(person_name) >= 2:
                sec_updates = await self.update_secondary_from_text(
                    owner_id, person_name, text)
                if sec_updates:
                    return sec_updates

        # 获取当前节点
        node = await self._gs.get_person_node(primary_person_id)
        if not node:
            return updates
        identity = node["identity"] if isinstance(node["identity"], dict) else json.loads(node["identity"] or "{}")

        # 使用 LLM 提取身份属性
        llm_attrs = await extract_identity_attributes(text, identity, self._llm)

        # 更新 identity 字段
        for key in ["age", "name", "school", "grade", "birthday", "gender"]:
            if key in llm_attrs:
                identity[key] = llm_attrs[key]
                updates.append(f"identity.{key}={llm_attrs[key]}")

        if updates:
            await self._gs.update_person_field(primary_person_id, "identity", identity)

        # 使用 LLM 提取偏好/厌恶
        pref_result = await extract_preferences(text, self._llm)
        if isinstance(pref_result, list):
            pref_result = {"preferences": pref_result}

        # 更新 preferences
        if isinstance(pref_result, dict) and pref_result.get("preferences"):
            prefs = node["preferences"] if isinstance(node["preferences"], list) else json.loads(node["preferences"] or "[]")
            for p in pref_result["preferences"]:
                item = p.get("item")
                if not item or self._is_garbage_value(item):
                    continue
                # 去重
                prefs = [x for x in prefs if x.get("item") != item]
                prefs.append({
                    "item": item,
                    "category": p.get("category", "general"),
                    "strength": p.get("strength", 0.7)
                })
                updates.append(f"preference+={item}")
            await self._gs.update_person_field(primary_person_id, "preferences", prefs)

        # 更新 aversions
        if isinstance(pref_result, dict) and pref_result.get("aversions"):
            aversions = node["aversions"] if isinstance(node["aversions"], list) else json.loads(node["aversions"] or "[]")
            for a in pref_result["aversions"]:
                item = a.get("item")
                if not item or self._is_garbage_value(item):
                    continue
                # 去重
                aversions = [x for x in aversions if x.get("item") != item]
                aversions.append({
                    "item": item,
                    "category": a.get("category", "general"),
                    "strength": a.get("strength", 0.7)
                })
                updates.append(f"aversion+={item}")
            await self._gs.update_person_field(primary_person_id, "aversions", aversions)

        return updates

    async def update_from_event(
        self, owner_id: str, primary_person_id: str,
        event: dict, participant_ids: list[str],
    ) -> list[str]:
        """
        事件写入后更新 Profile：
        1. 关系 sentiment 变化
        2. 偏好变化检测
        3. 日级情绪聚合
        4. P0-3: 创伤标记
        """
        updates: list[str] = []

        event_type = event.get("event_type", "daily")
        emotion = event.get("emotion_summary", "平静")
        event_id = str(event.get("event_id", ""))
        event_time = event.get("event_time")
        summary = event.get("summary", "")

        # P0-3: 创伤事件标记
        is_trauma = _is_trauma_event(summary, emotion)
        if is_trauma:
            # 标记事件为创伤
            await self._gs.update_event_field(event_id, "trauma", True)
            await self._gs.update_event_field(event_id, "priority", "high")
            updates.append(f"trauma=True")

            # 添加到 current_focus，不会被挤出
            node = await self._gs.get_person_node(primary_person_id)
            if node:
                focus = node.get("current_focus", [])
                if isinstance(focus, str):
                    focus = json.loads(focus or "[]")

                # 创伤事件插入到最前面
                focus.insert(0, {
                    "topic": summary[:50],
                    "sentiment": "negative",
                    "trauma": True,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })

                # 保留最多 10 个 focus（创伤事件不计入限制）
                trauma_focus = [f for f in focus if f.get("trauma")]
                normal_focus = [f for f in focus if not f.get("trauma")][:7]
                focus = trauma_focus + normal_focus

                await self._gs.update_person_field(primary_person_id, "current_focus", focus)
                updates.append(f"focus+trauma")

        # 1. 关系 sentiment
        base_delta = _SENTIMENT_MAP.get(event_type, 0.0)
        emotion_delta = _EMOTION_SENTIMENT.get(emotion, 0.0)
        total_delta = round(base_delta + emotion_delta, 3)

        # 检测关系破裂关键词
        BREAKUP_KEYWORDS = {"绝交", "断绝", "不再联系", "拉黑", "删除", "分手", "离婚"}
        is_breakup = any(kw in summary for kw in BREAKUP_KEYWORDS)

        for pid in participant_ids:
            if pid != primary_person_id:
                # 如果是关系破裂，直接设置状态
                if is_breakup:
                    await self._gs.update_relationship_field(
                        owner_id, primary_person_id, pid, "state", "broken"
                    )
                    await self._gs.update_relationship_field(
                        owner_id, primary_person_id, pid, "sentiment", -1.0
                    )
                    updates.append(f"rel[{pid[:8]}] BROKEN")
                else:
                    # 正常更新 sentiment
                    await self._gs.upsert_relationship(
                        owner_id, primary_person_id, pid,
                        sentiment_delta=total_delta,
                        event_id=event_id,
                        event_time=event_time,
                    )
                    updates.append(f"rel[{pid[:8]}] sentiment{total_delta:+.2f}")

        # 2. 偏好变化
        source = event.get("source_text") or event.get("summary", "")
        from pipeline.meaning_extractor import extract_preference_change
        old_pref, new_pref = extract_preference_change(source)
        if new_pref:
            node = await self._gs.get_person_node(primary_person_id)
            if node:
                prefs = node["preferences"]
                if isinstance(prefs, str):
                    prefs = json.loads(prefs or "[]")
                # 覆盖
                prefs = [p for p in prefs if p.get("item") != new_pref.item]
                if new_pref.sentiment != "dislike":
                    prefs.append({
                        "item": new_pref.item,
                        "category": new_pref.category,
                        "strength": new_pref.strength,
                        "since_event_id": event_id,
                    })
                    updates.append(f"preference={new_pref.item}({new_pref.sentiment})")
                else:
                    # dislike → 从 preferences 移到 aversions
                    aversions = node["aversions"]
                    if isinstance(aversions, str):
                        aversions = json.loads(aversions or "[]")
                    aversions = [a for a in aversions if a.get("item") != new_pref.item]
                    aversions.append({
                        "item": new_pref.item,
                        "category": new_pref.category,
                        "strength": new_pref.strength,
                        "since_event_id": event_id,
                    })
                    await self._gs.update_person_field(primary_person_id, "aversions", aversions)
                    updates.append(f"aversion+={new_pref.item}")

                await self._gs.update_person_field(primary_person_id, "preferences", prefs)

        # 3. 日级情绪（增量更新，不是覆盖）
        if emotion and emotion != "平静":
            today = date.today()
            # 读取当天已有分布
            existing_emotions = await self._gs.get_recent_emotions(primary_person_id, days=0)
            today_record = None
            for rec in existing_emotions:
                if str(rec.get("date", "")) == str(today):
                    today_record = rec
                    break

            if today_record:
                dist = today_record.get("emotion_distribution", {})
                if isinstance(dist, str):
                    dist = json.loads(dist)
                dist[emotion] = dist.get(emotion, 0) + 1.0
            else:
                dist = {emotion: 1.0}

            # 重新计算 dominant（按出现次数）
            dominant = max(dist, key=dist.get) if dist else emotion

            await self._gs.upsert_daily_emotion(
                owner_id, primary_person_id, today,
                dist, dominant, event_count=1,
            )
            updates.append(f"emotion={emotion}")

        # 4. 事件计数
        await self._gs.increment_events(primary_person_id)
        for pid in participant_ids:
            if pid != primary_person_id:
                await self._gs.increment_events(pid)

        return updates

    async def update_secondary_from_text(
        self, owner_id: str, person_name: str, text: str,
    ) -> list[str]:
        """
        从用户描述更新 secondary 人物属性。
        例："小华喜欢画画" → 小华.preferences += 画画
        如果人物不存在则自动创建。
        """
        updates: list[str] = []
        from pipeline.relation_extractor import _is_valid_person_name
        if not _is_valid_person_name(person_name):
            return updates
        node = await self._gs.get_person_by_name(owner_id, person_name)
        if not node:
            # 自动创建 secondary PersonNode
            node = await self._gs.upsert_person_node(owner_id, person_name, "secondary")

        pid = str(node["person_id"])

        # preference
        m = _PREFERENCE_PAT.search(text)
        if m:
            item = m.group(1).strip()
            if item and item != person_name:
                prefs = node["preferences"]
                if isinstance(prefs, str):
                    prefs = json.loads(prefs or "[]")
                prefs = [p for p in prefs if p.get("item") != item]
                prefs.append({"item": item, "category": "general", "strength": 0.7})
                await self._gs.update_person_field(pid, "preferences", prefs)
                updates.append(f"{person_name}.preference+={item}")

        return updates
