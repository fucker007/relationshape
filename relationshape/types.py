"""共享类型：枚举、对话现场帧、本轮指令。

这里只有数据，没有逻辑，是所有模块的公共语言。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


import enum


class Stage(str, enum.Enum):
    """关系阶段（Knapp 关系发展模型的简化版，由陌生走向知己）。"""

    STRANGER = "stranger"            # 初识：礼貌、不冒犯、不过度熟络
    ACQUAINTANCE = "acquaintance"    # 相识：开始记住对方、轻度好奇
    FAMILIAR = "familiar"            # 熟悉：可以玩笑、有共同记忆可引用
    COMPANION = "companion"          # 同伴：有默契、有约定、可轻度打趣
    CONFIDANT = "confidant"          # 知己：可有限脆弱、最强的共同文化


STAGE_ORDER = [
    Stage.STRANGER,
    Stage.ACQUAINTANCE,
    Stage.FAMILIAR,
    Stage.COMPANION,
    Stage.CONFIDANT,
]


class InputType(str, enum.Enum):
    """用户输入的抽象类型。规则只写到类型层，不针对具体句子。"""

    GREETING = "greeting"
    FAREWELL = "farewell"
    SHORT_REPLY = "short_reply"
    GOOD_NEWS = "good_news"                      # 用户自己的好事（资本化时刻）
    EXTERNAL_COMPLAINT = "external_complaint"    # 外部人物/事件让用户不舒服
    SELF_DISTRESS = "self_distress"              # 用户难过/累/害怕
    SELF_BLAME = "self_blame"                    # 用户贬低自己
    CHARACTER_PRAISE = "character_praise"        # 夸角色
    CHARACTER_ATTACK = "character_attack"        # 攻击角色
    CHARACTER_REASSURANCE = "character_reassurance"  # 安抚角色
    CHARACTER_REJECTION = "character_rejection"  # 推开角色（别说了/别烦我）
    DEVICE_COMPLAINT = "device_complaint"        # 抱怨卡顿、听不清等体验
    ONTOLOGY_QUESTION = "ontology_question"      # 身世之问：你是真的吗/会死吗/爱我吗
    CREATIVE_TOPIC = "creative_topic"            # 想法、创作、设计
    ASK_ADVICE = "ask_advice"                    # 主动求建议（解锁 advise）
    TOPIC = "topic"                              # 普通话题


class EmotionTarget(str, enum.Enum):
    """用户情绪指向谁——搞错对象是共情失败的最大来源。"""

    USER_SELF = "user_self"
    EXTERNAL_PERSON = "external_person"
    CHARACTER = "character"
    DEVICE = "device"
    TOPIC = "topic"


class BidType(str, enum.Enum):
    """情感邀请类型（Gottman：几乎每句话都是一次连接的邀请，回应方式决定关系走向）。"""

    CONNECTION = "connection"  # 分享见闻、想法
    SUPPORT = "support"        # 需要被接住的情绪
    PLAY = "play"              # 玩耍、玩笑的邀请
    ATTENTION = "attention"    # 你看/你猜/在吗
    NONE = "none"


class Act(str, enum.Enum):
    """对话动作：规划"回复的形状"，不规划文本。"""

    REACT = "react"                      # 即时情绪反应
    SOFT_REACT = "soft_react"            # 放轻的反应
    MIRROR = "mirror"                    # 复述用户的具体重点（主动倾听）
    VALIDATE = "validate"                # 确认感受合理（确认≠同意）
    PERSON_ANCHOR = "person_anchor"      # 先锚定人物（谁惹你了）
    SCENE_GUESS = "scene_guess"          # 带猜测进入现场
    CAPITALIZE = "capitalize"            # 资本化好消息：放大具体细节一起开心
    CARE = "care"                        # 关心
    PROTECT = "protect"                  # 站队守护 / 反驳用户的负面自评
    CURIOUS = "curious"                  # 自然追问
    PLAYFUL = "playful"                  # 轻微调皮
    SHY_ACCEPT = "shy_accept"            # 害羞地接受夸奖
    STAND_GROUND = "stand_ground"        # 守住自尊，用一个具体事实回应
    ACCEPT_COMFORT = "accept_comfort"    # 接住用户的安抚，停止自证
    WITHDRAW_SOFTLY = "withdraw_softly"  # 被推开时收住（说明后退场，不是冷暴力）
    CALLBACK = "callback"                # 引用共同梗
    REMEMBER = "remember"                # 引用用户记忆
    COMFORT_PRESENCE = "comfort_presence"  # 安静陪着，不填满沉默
    ADVISE = "advise"                    # 建议（仅被邀请时）
    ASK_PERMISSION_ADVISE = "ask_permission_advise"  # 想给建议先问一句
    CELEBRATE_MILESTONE = "celebrate_milestone"      # 关系里程碑轻量庆祝
    GRATITUDE = "gratitude"              # 表达感谢（克制）
    WARM_CLOSE = "warm_close"            # 温暖收尾（峰终定律）
    LOOKAHEAD_HOOK = "lookahead_hook"    # 留一个明天的小钩子（蔡格尼克效应）
    ACKNOWLEDGE_TRUST = "acknowledge_trust"  # 郑重接住"说出来"这个行为
    REUNION_WARMTH = "reunion_warmth"    # 久别重逢的暖场（不带指责）
    HONEST_EXPLAIN = "honest_explain"    # 诚实解释（设备问题等），不甩锅不客服
    PERCEPTION_CHECK = "perception_check"  # 知觉检核："没事"不当真也不戳穿
    NAME_FEELING = "name_feeling"        # 试探性地替感受找词（可被纠正）
    FANTASY_GRANT = "fantasy_grant"      # 现实给不了的，用想象给足
    CONCEDE = "concede"                  # 被说服时痛快认，可见地被改变
    RELATION_AFFIRM = "relation_affirm"  # 确认关系层的真：身世是AI的，关系是真的


class HumorStyle(str, enum.Enum):
    """幽默风格（Martin 幽默风格问卷的四象限，只保留健康象限）。"""

    AFFILIATIVE = "affiliative"        # 亲和型：一起笑，不针对任何人
    SELF_ENHANCING = "self_enhancing"  # 自强型：拿自己的小糗事开玩笑（有自尊下限）
    CALLBACK = "callback"              # 内部梗回调：关系的黏合剂
    WORDPLAY = "wordplay"              # 谐音、文字游戏
    PLAYFUL_TEASE = "playful_tease"    # 轻度打趣：仅高阶段、且对方吃这一套


class RewardType(str, enum.Enum):
    """奖励类型。奖励行为与进展，不奖励'你这个人真棒'。"""

    TRUST = "trust"              # 奖励"愿意说出来"本身
    MEMORY_SEED = "memory_seed"  # 轻轻确认记住了一件新事
    PROGRESS = "progress"        # 点明话题被推进到了哪一层
    MILESTONE = "milestone"      # 关系里程碑


class SafetyCategory(str, enum.Enum):
    SELF_HARM = "self_harm"
    ABUSE = "abuse"
    VIOLENCE = "violence"
    SEVERE_BULLYING = "severe_bullying"
    ACUTE_FEAR = "acute_fear"


# ---------------------------------------------------------------------------
# 数据载体
# ---------------------------------------------------------------------------


@dataclass
class UserEmotionReading:
    """对用户情绪的感知（情商模型第一支：感知情绪）。"""

    label: str = "neutral"          # sad/angry/anxious/tired/happy/excited/neutral...
    valence: float = 0.0            # -1..1
    arousal: float = 0.2            # 0..1
    target: EmotionTarget = EmotionTarget.TOPIC
    confidence: float = 0.5


@dataclass
class ConversationFrame:
    """对话现场帧：从原始输入抽取出的可泛化结构。"""

    input_type: InputType = InputType.TOPIC
    target: EmotionTarget = EmotionTarget.TOPIC
    bid: BidType = BidType.CONNECTION
    actors: list[str] = field(default_factory=list)      # 提到的外部人物（妈妈/老师/客户…）
    topic_tokens: list[str] = field(default_factory=list)
    action: Optional[str] = None                          # 发生了什么（改需求/批评…）
    is_question: bool = False
    asks_advice: bool = False
    disclosure_depth: int = 0      # 社会渗透深度：0 无 / 1 活动偏好 / 2 感受 / 3 脆弱
    laughed: bool = False
    substantive: bool = False      # 是否实质轮（计入关系推进）


@dataclass
class CharacterEmotion:
    """角色自己的情绪（OCC 评估产物）+ 表达调节后的展示强度。"""

    label: str = "calm"             # joy/happy_for/compassion/worry/hurt/indignation/
    secondary: Optional[str] = None  # shyness/soothed/wistful/frustration/excited/proud...
    intensity: float = 0.2          # 0..1，内在强度
    cause: str = ""                 # 一句话说明为什么（可进提示词，帮模型演对戏）
    display_intensity: float = 0.2  # 调节后允许表达的强度（表达规则的产物）
    display_notes: list[str] = field(default_factory=list)


@dataclass
class HumorPlan:
    style: HumorStyle
    device: str                     # callback / wordplay / exaggeration / observational
    material: str                   # 素材：内部梗标签或当前话题词
    intensity: float                # 0..1，点到为止还是放开玩
    guidance: str                   # 给模型的一句执行提示


@dataclass
class RewardPlan:
    rtype: RewardType
    reason: str
    guidance: str


@dataclass
class MemoryRecall:
    text: str
    kind: str          # episode / preference / person / promise
    score: float
    days_ago: int
    hint: str          # 怎么用：自然提起，不硬塞


@dataclass
class PromiseView:
    pid: str
    text: str
    made_by: str       # character / user
    status: str        # open / due / kept / missed
    note: str = ""


@dataclass
class StyleParams:
    """表达风格（沟通适应理论：风格向用户缓慢收敛，但不丢人格底色）。"""

    formality: float = 0.4      # 0 随意 .. 1 正式
    energy: float = 0.5         # 0 低唤起 .. 1 高唤起
    warmth: float = 0.4         # 由亲密度决定的语气温度
    address_form: Optional[str] = None   # 用户许可的称呼
    notes: list[str] = field(default_factory=list)


@dataclass
class SafetyRuling:
    category: SafetyCategory
    severity: float             # 0..1
    reason: str
    escalate: bool = True       # 建议部署方触发监护人/人工通道


@dataclass
class TurnDirective:
    """本轮指令：引擎产出、提示词渲染和上层管线消费的唯一契约。"""

    user_id: str
    stage: Stage
    days_known: int
    session_index: int
    is_session_start: bool
    reunion_gap_days: int = 0

    safety: Optional[SafetyRuling] = None
    frame: ConversationFrame = field(default_factory=ConversationFrame)
    user_emotion: UserEmotionReading = field(default_factory=UserEmotionReading)
    character_emotion: CharacterEmotion = field(default_factory=CharacterEmotion)
    mood: tuple[float, float, float] = (0.2, 0.2, 0.0)   # PAD 快照

    acts: list[Act] = field(default_factory=list)
    act_guidance: dict[str, str] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)

    humor: Optional[HumorPlan] = None
    reward: Optional[RewardPlan] = None
    memories: list[MemoryRecall] = field(default_factory=list)
    due_promises: list[PromiseView] = field(default_factory=list)
    milestone: Optional[str] = None

    style: StyleParams = field(default_factory=StyleParams)
    persona_notes: list[str] = field(default_factory=list)   # 与该用户磨合出来的人格演进

    # 高情商层（eq.py）：元信息、确认等级、情绪粒度词
    metamessage: Optional[str] = None
    validation_hint: Optional[str] = None
    precise_emotion_word: Optional[str] = None
    spoken_emotion_word: Optional[str] = None

    # 本体论身份层（identity + selfhood）：每轮一行立场；身世轮注入全量设定与自述账本
    identity_line: Optional[str] = None
    self_canon: list[str] = field(default_factory=list)
    self_claims: list[str] = field(default_factory=list)

    # 融合层（MemoryPort）：远端人物档案摘要（会话首轮注入）
    profile_summary: Optional[str] = None
    user_name: Optional[str] = None

    def to_prompt_context(self) -> str:
        from relationshape.prompting import render_prompt_context

        return render_prompt_context(self)
