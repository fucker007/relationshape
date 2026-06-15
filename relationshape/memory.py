"""记忆系统：让"它记得我"成立的地方。

- 情景记忆：带情绪显著度的事件。显著度按遗忘曲线衰减（艾宾浩斯），
  被召回时获得复习强化（间隔效应）；高唤起时刻记得更牢（闪光灯记忆）。
- 语义记忆：偏好、厌恶、用户身边的人物。
- 承诺：完整生命周期 open → due → kept/missed。
  说到做到是信任的第一来源；接不住的承诺必须主动认账，不能假装没说过。
- 敏感记忆：危机披露只进封存区，永不被闲聊召回、永不成为玩笑素材。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

from relationshape import zh
from relationshape.types import MemoryRecall, PromiseView


def _mid(text: str, ts: str) -> str:
    return hashlib.md5(f"{text}|{ts}".encode()).hexdigest()[:10]


# 头部连词：只收"几乎不作名词词首"的连接词，绝不含可/就/也/还（可乐/就业/还有）
_LEAD_CONJ_RE = re.compile(
    r"^(?:但是|可是|不过|然而|然后|所以|因为|于是|虽然|尽管|并且|况且|再说|另外|其实|而且|但|却)+"
)


def _strip_particles(item: str) -> str:
    """去掉抽取结果头尾的连接词/语气词（"我最爱的就是足球"被泛模式吞成"的就是足球"→"足球"；
    "但蘑菇我不爱吃"宾语前置吞成"但蘑菇"→"蘑菇"）。"""
    item = re.sub(r"^(?:的就是|的是|就是|的|是)", "", item or "")
    item = _LEAD_CONJ_RE.sub("", item)
    return re.sub(r"[了的呢啊呀哦吧啦嘛]+$", "", item)


def _looks_like_self_name(name: str) -> bool:
    if not name:
        return False
    if any(word in name for word in ("什么", "啥", "哪个", "哪一个")):
        return False
    if name in globals().get("_NAME_STOP", set()):
        return False
    if "名字" in name and len(name) <= 4:
        return False
    return True


def _looks_like_bare_self_name(name: str) -> bool:
    if not _looks_like_self_name(name):
        return False
    return "·" in name or len(name) >= 4


def _extract_user_name(text: str) -> str | None:
    for pattern, validator in _NAME_RES:
        m = pattern.search(text)
        if not m:
            continue
        name = re.sub(r"(吗|呢|呀|哦|吧|啦|嘛)+$", "", m.group(1).strip())
        if not name or not validator(name):
            continue
        return name
    return None


@dataclass
class Episode:
    mid: str
    text: str
    created_at: str
    valence: float = 0.0
    arousal: float = 0.2
    salience: float = 0.4
    recall_count: int = 0
    last_recalled: str = ""
    sensitive: bool = False
    vulnerability: int = 0   # 表露深度：3=秘密级，不做开场钩子
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Episode":
        return cls(**d)


@dataclass
class Promise:
    pid: str
    text: str
    made_by: str            # character / user
    created_at: str
    session_made: int
    status: str = "open"    # open / kept / missed
    surfaced: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Promise":
        return cls(**d)


_OBJ = r"[^，。！？!?,\s]{1,12}"      # 通用宾语片段
# 主语与否定/程度词之间的程度副词簇（"我一点都不爱吃""我真的不喜欢""我从来不爱"）
_ADV = r"(?:一点(?:都|也)?|半点(?:都|也)?|压根|根本|从来|向来|真的?|实在|确实|其实|完全|特别|可|就|也)?"
# 喜好：动词锚定的一类模式（不强求主语"我"，覆盖口语）
_PREFERENCE_RES = [
    re.compile(r"(?:喜欢上?|最爱|爱上|迷上了?|钟意|中意|稀罕)(" + _OBJ + r")"),
    re.compile(r"(?:超|特别|很|好|最|可|就|真|贼|忒|更)爱(" + _OBJ + r")"),
    re.compile(r"对([^，。！？!?,\s]{1,12}?)(?:特别|超|很|非常)?(?:着迷|感兴趣|入迷|上瘾)"),
    re.compile(r"(?:超|特别|很|好|最|更)迷(" + _OBJ + r")"),
    re.compile(r"爱死(" + _OBJ + r")了?"),
    re.compile(r"(" + _OBJ + r")是我(?:的)?最爱"),
]
_AVERSION_RES = [
    re.compile(r"(?:讨厌|害怕|受不了|最怕|超怕|特别怕)(?!吃)(" + _OBJ + r")"),
    re.compile(r"我怕(?!吃)(" + _OBJ + r")"),
    # 食物厌恶（"不喜欢吃X/讨厌吃X/X我不爱吃"）——这里的不喜欢是真厌恶，不是偏好撤回
    re.compile(r"(?:不(?:大|太|怎么)?(?:喜欢|爱)|讨厌|最怕|受不了)吃(" + _OBJ + r")"),
    # 宾语前置：动词须落在小句末（后面是标点/语气词/句尾），否则真正的宾语在动词之后，
    # 抓到的前缀只是连词或别的成分（日志："但我不喜欢吃面条"误把"但"当食物）。
    # 主语"我"与否定词之间允许程度副词（"我一点都不爱吃""我真的不爱吃"）。
    re.compile(r"([^，。！？!?,\s]{1,8}?)我" + _ADV + r"(?:不|最不|不大|不太|不怎么)(?:爱|喜欢)吃(?=[，。！？!?\s了啦的呢吧]|$)"),
    re.compile(r"([^，。！？!?,\s]{1,8}?)我" + _ADV + r"(?:最|特别|超|可)?(?:讨厌|受不了|怕|烦)(?=[，。！？!?\s了啦的呢吧]|$)"),   # 宾语前置：X我最讨厌
]
# 偏好失效/更新（多事实冲突）：撤回旧偏好。"不喜欢X了"是撤回，不是新厌恶
_LAZY = r"[^，。！？!?,\s]{1,12}?"
_PREF_NEGATE_RES = [
    re.compile(r"不(?:再|太|大|怎么)?(?:喜欢|爱)(?!吃)(" + _OBJ + r")"),       # 不(再/太)喜欢X（吃X归食物厌恶）
    re.compile(r"(?:不想|懒得|不)(?:想|再|继续)*(?:玩|学|看|碰|要)(" + _OBJ + r")"),  # 不(想再)玩X/不玩X
    re.compile(r"对(" + _LAZY + r")(?:玩|看)?腻"),                       # 我对X(玩)腻了
    re.compile(r"(" + _LAZY + r")(?:玩|看)腻"),                          # X玩腻了
    re.compile(r"对(" + _LAZY + r")(?:没|不感)兴趣"),                    # 对X没兴趣了
    re.compile(r"(" + _LAZY + r")我(?:已经|早就)?(?:不再|不太|不大|不怎么|不)(?:喜欢|爱)(?!吃)"),  # 宾语前置：X我已经不喜欢（不爱吃归厌恶）
]
# 名字：一类模式而非单串
_NAME_RES = [
    (re.compile(r"我(?:小名|大名|本名|大名儿)(?:是|叫)(?!什么|啥|哪|何)([^\s，。！？!?]{1,20})"), _looks_like_self_name),
    (re.compile(r"我名叫(?!什么|啥|哪|何)([^\s，。！？!?]{1,20})"), _looks_like_self_name),
    (re.compile(r"我[，,、\s]*叫(?!什么|啥|哪|何)([^\s，。！？!?啦呀哦]{1,20})"), _looks_like_self_name),
    (re.compile(r"我(?:的)?名字[呀啊呢，,\s]*(?:是|叫)(?!什么|啥|哪|何)([^\s，。！？!?]{1,20})"), _looks_like_self_name),
    (re.compile(r"(?:你可以|你|都|大家都?|人家)?(?:叫|喊)我(?!什么|啥|哪|何)([^\s，。！？!?吧呀哦]{1,20})"), _looks_like_self_name),
    (re.compile(r"(?:人家|大家都?)(?:叫|喊)(?!什么|啥|哪|何)([^\s，。！？!?吧呀哦]{1,20})[啦呀哦吧]*$"), _looks_like_self_name),
    # 年龄自我介绍框架"X，今年N岁"几乎确定在报名字 → 放宽用字（允许 好/一 等名字常用字）
    (re.compile(r"我是(?!谁|什么|啥|哪|何)([^\s，。！？!?]{1,20})[，,]?今年"), _looks_like_self_name),
    # 裸"我是X"有歧义（我是学生/好人）→ 保守排除常见谓词起始字
    (
        re.compile(r"我是(?!谁|什么|啥|哪|何|一个|一名|个|在|想|很|不|没|来|说|觉得)([^\s，。！？!?]{1,20})(?:[，,。！？!?]|岁|$)"),
        _looks_like_bare_self_name,
    ),
]
_NAME_STOP = {"什么", "谁", "个", "一", "不", "很", "真", "好",
              # 常见身份谓词：是普通名词不是名字（"我是学生"不该把名字设成"学生"）
              "学生", "老师", "医生", "男生", "女生", "男孩", "女孩", "小孩", "孩子",
              "好人", "坏人", "新人", "大人",
              # 代词/泛称：绝不当人名（日志：'那'被当成人名）
              "那", "这", "那个", "这个", "那位", "这位", "那家伙", "这家伙", "家伙", "他", "她", "它"}
_REL_WORDS = {"朋友", "同桌", "同学", "老师", "哥哥", "姐姐", "弟弟", "妹妹", "闺蜜", "发小", "邻居"}
# 疑问词：抽取到这些说明是"在问"而非"在陈述"，一律不学（通用护栏，防把问句当事实）
_INTERROG = ("什么", "啥", "谁", "哪", "多少", "怎么")
# 连词/虚词：闭类功能词，永不是偏好/厌恶/人名的对象（日志：'但'被宾语前置模式当成食物）
# 这是语法泛化（封闭词类），不是测试特例——同 _NAME_STOP / _ROLE_WORDS 一样的停用词机制
_CONJ = {"但", "但是", "可", "可是", "不过", "然而", "而", "而且", "就", "也",
         "却", "只", "还", "又", "都", "那", "这", "然后", "所以", "因为",
         "虽然", "尽管", "于是", "并且", "况且", "再说", "另外", "其实"}


def _is_interrog(s: str) -> bool:
    return any(q in s for q in _INTERROG)


# 整句是"提问/检索"而非"陈述事实"——绝不从问句里学事实（生产日志：问句被当事实存）
def is_memory_query(text: str) -> bool:
    t = (text or "").strip()
    if t.endswith(("?", "？")):
        return True
    if re.search(r"(谁|哪个|哪些|几个|多少)", t):
        return True
    if re.search(r"(什么|啥)(来着|呢|吗|么)?\s*$", t):
        return True
    return bool(re.search(r"(是什么|叫什么|爱玩什么|喜欢什么|讨厌.{0,2}什么|吃什么|记不记得|还记不记得)", t))


# 第三方主体：这些词作主语时，"喜欢X"是别人的喜好，不是用户的（日志：妈妈喜欢→记成我喜欢）。
# 亲属称谓是封闭词类——尽量收全（祖辈/父母/叔伯姑舅姨/堂表/姻亲/拟亲），可选所有格"我"。
_THIRD_PARTY = re.compile(
    r"我?(?:妈妈?|妈咪|母亲|老妈"
    r"|爸爸?|爸比|父亲|老爸"
    r"|爷爷?|奶奶?|外公|外婆|姥爷|姥姥|太爷爷?|太奶奶?|曾祖[父母]?"
    r"|哥哥?|姐姐?|弟弟?|妹妹?"
    r"|叔叔?|伯伯?|伯父|大伯|舅舅?|舅妈|姑姑?|姑妈|姑父|姑爹|姨妈?|姨夫|姨父|婶婶?|阿姨"
    r"|表哥|表姐|表弟|表妹|堂哥|堂姐|堂弟|堂妹"
    r"|嫂子?|姐夫|妹夫|弟妹|干妈|干爹|继母|继父|后妈|后爸)"
    r"|老师|教练|同学|同桌|同事|老板|领导|客户|他们?|她们?|它们?|大家"
)


def _user_is_subject(seg: str) -> bool:
    """seg=动词之前的片段。判断这段里'我'是不是离动词最近的主语（否则是第三方）。"""
    tp = list(_THIRD_PARTY.finditer(seg))
    if not tp:
        return True
    wo = seg.rfind("我")
    return wo > tp[-1].start()      # "我"比最后一个第三方主语更靠近动词 → 我是主语


# 子句切分 + 负向子句识别：线头按子句生成，否定/厌恶子句整段不产出正向线头
_CLAUSE_SPLIT = re.compile(r"[，。！？!?,.;；：:、～~…\s]+")
_NEG_CLAUSE = re.compile(r"[不没别甭]|讨厌|烦死?|腻|嫌|受不了|怕|烦人")


def _is_third_party_clause(clause: str) -> bool:
    """子句的"主语位"是第三方 → 整段是"别人的事"，不产出正向线头。
    主语位 = 句首（可选所有格"我"打头）。"我外公总喜欢给我煮面条"主语是外公→True；
    "我和同学去春游""我今天去玩"主语是我→False（"我"后面不是亲属，是动词/连词）。
    必要性：content_runs 会把"外公"切成"公…"逃过词级过滤；且"给我煮"里的"我"是受事不是主语，
    不能据此判用户参与——故只看句首主语，不看后文是否出现"我"。"""
    return bool(_THIRD_PARTY.match(clause)) or (
        clause[:1] == "我" and bool(_THIRD_PARTY.match(clause[1:]))
    )


def clean_hook_tokens(text: str, negatives: list[str] | None = None) -> list[str]:
    """开场线头候选清洗：线头是"下次主动惦记的、属于用户自己的正向话题"。按子句处理：
    (1) 含否定/厌恶词的子句整段跳过（"但说实话我不爱吃面条"——content_runs 会吞掉"不"，
        故须在子句层用否定词识别，否则"但说实话""喜欢吃面条"这类碎片会漏成线头）；
    (2) 主语是第三方的子句整段跳过（"我外公喜欢熬南瓜"——是别人的事）；
    (3) 子句内再剔除含第三方主语的片段、与厌恶重叠的片段、问句词。
    这是泛化的钩子防污染（子句级否定/第三方 + 词级过滤），不针对任何特定句子。"""
    negs = [n for n in (negatives or []) if n]
    out: list[str] = []
    for clause in _CLAUSE_SPLIT.split(text or ""):
        if not clause or _NEG_CLAUSE.search(clause) or _is_third_party_clause(clause):
            continue                                   # 负向/第三方子句不产出正向线头
        for tok in zh.content_runs(clause):
            if _THIRD_PARTY.search(tok) or _is_interrog(tok):
                continue
            if any(n in tok or tok in n for n in negs):
                continue
            if tok not in out:
                out.append(tok)
    return out
_RELG = r"(朋友|同桌|同学|老师|哥哥|姐姐|弟弟|妹妹|闺蜜|发小|邻居|队友|死党)"
_NM = r"[^\s，。！？!?的了好亲最就和跟与是]{1,4}"     # 通用名字片段
_NM3 = r"[^\s，。！？!?的了对很太特好就和跟与是啊呀]{1,3}"  # 紧跟关系后的名字（更紧）
_PERSON_RES = [
    # X(就)是我(最好)(的)(好)同桌 —— 名字在前
    re.compile(r"(" + _NM + r")(?:就)?是我(?:最|最好)?的?(?:好|亲)?" + _RELG),
    # 我(有个|的|那个|那位)(好)同桌(叫|是)X —— 关系在前，名字在后
    re.compile(r"我(?:有个|的|那个|那位)(?:好|亲)?" + _RELG + r"(?:叫|是)(" + _NM + r")"),
    # 我(跟|和|与)X是(我)(的)同桌
    re.compile(r"我(?:跟|和|与)(" + _NM + r")是(?:我)?的?" + _RELG),
    # 我的同桌X（名字紧跟关系，无叫/是）
    re.compile(r"我的" + _RELG + r"(" + _NM3 + r")"),
    # X，我(的)闺蜜
    re.compile(r"(" + _NM + r")[，,]\s*我(?:的)?" + _RELG),
]
# 角色/关系泛称：永远不当作"具体人名"塞进用户档案（只有真名+关系才算"身边的人"）
_ROLE_WORDS = _REL_WORDS | {"客户", "老板", "领导", "同事", "教练", "妈妈", "爸爸",
                            "爷爷", "奶奶", "外婆", "外公", "姥姥", "姥爷", "老师",
                            "我妈", "我爸", "我爷", "我奶", "阿姨", "叔叔", "舅舅", "那", "这",
                            "那个", "这个", "他", "她", "它", "家伙", "那家伙"}
# 带区分属性的同类实体："(我有一个)[打篮球]的[朋友](叫)[尼古拉]"——存属性，支持计数与按属性检索
_QNUM = r"(?:[一二两三四五六七八九十0-9]+\s*[个位名]|个|俩|仨)?"
_QATTR = r"([^，。！？!?的\s]{0,8})"
_QNAME = r"([^，。！？!?的了是\s叫]{1,5})"
_QP_NAME_LAST = re.compile(r"(?:有|还有|认识|多了)?" + _QNUM + _QATTR + r"的(?:好|亲)?" + _RELG + r"(?:叫|是|，叫|，)?" + _QNAME)
_QP_NAME_FIRST = re.compile(_QNAME + r"是我" + _QATTR + r"的(?:好|亲)?" + _RELG)
_ATTR_LEAD = re.compile(r"^(?:我)?(?:有|还有|认识|多了)?(?:[一二两三四五六七八九十0-9]+\s*[个位名]|个|一个|两个|那个|这个|有个|俩|仨)?(?:的)?")
# 品类化偏好/厌恶："喜欢的[水果]是[苹果]" / "[水果]里我最喜欢[苹果]" —— category→item
_CAT = r"([^，。！？!?是的\s]{1,6})"
_ITEM = r"([^，。！？!?的了是\s]{1,8})"
_CAT_PREF_RES = [
    re.compile(r"(?:最)?(?:喜欢|爱)的" + _CAT + r"(?:是|就是)" + _ITEM),
    re.compile(_CAT + r"(?:里|中|当中)(?:我)?(?:最)?(?:喜欢|爱)(?:的(?:就)?是)?" + _ITEM),
    re.compile(r"要说" + _CAT + r"(?:我)?(?:最)?(?:喜欢|爱)" + _ITEM),
]
_CAT_AVERSION_RES = [
    re.compile(r"(?:最)?(?:讨厌|怕|不喜欢)的" + _CAT + r"(?:是|就是)" + _ITEM),
    re.compile(_CAT + r"(?:里|中|当中)(?:我)?(?:最)?(?:讨厌|怕|不喜欢)(?:的(?:就)?是)?" + _ITEM),
]
# 最在乎：高优先级、长期保留、永远进档案（用户主动强调的核心）
# 注意顺序：带"的(就)是"的更具体的先匹配，最后才是裸"最在乎X"
_CARED_RES = [
    re.compile(r"最(?:在乎|看重|珍惜|放不下|重视)的(?:就)?是([^，。！？!?]{1,14})"),
    re.compile(r"对我(?:来说)?最重要的(?:就)?是([^，。！？!?]{1,14})"),
    re.compile(r"心心念念的(?:就)?是([^，。！？!?]{1,14})"),
    re.compile(r"([^，。！？!?]{1,14}?)(?:对我(?:来说)?)?(?:就)?是(?:我的)?一切"),
    re.compile(r"([^，。！？!?]{1,14})是我(?:心里)?最(?:在乎|看重|重要|珍惜)的"),
    re.compile(r"(?:我)?(?:这辈子)?最(?:在乎|看重|珍惜|放不下|重视)([^，。！？!?]{1,14})"),
]

# 角色承诺的口头模式："下次我给你讲…" "明天我们…"
_CHAR_PROMISE_RE = re.compile(
    r"((下次|明天|以后|等你回来|下回)[^，。！？!?]{0,12}(我|我们|咱们)[^，。！？!?]{1,18}|我(答应你|保证)[^，。！？!?]{1,18})"
)


class MemoryBank:
    def __init__(self) -> None:
        self.episodes: list[Episode] = []
        self.preferences: list[str] = []
        self.aversions: list[str] = []
        self.people: dict[str, dict] = {}     # 名字/角色 -> {"relation":…, "mentions":n}
        self.user_name: str | None = None
        self.cared: list[str] = []            # 用户主动强调"最在乎"的核心，长期保留
        self.cat_prefs: dict[str, str] = {}   # 品类化偏好：水果→苹果、运动→篮球
        self.cat_aversions: dict[str, str] = {}
        self.promises: list[Promise] = []

    # ------------------------------------------------------------------ 写入

    def add_episode(
        self, text: str, valence: float, arousal: float, now: datetime,
        sensitive: bool = False, vulnerability: int = 0, tags: list[str] | None = None,
    ) -> Episode:
        # 显著度：情绪越强记得越牢（闪光灯记忆的工程近似）
        salience = min(1.0, 0.3 + 0.4 * abs(valence) + 0.3 * arousal)
        ep = Episode(
            mid=_mid(text, now.isoformat()), text=text[:80], created_at=now.isoformat(),
            valence=valence, arousal=arousal, salience=salience,
            sensitive=sensitive, vulnerability=vulnerability, tags=tags or [],
        )
        self.episodes.append(ep)
        return ep

    def extract_facts(self, text: str, mentioned_actors: list[str]) -> list[str]:
        """从用户原话提取语义事实，返回"新学到的事"列表（供奖励判断）。"""
        learned: list[str] = []
        if is_memory_query(text):
            return learned          # 问句是检索，不是事实——绝不学进记忆
        nm = _extract_user_name(text)
        if nm and self.user_name != nm:
            self.user_name = nm
            learned.append(f"名字：{nm}")
        # 先处理偏好撤回（"不喜欢X了"）：删旧偏好，且标记为已撤回，
        # 避免后续正向模式（"不喜欢"里嵌着"喜欢X"）把它又加回去，也不当新厌恶
        retracted: set[str] = set()
        for re_n in _PREF_NEGATE_RES:
            for m in re_n.finditer(text):
                item = _strip_particles(m.group(1))
                if item and not _is_interrog(item):
                    retracted.add(item)
                    if item in self.preferences:
                        self.preferences.remove(item)
                        learned.append(f"不再喜欢：{item}")
        # 品类化偏好/厌恶先抽（"喜欢的水果是苹果"），存 category→item，并把 item 也加进偏好
        cat_items: set[str] = set()
        for re_c in _CAT_PREF_RES:
            for m in re_c.finditer(text):
                c, it = _strip_particles(m.group(1)), _strip_particles(m.group(2))
                if c and it and not _is_interrog(c) and not _is_interrog(it):
                    self.cat_prefs[c] = it
                    cat_items.add(it)
                    if it not in self.preferences and it not in retracted:
                        self.preferences.append(it)
                        learned.append(f"喜欢的{c}：{it}")
        for re_c in _CAT_AVERSION_RES:
            for m in re_c.finditer(text):
                c, it = _strip_particles(m.group(1)), _strip_particles(m.group(2))
                if c and it and not _is_interrog(c) and not _is_interrog(it):
                    self.cat_aversions[c] = it
                    cat_items.add(it)
                    if it not in self.aversions:
                        self.aversions.append(it)
                        learned.append(f"讨厌的{c}：{it}")
        for re_p in _PREFERENCE_RES:
            for m in re_p.finditer(text):
                item = _strip_particles(m.group(1))
                # 只认"不/没"为否定（别在"特别"里——"我特别喜欢X"绝不是否定）
                neg_before = m.start() > 0 and text[m.start() - 1] in "不没"
                # 第三方主体（妈妈喜欢X）不是用户的偏好；前有"不/没"是否定（不喜欢吃X）；"是"多半是被吞的"X是Y"
                if (item and "是" not in item and not _is_interrog(item) and not neg_before
                        and item not in retracted and item not in cat_items and item not in _CONJ
                        and _user_is_subject(text[: m.start()])):
                    if item in self.preferences:
                        self.preferences.remove(item)      # 重提/转移 → 提到最近
                    self.preferences.append(item)
                    if f"喜欢：{item}" not in learned:
                        learned.append(f"喜欢：{item}")
        for re_a in _AVERSION_RES:
            for m in re_a.finditer(text):
                item = _strip_particles(m.group(1))
                if (item and "是" not in item and not _is_interrog(item) and item not in _CONJ
                        and item not in retracted and item not in cat_items and item not in self.aversions):
                    self.aversions.append(item)
                    learned.append(f"不喜欢：{item}")
        def _add_person(name, relation, attr=""):
            name = _strip_particles(name)
            attr = _ATTR_LEAD.sub("", _strip_particles(attr or "")).strip()
            if (name and name not in _NAME_STOP and name not in _ROLE_WORDS
                    and not _is_interrog(name) and not _is_interrog(attr)):
                if name not in self.people:
                    self.people[name] = {"relation": relation, "attr": attr, "mentions": 0}
                    learned.append(f"身边的人：{name}（{attr+'的' if attr else ''}{relation}）")
                elif attr and not self.people[name].get("attr"):
                    self.people[name]["attr"] = attr     # 补全属性
        # 带属性的限定人物先抽（更具体）；存区分属性
        for m in _QP_NAME_LAST.finditer(text):
            _add_person(m.group(3), m.group(2), m.group(1))
        for m in _QP_NAME_FIRST.finditer(text):
            _add_person(m.group(1), m.group(3), m.group(2))
        for re_p in _PERSON_RES:
            for m in re_p.finditer(text):
                g = m.groups()
                name, relation = (g[0], g[1]) if g[1] in _REL_WORDS else (g[1], g[0])
                _add_person(name, relation)
        for re_c in _CARED_RES:
            m = re_c.search(text)
            if m:
                item = _strip_particles(m.group(1))
                if item and not _is_interrog(item) and item not in self.cared:
                    self.cared.append(item)
                    self.cared = self.cared[-6:]
                    learned.append(f"最在乎：{item}")
                break
        # 注意：不再把 mentioned_actors（朋友/妈妈/那 等泛称/代词）塞进 people——
        # 只有"真名+关系"经上面的人物抽取才进 people（日志：朋友/我妈污染了人名库）
        return learned

    def user_profile_facts(self) -> dict:
        """结构化的"一个朋友本就知道的你"——不依赖字面命中，长期稳定。"""
        return {
            "name": self.user_name,
            "preferences": self.preferences[-5:],
            "aversions": self.aversions[-3:],
            # 只把"真名+关系"的人放进档案；泛称角色词不算具体的人。带区分属性的多带几个，支持计数/按属性检索
            "people": [(k, v.get("relation", ""), v.get("attr", "")) for k, v in self.people.items()
                       if k not in _ROLE_WORDS][-8:],
            "cared": self.cared[-3:],
            "cat_prefs": dict(list(self.cat_prefs.items())[-6:]),
            "cat_aversions": dict(list(self.cat_aversions.items())[-4:]),
        }

    def categorized_prefs(self) -> dict:
        return self.cat_prefs

    # ------------------------------------------------------------------ 遗忘

    def decay_and_prune(self, now: datetime, half_life_days: float, threshold: float, cap: int) -> None:
        kept: list[Episode] = []
        for ep in self.episodes:
            created = datetime.fromisoformat(ep.created_at)
            days = max(0.0, (now - created).total_seconds() / 86400.0)
            # 复习强化：召回次数延长记忆寿命
            effective_half_life = half_life_days * (1 + 0.8 * ep.recall_count)
            current = ep.salience * (0.5 ** (days / effective_half_life))
            if ep.sensitive or current >= threshold:
                kept.append(ep)
        kept = kept[-cap:]
        self.episodes = kept

    # ------------------------------------------------------------------ 召回

    def recall(self, query_text: str, now: datetime, k: int, half_life_days: float) -> list[MemoryRecall]:
        """相关性 = 主题重叠 × 当前显著度 × 新近度。敏感记忆不参与闲聊召回。"""
        qb = zh.bigrams(query_text)
        scored: list[tuple[float, Episode, int]] = []
        for ep in self.episodes:
            if ep.sensitive:
                continue
            created = datetime.fromisoformat(ep.created_at)
            days = (now - created).total_seconds() / 86400.0
            if days < 0:
                continue
            overlap = zh.jaccard(qb, zh.bigrams(ep.text))
            if overlap <= 0.02:
                continue
            effective_half_life = half_life_days * (1 + 0.8 * ep.recall_count)
            current_salience = ep.salience * (0.5 ** (days / effective_half_life))
            recency = 1.0 / (1.0 + days / 7.0)
            score = overlap * (0.5 + current_salience) * (0.6 + 0.4 * recency)
            scored.append((score, ep, int(days)))
        scored.sort(key=lambda x: -x[0])
        out: list[MemoryRecall] = []
        for score, ep, days in scored[:k]:
            ep.recall_count += 1
            ep.last_recalled = now.isoformat()
            out.append(MemoryRecall(
                text=ep.text, kind="episode", score=round(score, 3), days_ago=days,
                hint="相关就自然带一句，不相关就别硬塞",
            ))
        return out

    def most_salient(self, now: datetime, half_life_days: float, k: int = 3) -> list[MemoryRecall]:
        """模糊回指（"上次那件事"）兜底：浮出显著度最高的几条候选。
        多件事时"那件事"本就有歧义——浮出候选让模型据上下文挑、或轻轻确认是哪件
        （好朋友的反应：'你是说养乌龟还是上次摔跤那件？'），而不是默默猜错。"""
        scored = []
        for ep in self.episodes:
            if ep.sensitive:
                continue
            days = max(0.0, (now - datetime.fromisoformat(ep.created_at)).total_seconds() / 86400)
            half = half_life_days * (1 + 0.8 * ep.recall_count)
            cur = ep.salience * (0.5 ** (days / half))
            scored.append((cur, ep, int(days)))
        scored.sort(key=lambda x: -x[0])
        out = []
        multi = len(scored) > 1
        for cur, ep, days in scored[:k]:
            ep.recall_count += 1
            hint = ("对方在模糊提'那件事'，候选不止一件——不确定就轻轻问是哪件，别默默猜"
                    if multi else "对方在模糊提'那件事'，多半就是这件，自然接住")
            out.append(MemoryRecall(text=ep.text, kind="episode", score=round(cur, 3),
                                    days_ago=days, hint=hint))
        return out

    def recent_highlight(self, now: datetime) -> MemoryRecall | None:
        """开场用：最近 10 天里显著度最高的一件事（问候语没有话题词可匹配）。

        秘密级表露（vulnerability>=3）不做开场白：朋友会在话题相关时
        温柔接住秘密，但不会拿它打招呼。
        """
        best: tuple[float, Episode] | None = None
        for ep in self.episodes:
            if ep.sensitive or ep.vulnerability >= 3:
                continue
            try:
                created_at = datetime.fromisoformat(ep.created_at)
            except ValueError:
                continue
            days = (now - created_at).total_seconds() / 86400.0
            if days < 0:
                continue
            if days > 10:
                continue
            denom = max(1e-6, 1.0 + days / 5.0)
            if denom <= 0:
                continue
            score = ep.salience * (1.0 / denom)
            if best is None or score > best[0]:
                best = (score, ep)
        if best is None:
            return None
        ep = best[1]
        days_ago = int(max(0.0, (now - datetime.fromisoformat(ep.created_at)).total_seconds() / 86400.0))
        return MemoryRecall(
            text=ep.text, kind="episode", score=round(best[0], 3), days_ago=days_ago,
            hint="开场可以像朋友惦记一样问起这件事的后续",
        )

    def recall_preferences(self, query_text: str, k: int = 2) -> list[MemoryRecall]:
        qb = zh.bigrams(query_text)
        out = []
        for pref in self.preferences:
            if zh.jaccard(qb, zh.bigrams(pref)) > 0.05:
                out.append(MemoryRecall(
                    text=f"用户喜欢{pref}", kind="preference", score=0.5, days_ago=0,
                    hint="可以用对方的喜好接话",
                ))
        return out[:k]

    def recall_semantic_facts(self, query_text: str, k: int = 6) -> list[MemoryRecall]:
        """按语义问题召回稳定事实，不依赖用户再次说出同一个槽位词。

        这层只处理确定性语义记忆（偏好、雷区、身边的人），避免把情景回忆
        的词面重叠当作"记住了"。
        """
        text = re.sub(r"\s+", "", query_text or "")
        out: list[MemoryRecall] = []

        asks_preferences = bool(re.search(r"(偏好|喜好|爱好|喜欢什么|爱什么|爱吃什么|爱玩什么|记得我喜欢)", text))
        asks_aversions = bool(re.search(r"(雷区|讨厌什么|不喜欢什么|怕什么|害怕什么|受不了什么|记得我讨厌)", text))
        asks_people = bool(re.search(r"(谁是我的|我的朋友|我的同桌|我的同学|我的老师|身边的人|我认识谁)", text))

        if asks_preferences:
            for pref in self.preferences[-k:]:
                out.append(MemoryRecall(
                    text=f"用户喜欢{pref}", kind="preference", score=0.8, days_ago=0,
                    hint="用户问自己的偏好时，这是确定事实，直接回答",
                ))

        if asks_aversions:
            for item in self.aversions[-k:]:
                out.append(MemoryRecall(
                    text=f"用户讨厌{item}", kind="aversion", score=0.8, days_ago=0,
                    hint="用户问自己的雷区时，这是确定事实，直接回答",
                ))

        if asks_people:
            for name, entry in list(self.people.items())[-k:]:
                relation = str(entry.get("relation") or "熟人")
                if relation in text or "谁是我的" in text or "身边的人" in text or "我认识谁" in text:
                    out.append(MemoryRecall(
                        text=f"{name}是用户的{relation}", kind="person", score=0.75, days_ago=0,
                        hint="用户问身边的人时，这是确定关系，直接回答",
                    ))
        else:
            for name, entry in self.people.items():
                if name and name in text:
                    relation = str(entry.get("relation") or "熟人")
                    out.append(MemoryRecall(
                        text=f"{name}是用户的{relation}", kind="person", score=0.75, days_ago=0,
                        hint="用户问这个人是谁时，这是确定关系，直接回答",
                    ))

        seen: set[tuple[str, str]] = set()
        deduped: list[MemoryRecall] = []
        for item in out:
            key = (item.kind, item.text)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= k:
                break
        return deduped

    # ------------------------------------------------------------------ 承诺

    def detect_character_promise(self, assistant_text: str, now: datetime, session_index: int) -> Promise | None:
        m = _CHAR_PROMISE_RE.search(assistant_text)
        if not m:
            return None
        text = m.group(0)[:40]
        # 同文已存在就不重复记
        for p in self.promises:
            if p.text == text and p.status == "open":
                return None
        promise = Promise(
            pid=_mid(text, now.isoformat()), text=text, made_by="character",
            created_at=now.isoformat(), session_made=session_index,
        )
        self.promises.append(promise)
        return promise

    def due_promises(self, session_index: int) -> list[PromiseView]:
        """下一次会话起，未兑现的承诺就到期：要么兑现，要么主动认账。"""
        views = []
        for p in self.promises:
            if p.status == "open" and session_index > p.session_made:
                note = "主动提起并兑现它" if p.surfaced == 0 else "已经提过还没兑现：认账+补救，别再拖"
                views.append(PromiseView(pid=p.pid, text=p.text, made_by=p.made_by, status="due", note=note))
        return views

    def mark_promise(self, pid: str, status: str) -> None:
        for p in self.promises:
            if p.pid == pid:
                p.status = status
                return

    # ------------------------------------------------------------------ serde

    def to_dict(self) -> dict:
        return {
            "episodes": [e.to_dict() for e in self.episodes],
            "preferences": self.preferences,
            "aversions": self.aversions,
            "people": self.people,
            "user_name": self.user_name,
            "cared": self.cared,
            "cat_prefs": self.cat_prefs,
            "cat_aversions": self.cat_aversions,
            "promises": [p.to_dict() for p in self.promises],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryBank":
        bank = cls()
        bank.episodes = [Episode.from_dict(e) for e in d.get("episodes", [])]
        bank.preferences = d.get("preferences", [])
        bank.aversions = d.get("aversions", [])
        bank.people = d.get("people", {})
        bank.user_name = d.get("user_name")
        if not bank.user_name or any(word in str(bank.user_name) for word in ("什么", "啥", "哪个", "哪一个")):
            for ep in reversed(bank.episodes):
                name = _extract_user_name(ep.text)
                if name:
                    bank.user_name = name
                    break
        bank.cared = d.get("cared", [])
        bank.cat_prefs = d.get("cat_prefs", {})
        bank.cat_aversions = d.get("cat_aversions", {})
        bank.promises = [Promise.from_dict(p) for p in d.get("promises", [])]
        return bank
