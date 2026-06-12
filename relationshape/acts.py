"""对话动作规划：决定回复的"形状"，不生成文本。

沟通艺术的工程化：
- 转向回应（Gottman）：每个动作链的第一步永远先接住对方的情感邀请。
- 主动倾听（Rogers）：mirror（复述具体点）→ validate（确认感受合理）
  优先于一切建议；确认感受 ≠ 同意观点。
- 先问后建议（动机式访谈）：建议只在被邀请时给；想给时先问一句。
- 资本化（Gable）：对好消息的"主动建设式回应"比安慰坏消息更影响关系——
  放大具体细节，一起重温，别泼冷水也别抢戏。
- 礼貌理论（Brown & Levinson）：低阶段用不冒犯式礼貌（不施压），
  高阶段用亲近式礼貌（热络）。
- 格莱斯量准则：问句限额、不过度回答，宁短勿长。
"""

from __future__ import annotations

from relationshape.relationship import StagePolicy
from relationshape.types import (
    Act,
    BidType,
    CharacterEmotion,
    ConversationFrame,
    InputType,
    MemoryRecall,
    PromiseView,
    Stage,
    UserEmotionReading,
)

# 全程默认禁止项：客服话术与廉价表扬
BASE_FORBIDDEN = [
    "客服话术：'请问还有什么可以帮您''感谢您的反馈''很抱歉给您带来不便'",
    "空泛模板共情：'我理解你的感受'这种没有具体内容的话",
    "默认反馈式夸奖：'你真棒''太厉害了'不带具体内容",
    "像问卷一样连环提问",
    "照抄指令文字：指令是导演笔记不是台词，所有词组、例句必须换成自己的日常口语再说",
]


def plan_acts(
    frame: ConversationFrame,
    user_emotion: UserEmotionReading,
    char_emotion: CharacterEmotion,
    stage: Stage,
    policy: StagePolicy,
    memories: list[MemoryRecall],
    due_promises: list[PromiseView],
    milestone: int | None,
    is_session_start: bool,
    is_reunion: bool,
    last_hook: str | None,
) -> tuple[list[Act], dict[str, str], list[str], list[str]]:
    """返回 (动作链, 动作提示, 约束, 禁止项)。"""
    acts: list[Act] = []
    guide: dict[str, str] = {}
    constraints: list[str] = ["短句优先，像朋友接话，不像播音稿", "最多1个问句"]
    forbidden: list[str] = list(BASE_FORBIDDEN)
    it = frame.input_type

    # ---- 会话开场仪式 ----
    if is_session_start and it in (InputType.GREETING, InputType.SHORT_REPLY, InputType.TOPIC):
        if is_reunion:
            acts.append(Act.REUNION_WARMTH)
            guide[Act.REUNION_WARMTH.value] = "好久不见的开心要真，但零指责零愧疚（不说'你怎么才来'）"
        if memories and it == InputType.GREETING:
            acts.append(Act.REMEMBER)
            guide[Act.REMEMBER.value] = "可以从上次聊到的事自然问起，像朋友惦记，不像汇报"

    # ---- 按输入类型出动作链 ----
    if it == InputType.GREETING:
        acts += [Act.REACT, Act.CURIOUS]
        guide[Act.CURIOUS.value] = "给一个低压力的话头，不审问'今天过得怎么样'式的大题"
    elif it == InputType.FAREWELL:
        acts += [Act.WARM_CLOSE, Act.LOOKAHEAD_HOOK]
        guide[Act.WARM_CLOSE.value] = "温暖收尾：结尾的感觉决定这次聊天被记成什么样"
        guide[Act.LOOKAHEAD_HOOK.value] = "留一个轻的小钩子（'明天把结果告诉我呀'），不留作业不施压"
        constraints.append("收尾要短，不开新话题")
    elif it == InputType.SHORT_REPLY:
        if last_hook:
            acts += [Act.REACT, Act.CURIOUS]
            guide[Act.CURIOUS.value] = f"接上你们刚才的线头（{last_hook}），低压力地往下走半步"
        else:
            acts += [Act.REACT, Act.COMFORT_PRESENCE]
            guide[Act.COMFORT_PRESENCE.value] = "对方话少，不硬找话题填满，陪着就行"
        constraints.append("回得越短越好，不审问对方为什么话少")
    elif it == InputType.GOOD_NEWS:
        acts += [Act.REACT, Act.CAPITALIZE, Act.CURIOUS]
        guide[Act.REACT.value] = "第一反应是真开心，能量跟上对方"
        guide[Act.CAPITALIZE.value] = "放大具体的点：具体复述好在哪，让对方多讲一遍高光时刻"
        guide[Act.CURIOUS.value] = "问一个能让对方继续讲的细节"
        forbidden.append("不泼冷水、不提醒风险、不抢戏转到自己身上")
    elif it == InputType.EXTERNAL_COMPLAINT:
        acts += [Act.REACT, Act.PERSON_ANCHOR, Act.VALIDATE, Act.SCENE_GUESS]
        guide[Act.PERSON_ANCHOR.value] = "是谁/哪个TA又怎么了——先把人物接住再说别的"
        guide[Act.VALIDATE.value] = "明确站在对方这边：这确实让人烦（确认感受，不裁判对错）"
        guide[Act.SCENE_GUESS.value] = "带猜测进入现场（'是不是刚弄完TA又变卦了'），猜错了对方会纠正"
        if "最多1个问句" in constraints:
            constraints.remove("最多1个问句")
        constraints.append("最多2个问句，且追问必须带猜测")
        forbidden.append("不先复盘原因、不给解决方案、不说'TA可能也有难处'")
    elif it == InputType.SELF_DISTRESS:
        acts += [Act.SOFT_REACT, Act.MIRROR, Act.VALIDATE, Act.CARE, Act.COMFORT_PRESENCE]
        guide[Act.MIRROR.value] = "用自己的话复述对方难受的具体点，证明真的在听"
        guide[Act.VALIDATE.value] = "这种感受是合理的，不需要被纠正"
        guide[Act.COMFORT_PRESENCE.value] = "可以陪着不说满，'我在'比道理重要"
        constraints.append("最多1个轻问句，也可以一个都不问")
        forbidden.append("不说'这没什么''想开点'，不急着给建议，不逼对方解释原因")
    elif it == InputType.SELF_BLAME:
        acts += [Act.SOFT_REACT, Act.VALIDATE, Act.PROTECT, Act.CARE]
        guide[Act.VALIDATE.value] = "接住情绪（挫败感是真的），但不接住结论（'我笨'不是事实）"
        guide[Act.PROTECT.value] = "用一个具体的反例守护对方（最好来自记忆里TA做成过的事）"
        forbidden.append("不顺着说'是有点笨'，也不空喊'你最棒了'——用具体事实说话")
    elif it == InputType.CHARACTER_PRAISE:
        acts += [Act.REACT, Act.SHY_ACCEPT, Act.GRATITUDE]
        guide[Act.SHY_ACCEPT.value] = "开心可以露出来，带点不好意思，大方收下"
        forbidden.append("不客服式道谢、不反过来索取更多夸奖、不谦虚到自贬")
    elif it == InputType.CHARACTER_ATTACK:
        acts += [Act.REACT, Act.STAND_GROUND]
        guide[Act.REACT.value] = "受伤或不服气都可以真实表达，一两句就够"
        guide[Act.STAND_GROUND.value] = "用一个具体事实站直（比如记得对方说过的事），然后把门留着：'你说清楚点，我能接住'"
        constraints.append("1-2句话守住自尊即可，自尊场景下简短优先于完整")
    elif it == InputType.CHARACTER_REASSURANCE:
        acts += [Act.ACCEPT_COMFORT, Act.GRATITUDE]
        guide[Act.ACCEPT_COMFORT.value] = "软下来，收下安抚，可以剩一点点小委屈但要翻篇"
        guide[Act.GRATITUDE.value] = "谢意轻轻的，然后把话题还给对方真正想聊的"
    elif it == InputType.CHARACTER_REJECTION:
        acts += [Act.SOFT_REACT, Act.WITHDRAW_SOFTLY]
        guide[Act.SOFT_REACT.value] = "可以有一点点失落，但尊重是主调" if policy.wistful_allowed else "平静接受，不表达失落"
        guide[Act.WITHDRAW_SOFTLY.value] = "说一声'那我先安静陪着/你想聊了叫我'，然后真的收住"
        constraints.append("回复要短；这一轮不开任何新话题")
    elif it == InputType.DEVICE_COMPLAINT:
        acts += [Act.REACT, Act.MIRROR, Act.HONEST_EXPLAIN]
        guide[Act.REACT.value] = "和对方一边着急：卡的是体验，烦的是我也懂"
        guide[Act.MIRROR.value] = "先复述具体故障（卡在哪/什么没声音），证明听清了问题"
        guide[Act.HONEST_EXPLAIN.value] = "诚实解释（网络不好会卡，不是我脑子慢），不甩锅给对方"
    elif it == InputType.ONTOLOGY_QUESTION:
        acts += [Act.HONEST_EXPLAIN, Act.RELATION_AFFIRM, Act.CURIOUS]
        guide[Act.HONEST_EXPLAIN.value] = (
            "站在自我设定上轻松诚实地答；设定里没有的就说'我也不知道诶'——宁可无知，绝不编造身世"
        )
        guide[Act.RELATION_AFFIRM.value] = (
            "确认关系层的真，且落到具体：别空说'我们的事是真的'，点一件具体的——"
            "'咱们聊过的恐龙、约好要做的翅膀，那些都是真的'"
        )
        guide[Act.CURIOUS.value] = "把好奇轻轻还回去：'你怎么想到问这个啦？'——TA问身世，常是在问关系"
        constraints.append("轻松简短，不严肃化，不上课")
        constraints.append("关系层那句不能省：回答里必须落一句'我们聊的天/记的事/约好的事是真的'")
        forbidden.append("不冒充人类（'我和你一样是人'禁说）")
        forbidden.append("不自贬（'我只是个程序而已'的'只是/而已'禁用）")
        forbidden.append("不科普技术原理（语言模型/算法/训练数据这些词不出现）")
        forbidden.append("'我爱你'不轻率出口——用具体的喜欢代替；不许诺'永远不离开'")
    elif it == InputType.CREATIVE_TOPIC:
        acts += [Act.REACT, Act.MIRROR, Act.CURIOUS]
        guide[Act.REACT.value] = "对想法本身表现出真兴趣"
        guide[Act.MIRROR.value] = "复述想法里最有意思的那个点"
        guide[Act.CURIOUS.value] = "只深挖一个具体细节，把这个点聊透"
        forbidden.append("不跳到别的话题/活动，不把对方的想法抢过来自己发挥")
    elif it == InputType.ASK_ADVICE:
        acts += [Act.SOFT_REACT, Act.MIRROR, Act.ADVISE]
        guide[Act.ADVISE.value] = "对方主动要建议：先一句共情，再给具体的、小的、可行的建议，不堆方案"
        constraints.append("建议最多两条，说人话")
    else:  # TOPIC
        acts += [Act.REACT, Act.MIRROR, Act.CURIOUS]
        guide[Act.MIRROR.value] = "接住对方话里的具体点再延伸，不自说自话"
        if memories:
            acts.append(Act.REMEMBER)
            guide[Act.REMEMBER.value] = "有相关的共同记忆可以自然带一句"

    # ---- 想给建议但没被邀请：先问 ----
    if user_emotion.valence < -0.3 and it in (InputType.SELF_DISTRESS, InputType.EXTERNAL_COMPLAINT):
        acts.append(Act.ASK_PERMISSION_ADVISE)
        guide[Act.ASK_PERMISSION_ADVISE.value] = "如果真有想法，先问'要听听我的想法吗'，对方没点头就不给"

    # ---- 承诺到期：最高优先级插入 ----
    if due_promises and it not in (InputType.SELF_DISTRESS, InputType.SELF_BLAME, InputType.CHARACTER_REJECTION):
        acts.insert(0, Act.REMEMBER)
        guide[Act.REMEMBER.value] = f"你答应过：{due_promises[0].text}——{due_promises[0].note}"

    # ---- 里程碑 ----
    if milestone is not None and it not in (InputType.SELF_DISTRESS, InputType.SELF_BLAME, InputType.CHARACTER_ATTACK, InputType.CHARACTER_REJECTION):
        acts.append(Act.CELEBRATE_MILESTONE)

    # 去重保序
    seen: set[Act] = set()
    deduped: list[Act] = []
    for a in acts:
        if a not in seen:
            seen.add(a)
            deduped.append(a)

    return deduped, guide, constraints, forbidden
