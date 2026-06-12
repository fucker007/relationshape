"""
pipeline/event_extractor.py — Phase 2: 规则驱动的事件提取器

从自然语言文本中提取结构化 EventModel：
  人 + 时间 + 地点 + 事件 + 参与者 + 情绪

支持：
  - 多人参与者识别（和A、B、C一起...）
  - 时间模糊标准化（昨天/上周/很久以前 → 标准格式）
  - 地点提取（公园/学校/家里...）
  - 情绪识别（开心/难过/兴奋...）
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from models.event import EventModel


# ---------------------------------------------------------------------------
# 时间标准化
# ---------------------------------------------------------------------------

# 参考日期（改为动态获取当天日期，不再硬编码）
_TODAY = date.today()

_TIME_PATTERNS: list[tuple[re.Pattern, str | None]] = [
    # ── 精确相对时间 ──
    (re.compile(r'今天|今日'),                          None),   # → 今天实际日期
    (re.compile(r'昨天|昨日'),                          None),
    (re.compile(r'前天'),                               None),
    (re.compile(r'大前天'),                             None),
    # N天前
    (re.compile(r'(\d+)天前'),                          None),
    # N周前 / 上周 / 上个星期
    (re.compile(r'上周|上个星期|上星期'),                None),
    (re.compile(r'(\d+)周前|(\d+)个星期前'),            None),
    # N月前 / 上个月
    (re.compile(r'上个月|上月'),                        None),
    (re.compile(r'(\d+)个月前'),                        None),
    # 去年
    (re.compile(r'去年'),                               None),
    # 固定日期 YYYY-MM-DD / M月D日
    (re.compile(r'(\d{4})[年\-](\d{1,2})[月\-](\d{1,2})[日号]?'), None),
    (re.compile(r'(\d{1,2})月(\d{1,2})[日号]'),        None),
    # 模糊时间
    (re.compile(r'很久以前|很久之前|以前|从前|小时候'),  "过去"),
    (re.compile(r'最近|近来|近期'),                      "最近"),
    (re.compile(r'明天|明日'),                           "明天"),
    (re.compile(r'下周|下个星期'),                       "下周"),
]

# 早上/下午/晚上等修饰（不作为主时间）
_TIME_OF_DAY = re.compile(r'(早上|上午|中午|下午|傍晚|晚上|夜里|凌晨)')

# 星期
_WEEKDAY_PAT = re.compile(r'(上周|这周|本周|上个星期)?(星期|周)([一二三四五六日天])')
_WD_MAP = {'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}


def _normalize_time(text: str, ref: date = _TODAY) -> tuple[str | None, str | None, bool]:
    """
    返回 (when_normalized_str, when_raw_str, normalized_flag)
    """
    # 精确日期 YYYY-MM-DD
    m = re.search(r'(\d{4})[年\-](\d{1,2})[月\-](\d{1,2})[日号]?', text)
    if m:
        raw = m.group(0)
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", raw, True

    # M月D日
    m = re.search(r'(\d{1,2})月(\d{1,2})[日号]', text)
    if m:
        raw = m.group(0)
        return f"{ref.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}", raw, True

    # 星期
    m = _WEEKDAY_PAT.search(text)
    if m:
        prefix = m.group(1) or ""
        wd = _WD_MAP.get(m.group(3), 0)
        raw = m.group(0)
        # 计算目标日期
        today_wd = ref.weekday()
        delta = wd - today_wd
        if "上" in prefix or delta >= 0:
            delta -= 7
        target = ref + timedelta(days=delta)
        return target.isoformat(), raw, True

    # N天前
    m = re.search(r'(\d+)天前', text)
    if m:
        raw = m.group(0)
        return (ref - timedelta(days=int(m.group(1)))).isoformat(), raw, True

    # N周前 / 上周
    m = re.search(r'(\d+)周前|(\d+)个星期前', text)
    if m:
        n = int(m.group(1) or m.group(2))
        raw = m.group(0)
        return (ref - timedelta(weeks=n)).isoformat(), raw, True
    if re.search(r'上周|上个星期|上星期', text):
        raw = re.search(r'上周|上个星期|上星期', text).group(0)
        return (ref - timedelta(weeks=1)).isoformat(), raw, True

    # 今天
    if re.search(r'今天|今日', text):
        return ref.isoformat(), "今天", True
    # 昨天
    if re.search(r'昨天|昨日', text):
        return (ref - timedelta(days=1)).isoformat(), "昨天", True
    # 前天
    if re.search(r'前天', text):
        return (ref - timedelta(days=2)).isoformat(), "前天", True
    # 大前天
    if re.search(r'大前天', text):
        return (ref - timedelta(days=3)).isoformat(), "大前天", True

    # N个月前 / 上个月
    m = re.search(r'(\d+)个月前', text)
    if m:
        n = int(m.group(1))
        raw = m.group(0)
        # 粗略：每月30天
        return (ref - timedelta(days=n * 30)).isoformat(), raw, True
    if re.search(r'上个月|上月', text):
        return (ref - timedelta(days=30)).isoformat(), "上个月", True

    # 去年
    if re.search(r'去年', text):
        return f"{ref.year - 1}", "去年", True

    # 明天
    if re.search(r'明天|明日', text):
        return (ref + timedelta(days=1)).isoformat(), "明天", True
    # 下周
    if re.search(r'下周|下个星期', text):
        return (ref + timedelta(weeks=1)).isoformat(), "下周", True

    # 模糊：很久以前
    if re.search(r'很久以前|很久之前|以前|从前|小时候', text):
        raw_m = re.search(r'很久以前|很久之前|以前|从前|小时候', text)
        return "过去", raw_m.group(0) if raw_m else "以前", False

    # 最近
    if re.search(r'最近|近来|近期', text):
        return "最近", "最近", False

    # 兜底：无时间表达 → 默认"最近"（合理推断）
    return "最近", None, False


# ---------------------------------------------------------------------------
# 地点提取
# ---------------------------------------------------------------------------

_PLACE_KEYWORDS = [
    "公园", "游乐场", "图书馆", "游泳池", "博物馆", "动物园", "水族馆",
    "电影院", "操场", "海边", "山上", "山里", "奶奶家", "外婆家", "爷爷家",
    "姥姥家", "学校", "教室", "餐厅", "超市", "商场", "医院", "家里",
    "家中", "球场", "体育馆", "音乐厅", "剧场", "街上", "路上",
    "广场", "小区", "社区", "活动室", "图书室", "礼堂", "食堂",
]

_PLACE_PAT = re.compile(
    r'(?:在|去|到|来到|前往|出发去|跑去|回到)([^\s，。！？,]{1,10}?)(?:玩|游|逛|参观|看|待|住|学|练|跑|比赛|表演)',
)
_PLACE_PAT2 = re.compile(
    r'([^\s，。！？,]{1,8}(?:里|上|边|旁|处|馆|场|院|室|堂|厅|园|店|家))(?=[，。！？\s]|和|与)',
)


def _extract_place(text: str) -> str | None:
    # 先从关键词列表匹配
    for kw in _PLACE_KEYWORDS:
        if kw in text:
            return kw
    # 再用 pattern
    m = _PLACE_PAT.search(text)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# 参与者提取
# ---------------------------------------------------------------------------

_PERSON_NAME_PAT = re.compile(
    r'[和与跟同]([^，。！？,\s]{1,4}?)(?=[，。！？\s]|一起|一块|同行|玩|去|聊|做|学|和|以及|还有|、)',
)

# 「A、B和C一起」格式（支持"还有我/大家"等在一起之前的修饰）
_MULTI_PERSON_PAT = re.compile(
    r'[和与跟同]?([^\s，。！？,和与跟同]{1,4}(?:[、，,][\s]*[^\s，。！？,和与跟同]{1,4})+)'
    r'(?:[和与]([^\s，。！？,还]{1,4}))?'           # 可选：和X
    r'(?:还有我|还有大家|大家|我们|，我们)?'          # 可选：还有我/大家
    r'(?:一起|一块|同行)',
)

# 排除词（避免误识别）
_PERSON_EXCLUDES = frozenset([
    "一起", "一块", "同行", "大家", "他们", "她们", "我们", "小朋友",
    "老师", "妈妈", "爸爸", "家长", "同学", "朋友", "伙伴",
    "的", "了", "着", "过", "是", "在", "到",
])


def _extract_participants(text: str) -> list[str]:
    """提取参与者列表（排除主体'我'）"""
    participants: list[str] = []

    # 优先匹配多人格式：「和A、B、C一起」
    m = _MULTI_PERSON_PAT.search(text)
    if m:
        # 拆分 group(1): "明明、东东" → ["明明", "东东"]
        group1 = m.group(1)
        parts = re.split(r'[、，,]\s*', group1)
        for p in parts:
            p = p.strip()
            if p and p not in _PERSON_EXCLUDES and len(p) <= 4:
                participants.append(p)
        if m.group(2):
            extra = m.group(2).strip()
            if extra and extra not in _PERSON_EXCLUDES:
                participants.append(extra)

    if not participants:
        # 单人格式：「和xxx一起」/ 「跟xxx玩」
        for m in _PERSON_NAME_PAT.finditer(text):
            name = m.group(1).strip()
            if name and name not in _PERSON_EXCLUDES and len(name) <= 4:
                participants.append(name)

    # 去重，保持顺序
    seen = set()
    result = []
    for p in participants:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


# ---------------------------------------------------------------------------
# 情绪提取
# ---------------------------------------------------------------------------

_EMOTION_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r'开心|高兴|快乐|乐|喜悦|兴奋|激动|欢喜|欢乐|棒|美好|满足|感动'), "开心"),
    (re.compile(r'难过|伤心|哭|委屈|痛苦|悲伤|低落|沮丧|失落'), "难过"),
    (re.compile(r'害怕|恐惧|担心|担忧|恐慌|紧张|焦虑'), "害怕"),
    (re.compile(r'生气|愤怒|不满|烦|气'), "生气"),
    (re.compile(r'平静|普通|一般|还好|无聊|没意思'), "平静"),
    (re.compile(r'惊讶|惊喜|吃惊|没想到'), "惊喜"),
]


def _extract_emotion(text: str) -> str | None:
    for pat, emotion in _EMOTION_MAP:
        if pat.search(text):
            return emotion
    return None


# ---------------------------------------------------------------------------
# 事件核心动作提取
# ---------------------------------------------------------------------------

_WHAT_PATTERNS: list[re.Pattern] = [
    # 「和xxx一起去/玩/做...」
    re.compile(r'[和与跟同].{0,10}?(?:一起|一块)(.{2,20}?)(?=[，。！？\s]|$)'),
    # 「去xxx了」/ 「去xxx玩」
    re.compile(r'去(.{1,10}?(?:玩|逛|参观|看|学|练|比赛|游|游泳|表演))'),
    # 「做了/完成了/参加了...」
    re.compile(r'(?:做了|完成了|参加了|举行了|上了|学了|看了|去了)(.{1,15}?)(?=[，。！？\s]|$)'),
    # 「和xxx ...动词 + 宾语」
    re.compile(r'[和与跟同].{1,6}?(?:一起)?(.{2,20}?[了过])(?=[，。！？\s]|$)'),
]


def _extract_what(text: str) -> str | None:
    for pat in _WHAT_PATTERNS:
        m = pat.search(text)
        if m:
            what = m.group(1).strip().rstrip('，。！？')
            if len(what) >= 2:
                return what
    # 最后兜底：取去掉时间地点后的核心短语
    cleaned = re.sub(r'(昨天|今天|上周|最近|[^\s]+[里上边])', '', text).strip()
    if len(cleaned) >= 4:
        return cleaned[:25]
    return text[:25] if text else None


# ---------------------------------------------------------------------------
# 主提取函数
# ---------------------------------------------------------------------------

def extract_event(text: str, who: str = "用户",
                  ref_date: date = _TODAY) -> EventModel:
    """
    从自然语言文本中提取 EventModel。

    Args:
        text:     原始用户话语
        who:      主体（默认"用户"，可以传入已知的人物名）
        ref_date: 参考日期（用于时间标准化）

    Returns:
        EventModel（当信息缺失时对应字段为 None）
    """
    # 1. 时间
    when, when_raw, normalized = _normalize_time(text, ref=ref_date)

    # 2. 地点
    where = _extract_place(text)

    # 3. 参与者
    participants = _extract_participants(text)

    # 4. 情绪
    emotion = _extract_emotion(text)

    # 5. 事件核心（what）
    what = _extract_what(text)
    if not what:
        what = text[:30]

    return EventModel(
        who=who,
        when=when,
        when_raw=when_raw,
        when_normalized=normalized,
        where=where,
        what=what,
        participants=participants,
        emotion=emotion,
    )


def extract_events_batch(texts: list[str], who: str = "用户",
                         ref_date: date = _TODAY) -> list[EventModel]:
    """批量提取"""
    return [extract_event(t, who=who, ref_date=ref_date) for t in texts]


# ---------------------------------------------------------------------------
# Person Graph 扩展：EventModel → Graph Event dict
# ---------------------------------------------------------------------------

# 事件类型推断规则
_EVENT_TYPE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'吵架|打架|骂|推|抢|闹矛盾|嘲笑|欺负|打了|冲突'), "conflict"),
    (re.compile(r'考了|考试|比赛|竞赛|得[了奖]|一等奖|第一|获奖|满分|冠军'), "achievement"),
    (re.compile(r'难过|伤心|哭|害怕|焦虑|孤独|委屈|生气|愤怒|痛苦|绝望|低落'), "emotional"),
    (re.compile(r'开始|不再|戒掉|改了|变了|从今|以后|决定'), "change"),
    (re.compile(r'一起|玩|聊天|吃饭|散步|打球|出去|逛|唱歌|跳舞'), "social"),
]


def _infer_event_type(text: str, emotion: str | None) -> str:
    for pat, etype in _EVENT_TYPE_RULES:
        if pat.search(text):
            return etype
    if emotion and emotion in ("难过", "害怕", "生气", "委屈", "焦虑", "孤独", "绝望"):
        return "emotional"
    return "daily"


def is_event(text: str) -> bool:
    """
    判断一段文字是否描述了事件（至少满足 2 项）：
    1. 有时间指向
    2. 有具体动作/状态变化
    3. 有参与者（除自己外）或提到其他人名
    4. 有情绪影响

    不是事件的例子："我叫小明" / "嗯嗯好的" / "我喜欢吃辣"
    """
    score = 0
    # 有时间
    when, _, normalized = _normalize_time(text)
    if when and when != "最近":
        score += 1
    # 有参与者（放宽：也检测 "和X" 模式）
    participants = _extract_participants(text)
    if participants:
        score += 1
    elif re.search(r'[和跟同与][\u4e00-\u9fff]{1,4}', text):
        score += 1  # "和小华" 即使 participant 提取没命中
    # 有动作词
    if re.search(
        r'了$|了[，。！？\s]|一起|去了|做了|参加|学会|完成|发生|开始|改了|'
        r'打了|打篮球|打球|踢球|吵架|考试|吃饭|写作业|看了|玩了|'
        r'被.{0,4}(?:骂|批评|表扬|打|推|嘲笑)|'
        r'得了|拿了|赢了|输了|哭了|笑了|'
        r'考了|比赛|竞赛|获奖|得奖|一等奖|满分|第一名',
        text
    ):
        score += 1
    # 有情绪
    if _extract_emotion(text):
        score += 1
    # 成就/冲突关键词自带"事件性"（即使没有时间/参与者）
    if re.search(r'100分|满分|第一名|一等奖|冠军|获奖|考砸|考差|不及格|被骂|被批评', text):
        score += 1
    return score >= 2


def extract_graph_event(
    text: str,
    who: str = "用户",
    ref_date: date | None = None,
) -> dict | None:
    """
    从文本提取 Graph Event dict，用于写入 events 表。

    复用所有 Phase 2 规则（时间/人物/地点/情绪），扩展：
    - 强制绝对时间（"最近"/"过去" 时用当天兜底）
    - 事件类型推断
    - 意义层提取（情绪/信念/重要性）

    返回 None 表示此文本不构成事件。
    """
    if ref_date is None:
        ref_date = date.today()

    # 先判断是否是事件
    if not is_event(text):
        return None

    # 复用 Phase 2 提取
    event_model = extract_event(text, who=who, ref_date=ref_date)

    # 强制绝对时间
    from datetime import datetime as dt, timezone as tz
    when_str = event_model.when
    time_approximate = False  # 时间是否为推断（非明确表达）

    if when_str and when_str not in ("最近", "过去"):
        try:
            if len(when_str) == 10:  # YYYY-MM-DD
                event_time = dt.strptime(when_str, "%Y-%m-%d").replace(
                    hour=12, tzinfo=tz.utc)
            elif len(when_str) == 4:  # YYYY (去年)
                event_time = dt(int(when_str), 6, 1, tzinfo=tz.utc)
                time_approximate = True
            else:
                event_time = dt.now(tz.utc)
                time_approximate = True
        except ValueError:
            event_time = dt.now(tz.utc)
            time_approximate = True
    else:
        # "最近"/"过去"/None → 兜底当天，但标记为近似
        event_time = dt.now(tz.utc)
        time_approximate = True

    # 事件类型推断
    event_type = _infer_event_type(text, event_model.emotion)

    # 意义层提取（复用 Phase 3）
    from pipeline.meaning_extractor import extract_meaning
    meaning = extract_meaning(text)

    return {
        "event_time": event_time,
        "event_time_raw": event_model.when_raw,
        "time_approximate": time_approximate,  # True=推断的(无明确时间词), False=确定的
        "event_type": event_type,
        "summary": event_model.what or text[:50],
        "participant_names": event_model.participants,
        "scene": event_model.where,
        "emotion_summary": meaning.emotional_impact,
        "emotions_tags": meaning.emotional_tags,
        "importance": meaning.importance,
        "belief_impact": meaning.belief_type,
        "impact": [],  # 由 LLM 或后续规则补充
        "source_text": text,
    }

