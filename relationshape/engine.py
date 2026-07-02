"""CompanionEngine：引擎编排。

每轮两步：
1. prepare_turn(user_id, text, now) → TurnDirective
   安全门 → 感知 → 评估/心境 → 表达调节 → 阶段策略 → 记忆召回
   → 动作规划 → 幽默 → 奖励 → 风格 → 渲染。
2. commit(user_id, user_text, assistant_text, now)
   记忆写入 → 信任账本 → 裂痕/修复 → 承诺生命周期 → 人格适应
   → 钩子治理 → 遗忘 → 阶段推进 → 持久化。

引擎自身从不调用大模型；时间永远由参数注入，便于测试与回放。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from relationshape import eq as eq_mod
from relationshape import safety as safety_mod
from relationshape import zh
from relationshape.acts import plan_acts
from relationshape.adaptation import AdaptationState
from relationshape.affect import appraise, apply_emotion_to_mood, mood_word, regulate
from relationshape.config import EngineConfig
from relationshape.humor import plan_humor
from relationshape.identity import CharacterIdentity
from relationshape.memory import is_memory_query as _memory_is_query
from relationshape.memory import clean_hook_tokens as _clean_hook_tokens
from relationshape.memory_port import MemoryPort
from relationshape.perception import perceive
from relationshape.persistence import build_store
from relationshape.relationship import (
    apply_absence,
    maybe_demote,
    milestone_due,
    policy_for,
    record_promise,
    record_repair,
    record_rupture,
    record_session,
    record_substantive_turn,
    try_progress,
)
from relationshape.reward import plan_reward
from relationshape.state import UserRelationState
from relationshape.types import (
    Act,
    CharacterEmotion,
    EmotionTarget,
    InputType,
    StyleParams,
    TurnDirective,
    UserEmotionReading,
)


# 可外发到远端记忆的轮次：仅用户生活内容（钩子防污染同款边界）
_FORWARD_TYPES = (
    InputType.TOPIC, InputType.CREATIVE_TOPIC, InputType.EXTERNAL_COMPLAINT,
    InputType.GOOD_NEWS, InputType.SELF_DISTRESS, InputType.ASK_ADVICE,
)


_STAGE_ZH = {
    "stranger": "陌生", "acquaintance": "相识", "familiar": "熟悉",
    "companion": "同伴", "confidant": "知己",
}


def _stage_zh(stage) -> str:
    return _STAGE_ZH.get(getattr(stage, "value", stage), str(stage))


_VAGUE_RECALL_RE = re.compile(
    r"(那件事|那个事儿?|上次(那|的|跟|说)|之前(那|的|说|聊)|以前(说|聊|讲)的"
    r"|跟你说过的那|好久前.{0,4}的那?件?事|还记得.{0,10}(吗|不|么))"
)


def _render_user_facts(f: dict) -> Optional[str]:
    """把结构化用户档案折成一行人话——朋友本就记得的你。空则不渲染。"""
    bits: list[str] = []
    if f.get("name"):
        bits.append(f"TA叫{f['name']}")
    if f.get("preferences"):
        bits.append("喜欢" + "、".join(f["preferences"]))
    if f.get("aversions"):
        bits.append("怕/讨厌" + "、".join(f["aversions"]))
    if f.get("people"):
        ppl = f["people"]
        # 按关系计数（"有几个朋友"），并带区分属性（"打篮球的朋友叫什么"）
        rel_count: dict = {}
        for _, r, _a in ppl:
            rel_count[r] = rel_count.get(r, 0) + 1
        count_str = "、".join(f"{c}个{r}" for r, c in rel_count.items() if c >= 2)
        listing = "、".join(f"{n}（{a+'的' if a else ''}{r}）" for n, r, a in ppl)
        bits.append(("身边的人：" + (f"共{count_str}；" if count_str else "") + listing))
    if f.get("cared"):
        bits.append("TA最在乎：" + "、".join(f["cared"]))
    if f.get("cat_prefs"):
        bits.append("喜欢的：" + "、".join(f"{c}是{i}" for c, i in f["cat_prefs"].items()))
    if f.get("cat_aversions"):
        bits.append("讨厌的：" + "、".join(f"{c}是{i}" for c, i in f["cat_aversions"].items()))
    return "；".join(bits) if bits else None


class CompanionEngine:
    def __init__(
        self,
        identity: Optional[CharacterIdentity] = None,
        config: Optional[EngineConfig] = None,
        memory_port: Optional[MemoryPort] = None,
        extractor=None,                            # 可选 LLM 抽取层（MemoryExtractorPort）；None=纯规则
        extractor_mode: str = "fallback",          # fallback=仅规则未命中时调；always=每个实质轮都调
        store=None,                                # 注入存储后端；None=按 config.state_backend 自动选择
    ) -> None:
        self.identity = identity or CharacterIdentity()
        self.config = config or EngineConfig()
        self.store = store or build_store(self.config)
        self.memory_port = memory_port            # None = 纯本地（默认行为不变）
        self.extractor = extractor                # None = 默认零依赖、纯规则抽取
        self.extractor_mode = extractor_mode
        self._cache: dict[str, UserRelationState] = {}

    # ------------------------------------------------------------------ 状态

    def _state(self, user_id: str) -> UserRelationState:
        if user_id not in self._cache:
            self._cache[user_id] = self.store.load(user_id)
        return self._cache[user_id]

    @staticmethod
    def _log_event(
        st: UserRelationState, now: datetime, kind: str, label: str, detail: str = "",
    ) -> None:
        """关系大事记：每个事件都钉在时间线上，并快照此刻的信任/亲密/阶段。

        时间线是关系真实历史的忠实记录——可视化的"动态"就长在这里。敏感内容
        （危机轮）只记一个不含内容的标记，与封存区同一条红线。
        """
        ev = {
            "t": now.isoformat(timespec="minutes"),
            "kind": kind,
            "label": label,
            "trust": round(st.ledger.trust, 1),
            "closeness": round(st.ledger.closeness, 1),
            "stage": st.core.stage.value,
            "session": st.core.sessions,
        }
        if detail:
            ev["detail"] = detail[:60]
        st.timeline.append(ev)
        st.timeline = st.timeline[-400:]

    def _llm_extract(self, st: UserRelationState, text: str, frame, rule_hit: bool) -> list[str]:
        """可选 LLM 抽取层：补齐规则漏掉的口语句式。仅实质轮、非问句时触发；
        fallback 模式只在规则未命中时调（省调用），结果经 apply_extracted 过门槛+子串接地落地。"""
        if self.extractor is None or not frame.substantive or _memory_is_query(text):
            return []
        if self.extractor_mode != "always" and rule_hit:
            return []
        try:
            facts = self.extractor.extract(text)
        except Exception:       # 抽取层任何异常都不影响主流程（退化为纯规则）
            return []
        if not facts or facts.is_empty():
            return []
        return st.memory.apply_extracted(facts, source_text=text)

    def close(self) -> None:
        """释放存储后端资源（如 PostgresStateStore 的连接池）。可安全多次调用。"""
        close = getattr(self.store, "close", None)
        if callable(close):
            close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _touch_session(self, st: UserRelationState, now: datetime) -> tuple[bool, int]:
        """会话切分与缺席处理。返回 (是否新会话, 距上次的间隔天数)。"""
        if not st.core.first_met:
            st.core.first_met = now.isoformat()
            st.core.sessions = 1
            return True, 0
        if not st.core.last_seen:
            return True, 0
        last = datetime.fromisoformat(st.core.last_seen)
        gap_minutes = (now - last).total_seconds() / 60.0
        if gap_minutes >= self.config.session_gap_minutes:
            gap_days = (now - last).total_seconds() / 86400.0
            st.core.sessions += 1
            st.session_reward_count = 0
            record_session(st.ledger)
            apply_absence(st.ledger, gap_days, self.config)
            return True, int(gap_days)
        return False, 0

    # ------------------------------------------------------------------ 准备

    def prepare_turn(self, user_id: str, text: str, now: Optional[datetime] = None) -> TurnDirective:
        now = now or datetime.now()
        st = self._state(user_id)
        first_meet = not st.core.first_met
        is_session_start, gap_days = self._touch_session(st, now)
        days_known = (now - datetime.fromisoformat(st.core.first_met)).days

        # 心境向人格基线回归（时间真实流逝的效果）
        st.mood.decay_toward(self.identity.baseline_mood(), now, self.config.mood_half_life_hours)

        directive = TurnDirective(
            user_id=user_id,
            stage=st.core.stage,
            days_known=days_known,
            session_index=st.core.sessions,
            is_session_start=is_session_start,
            reunion_gap_days=gap_days if (is_session_start and gap_days >= self.config.reunion_gap_days) else 0,
        )

        # ---- 安全门：命中即接管，人格全部让位 ----
        ruling = safety_mod.check(text)
        if ruling is not None:
            directive.safety = ruling
            directive.acts = list(safety_mod.SAFETY_ACTS)
            directive.act_guidance = dict(safety_mod.SAFETY_GUIDANCE)
            directive.forbidden = list(safety_mod.SAFETY_FORBIDDEN)
            directive.constraints = [self.config.crisis_guidance]
            directive.user_emotion = UserEmotionReading(
                label="distress", valence=-0.8, arousal=0.7,
                target=EmotionTarget.USER_SELF, confidence=0.9,
            )
            directive.character_emotion = CharacterEmotion(
                label="compassion", intensity=0.9, display_intensity=0.5,
                cause="对方正在经历不好的事，稳稳接住",
            )
            directive.mood = st.mood.snapshot()
            st.pending = {"safety": True, "now": now.isoformat(),
                          "first_meet": first_meet, "is_session_start": is_session_start,
                          "gap_days": gap_days}
            return directive

        # ---- 感知 ----
        frame, reading = perceive(text)

        # ---- 评估与心境 ----
        emo = appraise(frame, reading, self.identity, st.mood, st.core.stage)
        extra_forbidden = regulate(emo, frame, reading, st.core.stage)
        apply_emotion_to_mood(emo, st.mood)

        policy = policy_for(st.core.stage)

        # ---- 记忆召回 ----
        memories = st.memory.recall(text, now, self.config.max_recall, self.config.memory_half_life_days)
        memories += st.memory.recall_preferences(text)
        memories += st.memory.recall_semantic_facts(text)
        if is_session_start and not memories and frame.input_type == InputType.GREETING:
            highlight = st.memory.recent_highlight(now)
            if highlight:
                memories = [highlight]
        # 模糊回指兜底：问"那件事/上次/还记得…吗"但无具体词命中时，浮出最显著记忆
        if not memories and _VAGUE_RECALL_RE.search(text):
            memories = st.memory.most_salient(now, self.config.memory_half_life_days, k=3)
        memories = memories[: self.config.max_recall]

        # ---- 融合层：远端长期记忆（fail-open，危机轮已在上方短路不会到这里）----
        profile_summary = None
        if self.memory_port is not None:
            profile_summary, remote = self.memory_port.recall(
                user_id, text, now, want_profile=is_session_start,
            )
            if remote:
                seen_text = {m.text[:20] for m in memories}
                for r in remote:
                    if r.text[:20] not in seen_text:
                        memories.append(r)
                memories = memories[: self.config.max_recall + 2]   # 远端可多带2条
        due = st.memory.due_promises(st.core.sessions)
        milestone = milestone_due(st.core, now, self.config)

        # ---- 学习新事实（供奖励与记忆确认）----
        learned = st.memory.extract_facts(text, frame.actors)
        learned += self._llm_extract(st, text, frame, bool(learned))
        nick = st.adaptation.maybe_learn_address(text)
        if nick:
            learned.append(f"称呼：{nick}")

        # ---- 动作规划 ----
        acts, guide, constraints, forbidden = plan_acts(
            frame, reading, emo, st.core.stage, policy,
            memories, due, milestone, is_session_start,
            directive.reunion_gap_days > 0, st.last_hook,
        )
        for f in extra_forbidden:
            if f not in forbidden:
                forbidden.append(f)

        # ---- 高情商层（语言艺术收敛，见 docs/EQ_CANON.md）----
        eq_notes = eq_mod.enrich(
            text=text, frame=frame, reading=reading, stage=st.core.stage,
            closeness=st.ledger.closeness, memories=memories,
            last_user_valence=st.last_user_valence, planned_acts=acts,
        )
        acts = eq_notes.lead_acts + acts
        if eq_notes.insert_acts:
            pos = min(1, len(acts))
            acts[pos:pos] = eq_notes.insert_acts
        acts.extend(eq_notes.tail_acts)
        _seen: set = set()
        acts = [a for a in acts if not (a in _seen or _seen.add(a))]
        # 知觉检核优先于一切话题推进：孩子刚把心事咽回去，不许转头聊旧线头
        if Act.PERCEPTION_CHECK in acts:
            acts = [a for a in acts if a != Act.CURIOUS]
            guide.pop(Act.CURIOUS.value, None)
            constraints.append("这一轮不接旧话题线头、不开新话题——先把'没事'背后的人接住")
        for k, v in eq_notes.guidance.items():
            guide[k] = v if k not in guide else f"{v}；{guide[k]}"
        for k, v in eq_notes.guidance_override.items():
            guide[k] = v   # 语义冲突时整句替换（如感情之问：命名替代举证）
        for c in eq_notes.constraints:
            if c not in constraints:
                constraints.append(c)
        for f in eq_notes.forbidden:
            if f not in forbidden:
                forbidden.append(f)

        # ---- 幽默 ----
        humor = plan_humor(
            frame, reading, st.mood, st.core.stage, policy, st.adaptation,
            turns_since_humor=st.turn_index - st.last_humor_turn,
            cooldown=self.config.humor_cooldown_turns,
            aversion_tags=st.memory.aversions,
            context_low=st.last_user_valence < -0.3,
        )

        # ---- 奖励 ----
        reward, reward_key = plan_reward(
            frame, learned, milestone,
            turns_since_reward=st.turn_index - st.last_reward_turn,
            cooldown=self.config.reward_cooldown_turns,
            session_reward_count=st.session_reward_count,
            session_cap=self.config.reward_session_cap,
            recent_keys=st.recent_reward_keys,
        )

        # ---- 风格（沟通适应 + 心境 + 亲密度）----
        style = StyleParams(
            formality=st.adaptation.formality,
            energy=0.5 * st.adaptation.energy + 0.5 * max(0.0, st.mood.a),
            warmth=min(1.0, st.ledger.closeness / 60.0),
            address_form=st.adaptation.address_form if policy.nickname_allowed else None,
        )
        if reading.valence < -0.3:
            style.energy = min(style.energy, 0.35)
            style.notes.append("对方情绪低，整体放轻放慢")
        style.notes.append(f"你的心境底色：{mood_word(st.mood)}")

        # ---- 组装 ----
        directive.frame = frame
        directive.user_emotion = reading
        directive.character_emotion = emo
        directive.mood = st.mood.snapshot()
        directive.acts = acts
        directive.act_guidance = guide
        directive.constraints = constraints
        directive.forbidden = forbidden
        directive.humor = humor
        directive.reward = reward
        directive.memories = memories
        directive.due_promises = due
        directive.milestone = f"今天是你们认识第{milestone}天" if milestone else None
        directive.style = style
        directive.persona_notes = st.adaptation.persona_notes()
        directive.metamessage = eq_notes.metamessage
        directive.validation_hint = eq_notes.validation_hint
        directive.precise_emotion_word = eq_notes.precise_emotion_word
        directive.spoken_emotion_word = eq_notes.spoken_emotion_word

        # ---- 本体论身份层：每轮一行立场；身世轮注入全量设定与自述账本 ----
        directive.identity_line = f"{self.identity.name}——{self.identity.ontology_stance}"
        directive.profile_summary = profile_summary
        directive.user_facts = _render_user_facts(st.memory.user_profile_facts())
        directive.user_name = st.memory.user_name
        if frame.input_type == InputType.ONTOLOGY_QUESTION:
            directive.self_canon = list(self.identity.self_canon)
            directive.self_claims = list(st.adaptation.self_claims)
            # 给"关系是真的"配上具体弹药：一件最近的共同经历，胜过空泛断言
            if not directive.memories:
                hl = st.memory.recent_highlight(now)
                if hl:
                    directive.memories = [hl]
        elif st.adaptation.self_claims:
            # 人设矛盾多发生在日常轮（喜好/习惯被再次问起）：
            # 话题相关的旧自述按需注入，防"上周说不爱吃糖今天说最爱"（Yes-and 连续性）
            qb = zh.bigrams(text)
            hits = [
                c for c in st.adaptation.self_claims
                if zh.jaccard(qb, zh.bigrams(c)) >= 0.12
                or any(t in c for t in frame.topic_tokens if len(t) >= 2)
            ]
            if hits:
                directive.self_claims = hits[-4:]

        # 承诺只有真被指示提起时才计一次"已提醒"（共情轮不算，避免闲聊几轮就误判失约）
        promise_surfaced = bool(due) and frame.input_type not in (
            InputType.SELF_DISTRESS, InputType.SELF_BLAME, InputType.CHARACTER_REJECTION,
        )
        st.pending = {
            "safety": False,
            "first_meet": first_meet,
            "is_session_start": is_session_start,
            "gap_days": gap_days,
            "frame": frame,
            "reading": reading,
            "humor": humor,
            "reward": reward,
            "reward_key": reward_key,
            "milestone": milestone,
            "due_pids": [p.pid for p in due],
            "promise_surfaced": promise_surfaced,
            "learned": learned,
            "recalled": [
                {"text": m.text[:36], "kind": m.kind} for m in memories
            ] + ([{"text": "（人物档案摘要）", "kind": "profile"}] if profile_summary else []),
            "now": now.isoformat(),
        }
        return directive

    # ------------------------------------------------------------------ 提交

    def commit(
        self,
        user_id: str,
        user_text: str,
        assistant_text: str,
        now: Optional[datetime] = None,
    ) -> None:
        now = now or datetime.now()
        st = self._state(user_id)
        st.turn_index += 1
        pend = st.pending or {}
        st.pending = None

        # ---- 安全轮：封存记忆，深度信任记账，不走常规演进 ----
        if pend.get("safety"):
            st.traces.append({"t": now.isoformat(timespec="minutes"),
                              "text": "（危机轮，内容封存）", "itype": "safety", "recalled": []})
            st.traces = st.traces[-20:]
            st.memory.add_episode(user_text, valence=-0.9, arousal=0.8, now=now, sensitive=True)
            record_substantive_turn(st.ledger, disclosure_depth=3)
            # 危机时刻进时间线，但永不带内容——与封存区同一条红线
            self._log_event(st, now, "safety", "危机时刻 · 稳稳接住", "内容已封存，永不展示")
            st.last_hook = None
            # 危机轮之后情绪惯性拉满：哪怕下一轮对方说"没事"，也不许开玩笑
            st.last_user_valence = -0.9
            st.core.last_seen = now.isoformat()
            self.store.save(st)
            return

        frame = pend.get("frame")
        reading = pend.get("reading")
        direct_commit = frame is None or reading is None
        if direct_commit:
            # 无 prepare 的直接提交（如历史导入）：感知与语义事实在这里补做
            frame, reading = perceive(user_text)
        known_user_name = st.memory.user_name or ""
        if direct_commit:
            # 无 prepare（如历史导入）：这里首次抽取（规则 + 可选 LLM）
            learned_facts = st.memory.extract_facts(user_text, frame.actors)
            learned_facts += self._llm_extract(st, user_text, frame, bool(learned_facts))
        else:
            # 正常流：事实已在 prepare_turn 抽取并写入 st.memory，直接复用 pending，
            # 不再重复调用 extract_facts（既省一次全量正则，也避免与 pending 分叉）
            learned_facts = pend.get("learned", [])
        learned_user_name = any(fact.startswith("名字：") for fact in learned_facts)
        is_user_name_intro = bool(
            known_user_name
            and known_user_name in user_text
            and re.search(r"(我叫|我的名字(?:叫|是)|我是)", user_text)
        )
        st.adaptation.maybe_learn_address(user_text)

        # ---- 幽默学习：先看用户对上一轮幽默的反应，再登记本轮幽默 ----
        jokes_before = len(st.adaptation.inside_jokes)
        st.adaptation.react_to_pending_humor(user_text, st.turn_index)
        if len(st.adaptation.inside_jokes) > jokes_before:   # 一起笑过 → 诞生专属梗
            self._log_event(st, now, "joke", "诞生一个专属梗",
                            st.adaptation.inside_jokes[-1].label)
        humor = pend.get("humor")
        if humor is not None:
            st.adaptation.set_pending_humor(humor.style.value, humor.material, st.turn_index)
            st.last_humor_turn = st.turn_index

        # ---- 风格趋同与话题偏好 ----
        st.adaptation.observe_user_style(user_text)
        if frame.substantive:
            st.adaptation.observe_topics(frame.topic_tokens)

        # ---- 记忆与账本 ----
        # 情景记忆只收用户生活内容：对角色的攻击/夸奖/安抚进账本不进记忆，
        # 否则开场"惦记"的可能是一句"你真笨"；设备抱怨同理（钩子防污染）。
        # 问句是检索不是经历——不存为 episode、不生成 hook（生产日志：问句被当记忆）
        is_query = _memory_is_query(user_text)
        if frame.substantive and not is_query:
            if frame.input_type in _FORWARD_TYPES:
                st.memory.add_episode(
                    user_text, reading.valence, reading.arousal, now,
                    vulnerability=frame.disclosure_depth,
                )
            record_substantive_turn(st.ledger, frame.disclosure_depth)
            # 秘密级表露：社会渗透理论里的高价值时刻，单独钉在时间线上
            if frame.disclosure_depth >= 3:
                self._log_event(st, now, "disclosure", "一次很深的心里话",
                                "对方把藏着的脆弱说了出来")
        st.last_user_valence = reading.valence

        # ---- 自述账本：身世轮后登记角色的自我表述（连续性管理）----
        if frame.input_type == InputType.ONTOLOGY_QUESTION:
            st.adaptation.add_self_claims(assistant_text)

        # ---- 裂痕与修复 ----
        if frame.input_type == InputType.CHARACTER_ATTACK:
            record_rupture(st.ledger)
            self._log_event(st, now, "rupture", "出现裂痕", "一次冲突，信任受了点伤")
            st.adaptation.add_lesson("被攻击时站直但不升级：一个具体事实就够，不列清单")
        elif frame.input_type in (InputType.CHARACTER_REASSURANCE, InputType.CHARACTER_PRAISE):
            if st.ledger.ruptures_open > 0:
                record_repair(st.ledger)
                self._log_event(st, now, "repair", "裂痕修复", "和好了——吵过又和好的关系更结实")
        elif frame.input_type == InputType.CHARACTER_REJECTION:
            st.adaptation.add_lesson("对方想自己待着时，收住比追问好")

        # ---- 承诺生命周期 ----
        made = st.memory.detect_character_promise(assistant_text, now, st.core.sessions)
        if made is not None:
            self._log_event(st, now, "promise", "许下一个约定", made.text)
        due_pids = pend.get("due_pids", []) if pend.get("promise_surfaced") else []
        if due_pids:
            ab = zh.bigrams(assistant_text)
            for promise in st.memory.promises:
                if promise.pid not in due_pids or promise.status != "open":
                    continue
                if zh.jaccard(ab, zh.bigrams(promise.text)) > 0.12:
                    st.memory.mark_promise(promise.pid, "kept")
                    record_promise(st.ledger, kept=True)
                    self._log_event(st, now, "promise_kept", "兑现了约定", promise.text)
                else:
                    promise.surfaced += 1
                    if promise.surfaced >= 3:
                        st.memory.mark_promise(promise.pid, "missed")
                        record_promise(st.ledger, kept=False)
                        self._log_event(st, now, "promise_missed", "约定落空了", promise.text)
                        st.adaptation.add_lesson(f"没接住的承诺（{promise.text}）：下次少许诺、多兑现")

        # ---- 奖励与里程碑记账 ----
        if pend.get("reward") is not None:
            st.last_reward_turn = st.turn_index
            st.session_reward_count += 1
            key = pend.get("reward_key")
            if key:
                st.recent_reward_keys.append(key)
                st.recent_reward_keys = st.recent_reward_keys[-12:]
        milestone = pend.get("milestone")
        if milestone is not None and milestone not in st.core.milestones_done:
            st.core.milestones_done.append(milestone)
            self._log_event(st, now, "milestone", f"认识第 {milestone} 天", "一个值得一起记得的里程碑")

        # ---- 可观测性：每轮记忆调用痕迹（面板"会不会调记忆"的答案）----
        st.traces.append({
            "t": now.isoformat(timespec="minutes"),
            "text": user_text[:30],
            "itype": frame.input_type.value,
            "recalled": pend.get("recalled", []),
        })
        st.traces = st.traces[-20:]

        # ---- 钩子治理：只让用户的真实、正向话题成为下一轮线头 ----
        # content_runs 会切碎句子并吞掉否定词，故用本轮抽取出的"厌恶/撤回"作负向信号，
        # 配合第三方过滤，挡住"妈妈总喜欢给、煮面条"这类第三方碎片做开场（生产日志）。
        if frame.input_type == InputType.CHARACTER_REJECTION:
            st.last_hook = None
        elif (not is_query and not (learned_user_name or is_user_name_intro)
              and frame.substantive and frame.topic_tokens and frame.input_type in (
                  InputType.TOPIC, InputType.CREATIVE_TOPIC, InputType.EXTERNAL_COMPLAINT,
                  InputType.GOOD_NEWS, InputType.ASK_ADVICE,
              )):
            # 事实在 prepare_turn 抽取并存进 pending；commit 复抽时已无"新"事实，故优先用 pending。
            # 再叠加"本句提到的已知厌恶"——厌恶可能是早先轮学的，本轮不会重复学，但仍不该做正向线头。
            turn_learned = pend.get("learned") or learned_facts
            turn_negatives = [
                tail for f in turn_learned
                for head, _, tail in [f.partition("：")]
                if tail and ("讨厌" in head or "不喜欢" in head or "不再" in head)
            ] + [a for a in st.memory.aversions if a and a in user_text]
            cands = _clean_hook_tokens(user_text, turn_negatives)
            st.last_hook = "、".join(cands[:2]) if cands else None

        # ---- 遗忘 ----
        st.memory.decay_and_prune(
            now, self.config.memory_half_life_days,
            self.config.memory_prune_threshold, self.config.episodic_cap,
        )

        # ---- 阶段推进 / 倒退 ----
        # 先记本轮的"见面"节拍（首次相遇 / 久别重逢 / 普通见面），作为信任轨迹的采样点
        if pend.get("first_meet"):
            self._log_event(st, now, "meet", "初次相遇", "一段关系从这里开始")
        elif pend.get("is_session_start"):
            gap = int(pend.get("gap_days") or 0)
            if gap >= self.config.reunion_gap_days:
                self._log_event(st, now, "reunion", f"久别重逢 · 隔了 {gap} 天", "暖场，零指责")
            else:
                self._log_event(st, now, "session", f"第 {st.core.sessions} 次见面")

        promoted = try_progress(st.core, st.ledger, st.adaptation.culture_size(), now, self.config)
        if promoted is not None:
            self._log_event(st, now, "stage_up", f"关系进阶 → {_stage_zh(promoted)}",
                            "时间 × 互动 × 信任都到了")
        demoted = maybe_demote(st.core, st.ledger, self.config)
        if demoted is not None:
            self._log_event(st, now, "stage_down", f"关系降温 → {_stage_zh(demoted)}",
                            "未修复的裂痕累积了")

        st.core.last_seen = now.isoformat()
        self.store.save(st)

        # ---- 融合层：喂入远端抽取（尽力而为；危机轮在上方已 return，永不到达）----
        if self.memory_port is not None and frame.input_type in _FORWARD_TYPES:
            self.memory_port.observe(
                user_id, user_text, assistant_text,
                session_id=f"{user_id}-s{st.core.sessions}", now=now,
            )

    # ------------------------------------------------------------------ 工具

    def register_promise(self, user_id: str, text: str, now: Optional[datetime] = None) -> None:
        """上层管线显式登记一个角色承诺（比口头模式识别更可靠）。"""
        from relationshape.memory import Promise, _mid

        now = now or datetime.now()
        st = self._state(user_id)
        st.memory.promises.append(Promise(
            pid=_mid(text, now.isoformat()), text=text[:40], made_by="character",
            created_at=now.isoformat(), session_made=st.core.sessions,
        ))

    def snapshot(self, user_id: str) -> dict:
        """调试用：当前关系状态摘要。"""
        st = self._state(user_id)
        return {
            "stage": st.core.stage.value,
            "sessions": st.core.sessions,
            "trust": round(st.ledger.trust, 1),
            "closeness": round(st.ledger.closeness, 1),
            "substantive_turns": st.ledger.substantive_turns,
            "disclosures": st.ledger.disclosures,
            "deep_disclosures": st.ledger.deep_disclosures,
            "ruptures_open": st.ledger.ruptures_open,
            "ruptures_repaired": st.ledger.ruptures_repaired,
            "mood": st.mood.snapshot(),
            "culture": st.adaptation.culture_size(),
            "inside_jokes": [j.label for j in st.adaptation.inside_jokes],
            "preferences": st.memory.preferences,
            "promises": [(p.text, p.status) for p in st.memory.promises],
            "lessons": st.adaptation.lessons,
        }
