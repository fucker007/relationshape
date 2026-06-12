"""
pipeline/meaning_extractor.py — Phase 3: 意义提取 + 偏好变化检测

extract_meaning(text)            → MeaningLayer
extract_preference_change(text)  → (old_state | None, new_state | None)
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from models.meaning import MeaningLayer, PreferenceState, Sentiment

# ---------------------------------------------------------------------------
# 否定检测
# ---------------------------------------------------------------------------

_DOUBLE_NEGATION = re.compile(
    r'(?:不是不|没有不|并不是不|并没有不|不得不|不可不|不能不)'
)

_NEGATION_WORDS = re.compile(
    r'(?:不|没有?|并不|根本不|完全不|一点也不|毫不|毫无|未|从未|从不)'
    r'(?:感到|觉得|觉|感觉|算是?|叫做?|是|算)?'
    r'\s*'
)

# 强排除："没想到" 不是否定情绪词的修饰
_NEGATION_EXCLUDES = re.compile(r'没想到|没料到|没预料')


def _has_negation(text: str, match_span: tuple[int, int]) -> bool:
    start = match_span[0]
    prefix = text[max(0, start - 10): start]
    if _DOUBLE_NEGATION.search(prefix):
        return False
    # 排除 "没想到" 这类非否定用法
    if _NEGATION_EXCLUDES.search(prefix):
        return False
    return bool(_NEGATION_WORDS.search(prefix))


# ---------------------------------------------------------------------------
# 转折词识别
# ---------------------------------------------------------------------------

_CONTRAST_PAT = re.compile(
    r'[，,]?\s*(?:但是?|不过|可是|然而|虽然[^，。！？,]*?但|只是(?!有)|就是(?!说))'
)

# ---------------------------------------------------------------------------
# 情绪规则（顺序重要：特殊 > 通用）
# ---------------------------------------------------------------------------

_EMOTION_RULES: list[tuple[re.Pattern, str, list[str], float]] = [
    # ── 正向情绪 ──────────────────────────────────────────────────────────
    (re.compile(r'自豪|骄傲|自信|得意'),                           "自豪",  ["自信"],       0.65),
    (re.compile(r'被(?:表扬|夸|称赞|鼓励)|考了?(?:满分|第一|100分?|一百分?)|拿了?(?:第一|冠军|金牌|奖)'),
                                                                   "自豪",  ["自信"],       0.65),
    (re.compile(r'终于.*?(?:完成|做完|做到|学会|弹完|成功|赢了|写完|跑完|瘦下来|戒掉)|做到了|实现了'),
                                                                   "开心",  ["成就感"],     0.65),
    (re.compile(r'自己.*?(?:做出来|做好|完成|解出|想出)|独立.*?完成'),
                                                                   "自豪",  ["自信"],       0.65),
    (re.compile(r'付出.*?(?:值了?|没白费)|努力.*?(?:成功|完成|实现)'),
                                                                   "开心",  ["成就感"],     0.65),
    (re.compile(r'开心|高兴|快乐|喜悦|幸福|满足'),                "开心",  ["满足"],       0.55),
    (re.compile(r'兴奋|激动|欢喜|雀跃|期待'),                     "兴奋",  ["期待"],       0.55),
    (re.compile(r'感动|感激|感谢|暖[心了]|温暖'),                  "感动",  [],            0.60),
    (re.compile(r'棒[了！!极]|美好|好极了|太好了|真好'),           "开心",  [],            0.55),
    (re.compile(r'放松|轻松|解脱|如释重负'),                       "放松",  [],            0.50),

    # ── 负向情绪（特殊先于通用）──────────────────────────────────────────
    (re.compile(r'后悔|遗憾|懊悔|悔恨|要是.*?就好了|如果.*?就好了|如果能重来|早知道|来不及'),
                                                                   "后悔",  ["遗憾"],       0.65),
    (re.compile(r'嫉妒|吃醋'),                                    "嫉妒",  [],            0.55),
    (re.compile(r'孤独|孤单|寂寞|没人陪|没人理|被忽略|被忽视'),   "孤独",  ["委屈"],       0.70),
    (re.compile(r'绝望|心灰意冷|撑不下去'),                       "绝望",  ["痛苦"],       0.85),
    (re.compile(r'尴尬|难堪|丢脸|不好意思'),                      "尴尬",  [],            0.50),
    (re.compile(r'无聊|乏味|没意思|提不起劲'),                    "无聊",  [],            0.40),
    (re.compile(r'失望|好失望|很失望|太失望'),                    "失望",  [],            0.75),
    (re.compile(r'委屈|憋屈|受委屈'),                             "委屈",  ["孤独"],       0.70),
    (re.compile(r'害怕|恐惧|担心|担忧|恐慌|紧张|焦虑|惶恐'),     "害怕",  ["焦虑"],       0.65),
    (re.compile(r'生气|愤怒|不满|烦[了！!]|气炸|火大|气死'),     "生气",  ["愤怒"],       0.70),
    (re.compile(r'难过|伤心|哭|悲伤|低落|沮丧|失落|难受|痛苦'),  "难过",  ["悲伤"],       0.70),
    # 自我贬低 → 难过
    (re.compile(r'太笨了?|太差了?|太烂了?|太弱了?|我不行|我真笨'),
                                                                   "难过",  ["自责"],       0.65),
    # 失败 → 难过
    (re.compile(r'失败了|搞砸了|没做好|做错了|考砸了'),           "难过",  ["自责"],       0.65),

    # ── 隐含情绪（无情绪词但有语义）─────────────────────────────────────
    # 承诺被打破（最高优先级）
    (re.compile(r'说好.*?(?:却|但|没|不|结果|就|先)|答应.*?(?:却|但|没|不|结果|就)|本来.*?(?:却|但|没|不|结果)|说.*?不.*?[，,].*?还是.*?(?:扣|罚|骂|批)'),
                                                                   "失望",  ["被辜负"],    0.75),
    # "一直XX" 高优先级（比通用没来更具体）
    (re.compile(r'一直(?:看手机|玩手机|不理我|不听|无视|假装|不来|不管)'),
                                                                   "委屈",  ["孤独"],       0.65),
    # 不等我就走
    (re.compile(r'不等我.*?就走|悄悄.*?走了|先走了'),             "失望",  ["委屈"],       0.65),
    # 被排除在外
    (re.compile(r'大家都.*?就我没|别人都.*?只有我没|只有我没(?:有)?|所有人.*?只有我没'),
                                                                   "委屈",  ["孤独"],       0.70),
    # 公开批评
    (re.compile(r'当着.*?(?:全班|大家|所有人).*?(?:说|批评|骂)|当众.*?(?:说|批评|骂)'),
                                                                   "委屈",  ["难过"],       0.70),
    # 秘密/私事被公开
    (re.compile(r'(?:把|将).*?秘密.*?(?:告诉|说给|说出)|告密|告诉.*?秘密|秘密.*?被.*?(?:知道|说出)'),
                                                                   "难过",  ["被背叛"],    0.75),
    (re.compile(r'我们.*?的事.*?告诉.*?(?:大家|所有人|别人)|告诉了大家|说给.*?大家'),
                                                                   "难过",  ["被背叛"],    0.70),
    # 没被叫到/注意到
    (re.compile(r'没(?:人|有人)?(?:来|陪|理|在意|注意到|看见|听|管|叫|找|邀请|选)'),
                                                                   "孤独",  ["委屈"],       0.70),
    # 一个人（孤独场景）
    (re.compile(r'一个人(?:站|坐|待|留|在|呆)|自己一个人|就我一个人'),
                                                                   "孤独",  ["委屈"],       0.65),
    (re.compile(r'(?:被|叫|让).*?(?:骂|批评|责怪|惩罚)'),        "委屈",  ["难过"],       0.70),
    (re.compile(r'(?:没带|不带|没让|不让).*?(?:去|玩|参加|一起)'),
                                                                   "失望",  ["委屈"],       0.75),
    (re.compile(r'(?:拿走|抢走|弄坏|毁了).*?(?:不还|不赔|不道歉)|把.*?(?:拿走了|弄坏了)'),
                                                                   "生气",  ["委屈"],       0.65),
    (re.compile(r'说.*?坏话|背后.*?说|告密|出卖.*?我'),           "难过",  ["被背叛"],    0.75),
    # 自责→难过
    (re.compile(r'都怪我|是我不对|我的错'),                       "难过",  ["自责"],       0.65),
    # 受够了/再也不想
    (re.compile(r'受够了|受不了了|真的受够|完全不想|再也不想(?:做|去|说|学)'),
                                                                   "难过",  [],            0.65),
    (re.compile(r'凭什么|为什么.*?(?:偏偏|只有我|总是我|只是我)|不公平|不公正'),
                                                                   "生气",  [],            0.65),
    (re.compile(r'世界.*?不公平|好人.*?吃亏|吃亏.*?好人'),        "生气",  [],            0.60),
]


# ---------------------------------------------------------------------------
# 信念更新规则
# ---------------------------------------------------------------------------

_BELIEF_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r'说好.*?(?:却|但|没|不|结果)|答应.*?(?:却|但|没|不|结果)|骗了?我|食言|背叛|出卖'),
                "信任下降", "trust_decrease"),
    (re.compile(r'帮了?我|保护我|为我.*?(?:做|拼|付出)|一直陪|关心我|理解我|相信我'),
                "信任增强", "trust_increase"),
    (re.compile(r'没(?:带|让|陪|理|管|来|叫|找).*?(?:我|一起)'),
                "信任下降", "trust_decrease"),
    (re.compile(r'(?:把|将).*?秘密.*?(?:告诉|说给|说出)|告密|泄露'),
                "被背叛", "betrayal"),
    (re.compile(r'说.*?坏话|背后.*?说|出卖了?我'),
                "被背叛", "betrayal"),
    (re.compile(r'考了?(?:满分|第一|100|一百)|被(?:表扬|夸|称赞|鼓励)|做到了|成功了|赢了|终于.*?完成|终于.*?了'),
                "自信提升", "self_confidence_up"),
    (re.compile(r'考(?:砸了?|差了?|不好|不及格|不理想)|被批评|被骂|输了|失败了|没做好|搞砸'),
                "自责", "self_blame"),
    (re.compile(r'都怪我|是我的错|我太笨|我太差|我不够|要是我.*?就好了'),
                "自责", "self_blame"),
    (re.compile(r'没(?:人|有人)(?:陪|理|来|注意|关心|叫|找)|一个人|被忽视|被忽略|被冷落'),
                "孤独感增强", "loneliness"),
    (re.compile(r'大家都来了|好多人陪|一起(?:玩|做)|被邀请|加入了|融入'),
                "归属感增强", "belonging"),
    (re.compile(r'没了|失去了|丢失了|离开了|走了|再也(?:不|没)'),
                "失去感", "loss"),
    (re.compile(r'终于完成|做完了|努力.*?终于|终于.*?了|付出.*?值|实现了|做到了'),
                "成就感", "achievement"),
    (re.compile(r'后悔|早知道|如果当时|要是.*?就好了|如果能重来|来不及了'),
                "遗憾", "regret"),
    (re.compile(r'世界.*?不公平|凭什么|为什么偏偏|不公正|好人.*?吃亏'),
                "世界不公平感", "world_unsafe"),
    (re.compile(r'善有善报|好人有好报|公平|得到了应有'),
                "世界公平感", "world_fair"),
]


# ---------------------------------------------------------------------------
# 重要性调整规则
# ---------------------------------------------------------------------------

_IMPORTANCE_MODIFIERS: list[tuple[re.Pattern, float]] = [
    (re.compile(r'一直|总是|每次|每天|经常|从来都|从来不'),    +0.15),
    (re.compile(r'第一次|从来没有|破天荒'),                     +0.10),
    (re.compile(r'再也|永远|以后再也|不想了'),                  +0.10),
    (re.compile(r'特别|非常|极其|超级|太.*?了|真的很'),         +0.10),
    (re.compile(r'突然|没想到|意外|没有预料'),                  +0.10),
    (re.compile(r'别人.*?(?:都|全|大家)|只有我|就我'),          +0.10),
    (re.compile(r'没什么|还好啦?|还行|不太|不算|一般|而已|罢了'),  -0.20),
    (re.compile(r'有点|稍微|略微|也许|可能|感觉.*?一点'),       -0.10),
    (re.compile(r'只是[^让叫使]'),                              -0.10),
]


# ---------------------------------------------------------------------------
# 主提取函数
# ---------------------------------------------------------------------------

def extract_meaning(text: str) -> MeaningLayer:
    """
    从文本中提取 MeaningLayer。

    处理策略：
      1. 优先在转折词后面查找情绪（转折后是真实情绪）
      2. 若转折后无非否定情绪，回退到全文查找
      3. 否定情绪：negated=True，情绪词仍作为参考情绪
    """
    contrast_m    = _CONTRAST_PAT.search(text)
    after_contrast = text[contrast_m.end():].strip() if contrast_m else ""

    best_emotion: str | None = None
    best_tags:    list[str]  = []
    base_importance: float   = 0.5
    negated = False

    def _scan(search_text: str) -> tuple[str | None, list[str], float, bool]:
        for pat, emotion, extra_tags, base_imp in _EMOTION_RULES:
            m = pat.search(search_text)
            if m:
                neg = _has_negation(search_text, m.span())
                return emotion, list(extra_tags), base_imp, neg
        return None, [], 0.5, False

    # 先扫描转折后段（如有）
    if after_contrast:
        em, tags, base_imp, neg = _scan(after_contrast)
        if em is not None and not neg:
            best_emotion, best_tags, base_importance, negated = em, tags, base_imp, False

    # 若转折后无非否定情绪（或无转折），扫描全文
    if best_emotion is None:
        em, tags, base_imp, neg = _scan(text)
        if em is not None:
            best_emotion, best_tags, base_importance, negated = em, tags, base_imp, neg

    if best_emotion is None:
        best_emotion    = "平静"
        base_importance = 0.30

    # 信念更新
    belief_update: str | None = None
    belief_type:   str | None = None
    for pat, bupdate, btype in _BELIEF_RULES:
        if pat.search(text):
            belief_update = bupdate
            belief_type   = btype
            break

    # 重要性调整
    importance = base_importance
    for pat, delta in _IMPORTANCE_MODIFIERS:
        if pat.search(text):
            importance += delta
    importance = max(0.0, min(1.0, importance))

    neg_str    = "并非真的" if negated else ""
    belief_str = f"，{belief_update}" if belief_update else ""
    meaning_summary = f"对TA来说：{neg_str}{best_emotion}{belief_str}"

    return MeaningLayer(
        emotional_impact=best_emotion,
        emotional_tags=best_tags,
        belief_update=belief_update,
        belief_type=belief_type,
        importance=round(importance, 3),
        meaning_summary=meaning_summary,
        negated=negated,
    )


# ---------------------------------------------------------------------------
# 偏好变化检测
# ---------------------------------------------------------------------------

_ITEM_EXTRACT_PATS: list[re.Pattern] = [
    re.compile(r'(?:喜欢|爱上?|爱吃|爱玩|爱看|喜爱|迷上|讨厌|不喜欢|不爱|烦)(?:吃|玩|看|做|上)?([^\s，。！？,]{1,8}?)(?=[，。！？\s了不没]|$)'),
    re.compile(r'([^\s，。！？,]{1,8}?)(?:好吃|好玩|好看|很棒|超好|很好|不好|不好玩|不好吃|难吃|难看|很香|很甜|很烦|真烦)'),
]

_CATEGORY_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r'吃|食|菜|饭|零食|水果|糖|肉|鱼|虾|蛋|奶|草莓|苹果|辣|甜'), "food"),
    (re.compile(r'玩|游戏|乐高|积木|棋|牌|球|跑|跳|游泳|跳绳|骑车'),         "hobby"),
    (re.compile(r'学|课|数学|语文|英语|科学|美术|音乐|上学'),                 "subject"),
    (re.compile(r'书|漫画|故事|小说|动画|电影|电视|视频'),                    "media"),
    (re.compile(r'朋友|同学|老师|爸爸|妈妈|兄弟|姐妹|同伴'),                  "relationship"),
    (re.compile(r'衣服|穿|颜色|款式'),                                        "clothing"),
]

_CHANGE_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r'(?:以前|曾经|之前|一开始|原来).*?(?:喜欢|爱).*?(?:现在|后来|但|不过|，).*?(?:不喜欢|讨厌|不爱|烦|没意思|腻了)'),
     "like", "dislike"),
    (re.compile(r'(?:以前|曾经|之前|一开始|原来).*?(?:不喜欢|讨厌|不爱|不想|很烦).*?(?:现在|后来|但|不过|，).*?(?:喜欢|爱上?|觉得.*?好|迷上|还好|挺好|不错|发现.*?好)'),
     "dislike", "like"),
    (re.compile(r'再也不(?:喜欢|爱|想)'),              "like",    "dislike"),
    (re.compile(r'越来越(?:不喜欢|讨厌|不爱|烦|不想)'), "like",    "dislike"),
    (re.compile(r'越来越(?:喜欢|爱|迷)'),              "neutral", "like"),
    (re.compile(r'开始(?:喜欢|爱上|迷上)'),            "neutral", "like"),
    (re.compile(r'(?:喜欢|爱).*?但.*?(?:不喜欢|讨厌|不爱|烦)'),
     "like", "dislike"),
    # "之前不爱...最近迷上"
    (re.compile(r'(?:之前|以前|一开始).*?(?:不爱|不喜欢|讨厌|很烦).*?(?:最近|现在|后来).*?(?:迷上|喜欢|爱上|爱看|爱玩|挺好|不错|还好|发现.*?好)'),
     "dislike", "like"),
    # "之前喜欢...最近越来越烦/腻了"
    (re.compile(r'(?:之前|以前|原来).*?(?:喜欢|爱).*?(?:最近|现在).*?(?:越来越烦|不喜欢|腻了|觉得没意思|不爱)'),
     "like", "dislike"),
    (re.compile(r'不想上学了|越来越不想上学'),          "like",    "dislike"),
    # "本来...后来...现在又" 振荡
    (re.compile(r'本来.*?(?:喜欢|爱).*?(?:后来|不过).*?(?:不喜欢|不爱|腻)'),
     "like", "dislike"),
    # "原来觉得...烦，现在还好" — dislike→like
    (re.compile(r'(?:原来|以前|之前).*?(?:烦|难|不好|不喜欢|不爱).*?(?:现在|后来).*?(?:还好|挺好|不错|好了|喜欢|可以)'),
     "dislike", "like"),
]

_LIKE_PAT    = re.compile(r'(?<![不没])(?:喜欢|爱上?|爱看|爱玩|爱吃|好喜欢|超喜欢|非常喜欢|迷上)')
_DISLIKE_PAT = re.compile(r'不喜欢|讨厌|不爱|反感|很烦|真烦|再也不想|腻了|没意思了|不想(?:上学|去)')


def _detect_sentiment(text: str) -> Sentiment:
    if _DISLIKE_PAT.search(text):
        return "dislike"
    if _LIKE_PAT.search(text):
        return "like"
    return "neutral"


def _detect_category(item: str) -> str:
    for pat, cat in _CATEGORY_MAP:
        if pat.search(item):
            return cat
    return "general"


def _extract_item(text: str) -> str | None:
    for pat in _ITEM_EXTRACT_PATS:
        m = pat.search(text)
        if m:
            item = m.group(1).strip().rstrip('，。！？了不')
            if item and 1 <= len(item) <= 8 and item not in {'我', '他', '她', '它', '大家'}:
                return item
    m = re.search(r'(?:喜欢|不喜欢|讨厌|爱|不爱)(?:吃|玩|看|做)?([^\s，。！？,]{1,6})', text)
    if m:
        item = m.group(1).strip()
        if item:
            return item
    return None


def extract_preference_change(
    text: str,
) -> tuple[Optional[PreferenceState], Optional[PreferenceState]]:
    now = datetime.utcnow()

    for pat, old_sent, new_sent in _CHANGE_PATTERNS:
        if pat.search(text):
            item = _extract_item(text)
            if item:
                cat = _detect_category(item)
                old_s = PreferenceState(item=item, category=cat,
                                        sentiment=old_sent,  # type: ignore[arg-type]
                                        strength=0.65, timestamp=now,
                                        source_text=text)
                new_s = PreferenceState(item=item, category=cat,
                                        sentiment=new_sent,  # type: ignore[arg-type]
                                        strength=0.70, timestamp=now,
                                        source_text=text)
                return old_s, new_s

    if _DISLIKE_PAT.search(text) or _LIKE_PAT.search(text):
        item = _extract_item(text)
        if item:
            cat  = _detect_category(item)
            sent = _detect_sentiment(text)
            new_s = PreferenceState(item=item, category=cat,
                                    sentiment=sent, strength=0.70,
                                    timestamp=now, source_text=text)
            return None, new_s

    return None, None
