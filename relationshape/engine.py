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
from relationshape.perception import perceive
from relationshape.persistence import StateStore
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


class CompanionEngine:
    def __init__(
        self,
        identity: Optional[CharacterIdentity] = None,
        config: Optional[EngineConfig] = None,
    ) -> None:
        self.identity = identity or CharacterIdentity()
        self.config = config or EngineConfig()
        self.store = StateStore(self.config.state_dir)
        self._cache: dict[str, UserRelationState] = {}

    # ------------------------------------------------------------------ 状态

    def _state(self, user_id: str) -> UserRelationState:
        if user_id not in self._cache:
            self._cache[user_id] = self.store.load(user_id)
        return self._cache[user_id]

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
            st.pending = {"safety": True, "now": now.isoformat()}
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
        if is_session_start and not memories and frame.input_type == InputType.GREETING:
            highlight = st.memory.recent_highlight(now)
            if highlight:
                memories = [highlight]
        memories = memories[: self.config.max_recall]
        due = st.memory.due_promises(st.core.sessions)
        milestone = milestone_due(st.core, now, self.config)

        # ---- 学习新事实（供奖励与记忆确认）----
        learned = st.memory.extract_facts(text, frame.actors)
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
        for k, v in eq_notes.guidance.items():
            guide[k] = v if k not in guide else f"{v}；{guide[k]}"
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

        # 承诺只有真被指示提起时才计一次"已提醒"（共情轮不算，避免闲聊几轮就误判失约）
        promise_surfaced = bool(due) and frame.input_type not in (
            InputType.SELF_DISTRESS, InputType.SELF_BLAME, InputType.CHARACTER_REJECTION,
        )
        st.pending = {
            "safety": False,
            "frame": frame,
            "reading": reading,
            "humor": humor,
            "reward": reward,
            "reward_key": reward_key,
            "milestone": milestone,
            "due_pids": [p.pid for p in due],
            "promise_surfaced": promise_surfaced,
            "learned": learned,
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
            st.memory.add_episode(user_text, valence=-0.9, arousal=0.8, now=now, sensitive=True)
            record_substantive_turn(st.ledger, disclosure_depth=3)
            st.last_hook = None
            # 危机轮之后情绪惯性拉满：哪怕下一轮对方说"没事"，也不许开玩笑
            st.last_user_valence = -0.9
            st.core.last_seen = now.isoformat()
            self.store.save(st)
            return

        frame = pend.get("frame")
        reading = pend.get("reading")
        if frame is None or reading is None:
            # 无 prepare 的直接提交（如历史导入）：感知与语义事实在这里补做
            frame, reading = perceive(user_text)
            st.memory.extract_facts(user_text, frame.actors)
            st.adaptation.maybe_learn_address(user_text)

        # ---- 幽默学习：先看用户对上一轮幽默的反应，再登记本轮幽默 ----
        st.adaptation.react_to_pending_humor(user_text, st.turn_index)
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
        episode_types = (
            InputType.TOPIC, InputType.CREATIVE_TOPIC, InputType.EXTERNAL_COMPLAINT,
            InputType.GOOD_NEWS, InputType.SELF_DISTRESS, InputType.ASK_ADVICE,
        )
        if frame.substantive:
            if frame.input_type in episode_types:
                st.memory.add_episode(
                    user_text, reading.valence, reading.arousal, now,
                    vulnerability=frame.disclosure_depth,
                )
            record_substantive_turn(st.ledger, frame.disclosure_depth)
        st.last_user_valence = reading.valence

        # ---- 裂痕与修复 ----
        if frame.input_type == InputType.CHARACTER_ATTACK:
            record_rupture(st.ledger)
            st.adaptation.add_lesson("被攻击时站直但不升级：一个具体事实就够，不列清单")
        elif frame.input_type in (InputType.CHARACTER_REASSURANCE, InputType.CHARACTER_PRAISE):
            if st.ledger.ruptures_open > 0:
                record_repair(st.ledger)
        elif frame.input_type == InputType.CHARACTER_REJECTION:
            st.adaptation.add_lesson("对方想自己待着时，收住比追问好")

        # ---- 承诺生命周期 ----
        st.memory.detect_character_promise(assistant_text, now, st.core.sessions)
        due_pids = pend.get("due_pids", []) if pend.get("promise_surfaced") else []
        if due_pids:
            ab = zh.bigrams(assistant_text)
            for promise in st.memory.promises:
                if promise.pid not in due_pids or promise.status != "open":
                    continue
                if zh.jaccard(ab, zh.bigrams(promise.text)) > 0.12:
                    st.memory.mark_promise(promise.pid, "kept")
                    record_promise(st.ledger, kept=True)
                else:
                    promise.surfaced += 1
                    if promise.surfaced >= 3:
                        st.memory.mark_promise(promise.pid, "missed")
                        record_promise(st.ledger, kept=False)
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

        # ---- 钩子治理：只让用户的真实话题成为下一轮线头 ----
        if frame.input_type == InputType.CHARACTER_REJECTION:
            st.last_hook = None
        elif frame.substantive and frame.topic_tokens and frame.input_type in (
            InputType.TOPIC, InputType.CREATIVE_TOPIC, InputType.EXTERNAL_COMPLAINT,
            InputType.GOOD_NEWS, InputType.ASK_ADVICE,
        ):
            st.last_hook = "、".join(frame.topic_tokens[:2])

        # ---- 遗忘 ----
        st.memory.decay_and_prune(
            now, self.config.memory_half_life_days,
            self.config.memory_prune_threshold, self.config.episodic_cap,
        )

        # ---- 阶段推进 / 倒退 ----
        try_progress(st.core, st.ledger, st.adaptation.culture_size(), now, self.config)
        maybe_demote(st.core, st.ledger, self.config)

        st.core.last_seen = now.isoformat()
        self.store.save(st)

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
