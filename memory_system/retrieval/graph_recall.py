"""
retrieval/graph_recall.py — Person Graph 多维检索

三层检索策略：
  Layer 1: PersonNode 属性 + current_focus + 关系状态（~200 tokens，Redis 缓存）
           → 日常对话注入，0ms
  Layer 2: 按意图精确查询（人物/时间/类型过滤）
           → 提到具体人或事件时触发，<5ms
  Layer 3: 向量语义搜索（BGE-M3）
           → 模糊/开放性查询，<10ms

语言配置通过 config/i18n/{lang}.yaml 加载，支持多语言独立调优。

复用：
  - IntentClassifier 意图分类（retrieval/decider.py）
  - embed_text 向量编码（pipeline/embedding.py）
  - _compute_confidence 置信度计算（retrieval/recall.py）
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from storage.pg_store import GraphStore
from config.lang_config import get_lang_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 意图分类（从语言配置加载规则）
# ---------------------------------------------------------------------------

GraphIntent = Literal[
    "identity",       # 问身份
    "preference",     # 问喜好
    "person",         # 问某个人
    "event",          # 问事件
    "emotion",        # 问情绪
    "relationship",   # 问社交圈
    "general",        # 通用
]


def classify_intent(query: str) -> GraphIntent:
    """根据语言配置的正则规则分类意图"""
    cfg = get_lang_config()
    for intent_name in cfg.intent_order:
        patterns = cfg.intent_rules.get(intent_name, [])
        for pat in patterns:
            if pat.search(query):
                return intent_name
    return "general"


def extract_person_name(query: str) -> str | None:
    """从查询中提取人物名（根据语言配置）"""
    cfg = get_lang_config()
    pe = cfg.person_extraction

    if cfg.lang == "zh":
        return _extract_person_name_zh(query, pe)
    elif cfg.lang == "en":
        return _extract_person_name_en(query, pe)
    else:
        # 未知语言 fallback 到中文
        return _extract_person_name_zh(query, pe)


def _extract_person_name_zh(query: str, pe: dict) -> str | None:
    """中文人名提取"""
    # 家庭称谓
    for pattern in pe.get("family_titles", []):
        m = re.search(pattern, query)
        if m:
            return m.group(1) if m.lastindex else m.group(0)

    # 名字+职业
    nwr = pe.get("name_with_role", {})
    if nwr.get("pattern"):
        m = re.search(nwr["pattern"], query)
        if m:
            name = m.group(1)
            if name not in set(nwr.get("blacklist", [])):
                return name

    # 名字+关系动词
    nwv = pe.get("name_with_verb", {})
    if nwv.get("pattern"):
        m = re.search(nwv["pattern"], query)
        if m:
            name = m.group(1)
            if name not in set(nwv.get("blacklist", [])):
                return name

    # "小X" 模式
    xiao_pat = pe.get("xiao_pattern")
    if xiao_pat:
        m = re.search(xiao_pat, query)
        if m:
            return m.group(1) if m.lastindex else m.group(0)

    return None


def _extract_person_name_en(query: str, pe: dict) -> str | None:
    """英文人名提取"""
    # 家庭称谓
    for pattern in pe.get("family_titles", []):
        m = re.search(pattern, query, re.IGNORECASE)
        if m:
            return m.group(1).lower() if m.lastindex else m.group(0).lower()

    # 大写首字母人名 after preposition
    nap = pe.get("name_after_preposition", {})
    if nap.get("pattern"):
        stopwords = set(nap.get("stopwords", []))
        m = re.search(nap["pattern"], query)
        if m:
            name = m.group(1)
            if name not in stopwords:
                return name

    # Fallback: 非句首大写单词
    if pe.get("fallback_capitalized", False):
        stopwords = set(pe.get("name_after_preposition", {}).get("stopwords", []))
        words = query.split()
        for i, w in enumerate(words):
            if i > 0 and re.match(r'^[A-Z][a-z]{1,15}$', w) and w not in stopwords:
                return w

    return None


# ---------------------------------------------------------------------------
# Layer 1: Profile 摘要（注入 LLM 上下文）
# ---------------------------------------------------------------------------

async def build_profile_summary(
    gs: GraphStore, owner_id: str, primary_person_id: str,
) -> str:
    """
    构建 ~200 tokens 的人物摘要，用于注入 LLM system prompt。
    模板标签、分隔符等从语言配置读取。
    """
    cfg = get_lang_config()
    tpl = cfg.profile_template

    node = await gs.get_person_node(primary_person_id)
    if not node:
        return ""

    identity = node["identity"] if isinstance(node["identity"], dict) else json.loads(node["identity"] or "{}")
    prefs = node["preferences"] if isinstance(node["preferences"], list) else json.loads(node["preferences"] or "[]")
    aversions = node["aversions"] if isinstance(node["aversions"], list) else json.loads(node["aversions"] or "[]")
    behaviors = node["behaviors"] if isinstance(node["behaviors"], list) else json.loads(node["behaviors"] or "[]")
    focus = node["current_focus"] if isinstance(node["current_focus"], list) else json.loads(node["current_focus"] or "[]")
    name = identity.get("name") or node["name"]

    sep = tpl.get("separator", "、")
    sep_comma = tpl.get("separator_comma", "，")
    sent_icons = tpl.get("sentiment_icons", {"negative": "⚠", "positive": "✓", "neutral": "·"})
    sent_arrows = tpl.get("sentiment_arrows", {"positive": "↑", "negative": "↓", "neutral": "→"})

    lines = []

    # 基本信息
    info_parts = []
    if identity.get("name"):
        info_parts.append(f"{tpl.get('prefix_name', '姓名：')}{identity['name']}")
    if identity.get("age"):
        info_parts.append(f"{identity['age']}{tpl.get('prefix_age_suffix', '岁')}")
    if identity.get("gender"):
        gender_map = {"male": "男", "female": "女", "男": "男", "女": "女"}
        info_parts.append(f"性别{gender_map.get(identity['gender'], identity['gender'])}")
    if identity.get("school"):
        info_parts.append(identity["school"])
    if identity.get("grade"):
        info_parts.append(identity["grade"])
    if identity.get("birthday"):
        info_parts.append(f"{tpl.get('prefix_birthday', '生日')}{identity['birthday']}")
    if identity.get("zodiac"):
        info_parts.append(f"属{identity['zodiac']}")
    if identity.get("height"):
        info_parts.append(f"身高{identity['height']}")
    if identity.get("location"):
        info_parts.append(f"住{identity['location']}")
    if identity.get("job"):
        info_parts.append(f"{tpl.get('prefix_job', '职业：')}{identity['job']}")
    # 透传其余 identity 字段（班主任、家长姓名等自由扩展字段）
    _known_keys = {"name", "age", "gender", "school", "grade", "birthday",
                   "zodiac", "height", "location", "job"}
    for k, v in identity.items():
        if k in _known_keys or v is None or v == "":
            continue
        info_parts.append(f"{k}：{v}")
    if info_parts:
        lines.append(f"{tpl.get('section_basic', '**基本信息**')}：{sep_comma.join(info_parts)}")

    # 喜好
    if prefs:
        pref_items = [p.get("item", "") for p in prefs if p.get("item")]
        if pref_items:
            if len(pref_items) > 10:
                overflow = tpl.get("likes_overflow", "...（共{count}项）").format(count=len(pref_items))
                lines.append(f"{tpl.get('section_likes', '**喜好**')}：{sep.join(pref_items[:10])}{overflow}")
            else:
                lines.append(f"{tpl.get('section_likes', '**喜好**')}：{sep.join(pref_items)}")

    # 厌恶
    if aversions:
        aver_items = [a.get("item", "") for a in aversions if a.get("item")]
        if aver_items:
            lines.append(f"{tpl.get('section_dislikes', '**注意避免**')}：{sep.join(aver_items[:3])}")

    # 习惯
    if behaviors:
        beh_items = [b.get("pattern", "") for b in behaviors if b.get("pattern")]
        if beh_items:
            lines.append(f"{tpl.get('section_habits', '**习惯**')}：{sep.join(beh_items[:3])}")

    # 近期关注
    if focus:
        focus_parts = []
        for f in focus[:3]:
            topic = f.get("topic", "")
            sent = f.get("sentiment", "neutral")
            freq = f.get("frequency", 1)
            icon = sent_icons.get(sent, "·")
            focus_parts.append(f"{icon}{topic}(x{freq})")
        if focus_parts:
            lines.append(f"{tpl.get('section_focus', '**近期关注**')}：{sep_comma.join(focus_parts)}")

    # 社交圈
    rels = await gs.get_relationships(owner_id)
    # 收集已通过 relationship 输出的 person_id，避免下面"其他成员"重复
    _rel_person_ids: set[str] = set()
    if rels:
        # 关系数量聚合：回答"我有几个朋友/家人"这类问题（按全部关系统计，不受下面 top-5 展示截断影响）
        from collections import Counter as _Counter
        _rt_zh = {"friend": "朋友", "family": "家人", "classmate": "同学", "teacher": "老师",
                  "colleague": "同事", "partner": "伴侣", "other": "其他"}
        _counts = _Counter((r.get("relation_type") or "other") for r in rels)
        _cparts = [f"{_rt_zh.get(k, k)}{v}{tpl.get('count_unit', '个')}" for k, v in _counts.most_common()]
        lines.append(f"{tpl.get('section_social_count', '**关系数量**')}：{sep_comma.join(_cparts)}（共{len(rels)}）")
        lines.append(f"{tpl.get('section_social', '**社交圈**')}：")
        for r in rels[:5]:
            to_name = r.get("to_name", "?")
            rtype = r.get("relation_type", "other")
            _rel_person_ids.add(str(r.get("to_person_id", "")))
            sentiment = float(r.get("sentiment", 0))
            arrow = sent_arrows.get(
                "positive" if sentiment > 0.3 else ("negative" if sentiment < -0.3 else "neutral"),
                "→"
            )
            # 查最近事件
            last_event = ""
            if r.get("last_event_id"):
                events = await gs.query_events(
                    owner_id, participant_id=r.get("to_person_id"),
                    limit=1)
                if events:
                    last_event = f"{tpl.get('social_recent', ' 最近：')}{events[0]['summary'][:15]}"
            # 查此人喜好
            to_node = await gs.get_person_node(str(r.get("to_person_id", "")))
            to_prefs = ""
            to_attrs = ""
            if to_node:
                tp = to_node["preferences"]
                if isinstance(tp, str):
                    tp = json.loads(tp or "[]")
                if tp:
                    to_prefs = f"{tpl.get('social_likes', ' | 喜欢：')}{sep.join(p.get('item','') for p in tp[:2])}"
                # 输出对方 identity 关键字段（occupation/age/school/location 等）
                tid = to_node.get("identity")
                if isinstance(tid, str):
                    try:
                        tid = json.loads(tid or "{}")
                    except Exception:
                        tid = {}
                if isinstance(tid, dict) and tid:
                    attr_parts = []
                    if tid.get("age"):
                        attr_parts.append(f"{tid['age']}{tpl.get('prefix_age_suffix','岁')}")
                    if tid.get("occupation"):
                        attr_parts.append(tid["occupation"])
                    if tid.get("job"):
                        attr_parts.append(tid["job"])
                    if tid.get("school"):
                        attr_parts.append(tid["school"])
                    if tid.get("workplace"):
                        attr_parts.append(tid["workplace"])
                    if tid.get("location"):
                        attr_parts.append(f"住{tid['location']}")
                    # 透传其余非 name 字段
                    _seen = {"name", "age", "occupation", "job", "school", "workplace", "location"}
                    for k, v in tid.items():
                        if k in _seen or v is None or v == "":
                            continue
                        attr_parts.append(f"{k}：{v}")
                    if attr_parts:
                        to_attrs = f" | {sep_comma.join(attr_parts)}"

            lines.append(f"  {to_name}（{rtype} {arrow}{sentiment:.1f}）{last_event}{to_attrs}{to_prefs}")

    # 其他相关人员（已被抽出 person_node 但还未建 relationship 的，例如"我爸爸叫张伟"
    # 仅产生 secondary person + identity 而无 relationship 时）
    try:
        all_nodes = await gs.list_person_nodes(owner_id)
    except Exception:
        all_nodes = []
    extras = []
    for n in all_nodes or []:
        if n.get("role") == "primary":
            continue
        pid = str(n.get("person_id", ""))
        if pid in _rel_person_ids:
            continue
        nm = n.get("name") or ""
        if not nm:
            continue
        nid = n.get("identity")
        if isinstance(nid, str):
            try:
                nid = json.loads(nid or "{}")
            except Exception:
                nid = {}
        nid = nid if isinstance(nid, dict) else {}
        # 跳过没任何信息的"光杆"node（如"妹妹特别"这种 garbage）
        if not nid:
            continue
        attr_parts = []
        if nid.get("age"):
            attr_parts.append(f"{nid['age']}{tpl.get('prefix_age_suffix','岁')}")
        for k_pref in ("occupation", "job", "school", "workplace"):
            if nid.get(k_pref):
                attr_parts.append(nid[k_pref])
        if nid.get("location"):
            attr_parts.append(f"住{nid['location']}")
        _seen = {"name", "age", "occupation", "job", "school", "workplace", "location"}
        for k, v in nid.items():
            if k in _seen or v is None or v == "":
                continue
            attr_parts.append(f"{k}：{v}")
        if attr_parts:
            extras.append(f"  {nm}（{sep_comma.join(attr_parts)}）")
    if extras:
        lines.append(f"{tpl.get('section_others', '**其他相关人员**')}：")
        lines.extend(extras[:8])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer 2: 精确查询
# ---------------------------------------------------------------------------

async def query_by_person(
    gs: GraphStore, owner_id: str, person_name: str, limit: int = 10,
) -> list[dict]:
    """查某个人相关的事件（带归一化）"""
    from pipeline.person_normalizer import normalize_person_name
    canonical = normalize_person_name(person_name)
    node = await gs.get_person_by_name(owner_id, canonical)
    if not node and canonical != person_name:
        node = await gs.get_person_by_name(owner_id, person_name)
    if not node:
        return []
    return await gs.query_events(
        owner_id, participant_id=str(node["person_id"]), limit=limit)


async def query_by_time(
    gs: GraphStore, owner_id: str, days: int = 7, limit: int = 20,
) -> list[dict]:
    """查最近 N 天的事件"""
    time_from = datetime.now(timezone.utc) - timedelta(days=days)
    return await gs.query_events(owner_id, time_from=time_from, limit=limit)


async def query_by_type(
    gs: GraphStore, owner_id: str, event_type: str, limit: int = 10,
) -> list[dict]:
    """按事件类型查"""
    return await gs.query_events(owner_id, event_type=event_type, limit=limit)


# ---------------------------------------------------------------------------
# Layer 3: 向量语义搜索
# ---------------------------------------------------------------------------

async def vector_search(
    gs: GraphStore, owner_id: str, query: str, limit: int = 10,
    precomputed_embedding: list[float] | None = None,
) -> list[dict]:
    """BGE-M3 向量搜索事件（按时间倒序，过滤低相关性）"""
    if precomputed_embedding:
        emb = precomputed_embedding
    else:
        from pipeline.embedding import embed_text
        emb = await embed_text(query)
    if not emb:
        return []
    results = await gs.vector_search_events(owner_id, emb, limit=limit * 2)

    # 相关性过滤：similarity < 0.2 的直接丢弃
    filtered = []
    for r in results:
        sim = float(r.get("similarity", 0))
        if sim >= 0.2:  # 只保留相似度 >= 0.2 的（支持跨语言召回）
            r["final_score"] = float(r.get("importance", 0.5))
            r["_relevance"] = sim  # 语义相关性分数，供衰减缓存使用
            filtered.append(r)

    # 保持时间倒序（数据库已排序），直接截取
    return filtered[:limit]


# ---------------------------------------------------------------------------
# Event 意图时间解析（多语言）
# ---------------------------------------------------------------------------

# 时间范围正则：每种语言独立配置
_TIME_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "zh": [
        (r'最近|近来', 7),
        (r'上周|上个星期', 14),
        (r'昨天', 1),
        (r'今天', 0),
    ],
    "en": [
        (r'recently|recent', 7),
        (r'last week', 14),
        (r'last month', 30),
        (r'yesterday', 1),
        (r'today', 0),
    ],
}


def _parse_time_range(query: str) -> int:
    """从 query 解析时间范围（天数），未匹配返回 30"""
    cfg = get_lang_config()
    patterns = _TIME_PATTERNS.get(cfg.lang, _TIME_PATTERNS["zh"])
    for pat_str, days in patterns:
        if re.search(pat_str, query, re.IGNORECASE):
            return days
    return 30  # 默认查最近 30 天


# ---------------------------------------------------------------------------
# 主检索入口
# ---------------------------------------------------------------------------

@dataclass
class GraphRecallResult:
    """检索结果"""
    profile_summary: str = ""           # Layer 1 摘要
    events: list[dict] = field(default_factory=list)  # 事件列表
    session_context: list[dict] = field(default_factory=list)  # L0.5 当前会话最近 N 轮
    intent: str = "general"
    person_name: str | None = None      # 查询涉及的人名
    confidence: str = "empty"           # high/uncertain/low/empty
    latency_ms: float = 0.0
    source: str = ""                    # "profile" / "person" / "event" / "vector"


async def graph_recall(
    gs: GraphStore, owner_id: str, primary_person_id: str,
    query: str,
    redis=None,
    session_id: str = "",
) -> GraphRecallResult:
    """
    Person Graph 统一检索入口。

    1. 始终构建 Profile 摘要（Layer 1）
    2. 根据意图分类选择精确查询路径（Layer 2）
    3. 兜底向量搜索（Layer 3）
    """
    t0 = time.perf_counter()

    # L0.5：当前会话最近 8 轮即时上下文
    session_context = []
    if redis and session_id:
        try:
            session_context = await redis.get_recent_turns(session_id, n=8)
        except Exception as e:
            logger.warning(f"graph_recall: failed to get session context: {e}")

    intent = classify_intent(query)
    person_name = extract_person_name(query)

    # 并行执行 profile 构建和 embedding
    # identity/preference/general 都可能需要向量搜索兜底，提前计算 embedding
    if intent in ("general", "identity", "preference"):
        from pipeline.embedding import embed_text
        import asyncio
        profile_task = build_profile_summary(gs, owner_id, primary_person_id)
        embed_task = embed_text(query)
        profile_summary, query_embedding = await asyncio.gather(profile_task, embed_task)
    else:
        profile_summary = await build_profile_summary(gs, owner_id, primary_person_id)
        query_embedding = None

    events: list[dict] = []
    source = "profile"
    confidence = "high" if profile_summary else "empty"

    # Layer 2: 按意图精确查询
    if intent == "identity":
        # 身份信息已在 profile_summary 中，无需额外查事件
        source = "profile"

    elif intent == "preference":
        # 偏好已在 profile_summary 中
        source = "profile"

    elif intent == "person" and person_name:
        events = await query_by_person(gs, owner_id, person_name, limit=5)
        source = "person"
        confidence = "high" if events else "low"

    elif intent == "event":
        days = _parse_time_range(query)
        events = await query_by_time(gs, owner_id, days=days, limit=10)
        source = "event"
        confidence = "high" if events else "low"

    elif intent == "emotion":
        # 查最近情绪相关事件
        events = await query_by_type(gs, owner_id, "emotional", limit=5)
        # 也查冲突（影响情绪）
        conflicts = await query_by_type(gs, owner_id, "conflict", limit=3)
        events = (events + conflicts)[:8]
        source = "emotion"
        confidence = "high" if events else "uncertain"

    elif intent == "relationship":
        # 关系信息已在 profile_summary 中，补充社交事件
        events = await query_by_type(gs, owner_id, "social", limit=5)
        source = "relationship"

    elif intent == "general":
        # 兜底：向量搜索（使用预计算的 embedding）
        events = await vector_search(
            gs, owner_id, query, limit=5,
            precomputed_embedding=query_embedding,
        )
        source = "vector"
        confidence = "high" if events else "uncertain"

    # 如果意图不是 person 但提到了人名，补充查此人
    if not events and person_name and intent != "person":
        extra = await query_by_person(gs, owner_id, person_name, limit=3)
        if extra:
            events = extra
            source = "person"
            confidence = "high"

    # --- 关键兜底：精确查询无结果时 fallback 到向量搜索 ---
    # identity/preference 也需要兜底：用户问偏好时，相关 events（活动、计划）也应召回
    if not events and intent not in ("general",):
        from pipeline.embedding import embed_text
        if not query_embedding:
            query_embedding = await embed_text(query)
        fallback_events = await vector_search(
            gs, owner_id, query, limit=5,
            precomputed_embedding=query_embedding,
        )
        if fallback_events:
            events = fallback_events
            source = "vector"
            confidence = "high" if events else "uncertain"

    latency = (time.perf_counter() - t0) * 1000

    # 为精确查询（非向量搜索）的事件按排名赋 _relevance
    # 向量搜索的事件已经在 vector_search() 中标注了 _relevance = similarity
    for i, e in enumerate(events):
        if "_relevance" not in e:
            # 精确查询：按排名递减，第1条=1.0，每条-0.08，最低0.4
            e["_relevance"] = round(max(1.0 - i * 0.08, 0.4), 2)

    return GraphRecallResult(
        profile_summary=profile_summary,
        events=events,
        session_context=session_context,
        intent=intent,
        person_name=person_name,
        confidence=confidence,
        latency_ms=latency,
        source=source,
    )
