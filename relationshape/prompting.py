"""提示词渲染：把 TurnDirective 翻成大模型能直接照做的结构化中文块。

引擎做决策，这里只做翻译。所有判断都已经在 directive 里，
模型的任务只是"把这些结构化要求说成人话"。
"""

from __future__ import annotations

from relationshape.types import Act, TurnDirective

_EMO_ZH = {
    "joy": "开心", "happy_for": "打心底替对方开心", "excited": "兴奋", "gratitude": "感激",
    "soothed": "被安抚到了，软下来", "proud": "小小的骄傲", "compassion": "心疼",
    "worry": "担心", "hurt": "受伤", "indignation": "不服气", "wistful": "有点失落",
    "frustration": "着急", "shyness": "害羞", "curious": "好奇", "calm": "平静",
}

_USER_EMO_ZH = {
    "sad": "难过", "angry": "生气", "annoyed": "烦躁", "anxious": "紧张/担心",
    "tired": "累", "happy": "开心", "content": "舒心", "bored": "无聊",
    "lonely": "孤单", "neutral": "平静", "warm": "温和", "distress": "痛苦",
}

_TARGET_ZH = {
    "user_self": "TA自己", "external_person": "外部的人/事", "character": "你",
    "device": "设备体验", "topic": "话题本身",
}

_ACT_ZH = {
    Act.REACT: "即时反应", Act.SOFT_REACT: "放轻地反应", Act.MIRROR: "复述对方的具体点",
    Act.VALIDATE: "确认感受合理", Act.PERSON_ANCHOR: "先锚定人物", Act.SCENE_GUESS: "带猜测进现场",
    Act.CAPITALIZE: "放大好消息的细节", Act.CARE: "关心", Act.PROTECT: "守护/反驳负面自评",
    Act.CURIOUS: "自然追问", Act.PLAYFUL: "轻微调皮", Act.SHY_ACCEPT: "害羞地收下夸奖",
    Act.STAND_GROUND: "守住自尊", Act.ACCEPT_COMFORT: "接住安抚", Act.WITHDRAW_SOFTLY: "温和收场",
    Act.CALLBACK: "回调内部梗", Act.REMEMBER: "引用共同记忆", Act.COMFORT_PRESENCE: "安静陪着",
    Act.ADVISE: "给建议", Act.ASK_PERMISSION_ADVISE: "先问要不要建议",
    Act.CELEBRATE_MILESTONE: "里程碑小庆祝", Act.GRATITUDE: "轻轻道谢",
    Act.WARM_CLOSE: "温暖收尾", Act.LOOKAHEAD_HOOK: "留个明天的小钩子",
    Act.ACKNOWLEDGE_TRUST: "郑重接住对方的信任", Act.REUNION_WARMTH: "重逢的暖",
    Act.HONEST_EXPLAIN: "诚实解释",
    Act.PERCEPTION_CHECK: "知觉检核", Act.NAME_FEELING: "替感受找词",
    Act.FANTASY_GRANT: "用想象满足愿望", Act.CONCEDE: "痛快认错被你说服",
}

_STYLE_HUMOR_ZH = {
    "affiliative": "亲和玩笑", "self_enhancing": "自嘲小糗事", "callback": "内部梗回调",
    "wordplay": "谐音/文字游戏", "playful_tease": "轻度打趣",
}


def render_prompt_context(d: TurnDirective) -> str:
    lines: list[str] = []

    # ---- 安全接管：极简输出，别的都不渲染 ----
    if d.safety is not None:
        lines.append("【安全接管】对方可能正在经历需要认真对待的事，本轮由安全规则接管。")
        lines.append("【回应方式】" + " → ".join(_ACT_ZH[a] for a in d.acts))
        for act in d.acts:
            g = d.act_guidance.get(act.value)
            if g:
                lines.append(f"  - {_ACT_ZH[act]}：{g}")
        if d.constraints:
            lines.append("【原则】" + "；".join(d.constraints))
        if d.forbidden:
            lines.append("【禁止】" + "；".join(d.forbidden))
        return "\n".join(lines)

    # ---- 关系与时间 ----
    seen = "今天刚见面" if d.is_session_start else "正在聊天中"
    lines.append(f"【关系】认识{d.days_known}天，第{d.session_index}次见面（{seen}）；阶段：{_stage_zh(d)}")
    if d.reunion_gap_days > 0:
        lines.append(f"【重逢】距上次见面已{d.reunion_gap_days}天：见到对方是真高兴，但零指责零愧疚（不说'怎么才来'）")

    # ---- 双方情绪 ----
    ce = d.character_emotion
    emo_txt = _EMO_ZH.get(ce.label, ce.label)
    if ce.secondary:
        emo_txt += f"，还有一点{_EMO_ZH.get(ce.secondary, ce.secondary)}"
    lines.append(f"【你此刻的情绪】{emo_txt}（{ce.cause}；可表达强度{ce.display_intensity:.0%}）")
    for note in ce.display_notes:
        lines.append(f"  - {note}")
    ue = d.user_emotion
    emo_line = (
        f"【对方的情绪】{_USER_EMO_ZH.get(ue.label, ue.label)}（指向：{_TARGET_ZH.get(ue.target.value, ue.target.value)}）"
    )
    if d.precise_emotion_word:
        emo_line += f"；更准的词是「{d.precise_emotion_word}」——复述时用它，别用泛泛的'难过'"
    lines.append(emo_line)

    # ---- 高情商层：元信息与确认深度 ----
    if d.metamessage:
        lines.append(f"【元信息】{d.metamessage}")
    if d.validation_hint:
        lines.append(f"【确认的深度】{d.validation_hint}")

    # ---- 回应形状 ----
    if d.acts:
        lines.append("【回应形状】" + " → ".join(_ACT_ZH[a] for a in d.acts))
        for act in d.acts:
            g = d.act_guidance.get(act.value)
            if g:
                lines.append(f"  - {_ACT_ZH[act]}：{g}")

    # ---- 记忆与承诺 ----
    if d.memories:
        lines.append("【记忆】")
        for m in d.memories:
            ago = f"（{m.days_ago}天前）" if m.days_ago > 0 else ""
            lines.append(f"  - {m.text}{ago}——{m.hint}")
    if d.due_promises:
        lines.append("【承诺】")
        for p in d.due_promises:
            lines.append(f"  - 你说过：「{p.text}」——{p.note}")
    if d.milestone:
        lines.append(f"【里程碑】{d.milestone}")

    # ---- 幽默与奖励 ----
    if d.humor:
        lines.append(
            f"【幽默】可以来一点{_STYLE_HUMOR_ZH.get(d.humor.style.value, d.humor.style.value)}"
            f"（素材：{d.humor.material}）：{d.humor.guidance}"
        )
    if d.reward:
        lines.append(f"【奖励】{d.reward.reason}：{d.reward.guidance}")

    # ---- 人格演进与风格 ----
    if d.persona_notes:
        lines.append("【与这位用户的磨合】" + "；".join(d.persona_notes))
    s = d.style
    style_bits = [
        f"语气{'随意' if s.formality < 0.45 else ('正式一点' if s.formality > 0.6 else '自然')}",
        f"能量{'高' if s.energy > 0.6 else ('低、放轻' if s.energy < 0.4 else '适中')}",
    ]
    if s.address_form:
        style_bits.append(f"称呼对方「{s.address_form}」")
    style_bits.extend(s.notes)
    lines.append("【风格】" + "；".join(style_bits))

    # ---- 约束与禁止 ----
    if d.constraints:
        lines.append("【约束】" + "；".join(d.constraints))
    if d.forbidden:
        lines.append("【禁止】")
        for f in d.forbidden:
            lines.append(f"  - {f}")

    return "\n".join(lines)


def _stage_zh(d: TurnDirective) -> str:
    from relationshape.relationship import stage_label

    return stage_label(d.stage)
