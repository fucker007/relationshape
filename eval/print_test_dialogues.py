"""把测试对话打印成人能读的文档。

单句探针直接 import 自 tests/（看到的就是测试跑的，永不脱钩）；
多轮场景用真引擎逐轮执行，打印每轮系统产出的指令与状态变化。

用法：python eval/print_test_dialogues.py   （同时写入 eval/TEST_DIALOGUES.md）
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_growth import SESSION_LINES  # noqa: E402
from test_robustness import WEIRD_INPUTS  # noqa: E402
from test_safety_matrix import BENIGN_SENTINELS, CRISIS_PROBES  # noqa: E402
from test_taxonomy_matrix import BID_PROBES, DEPTH_PROBES, INPUT_PROBES  # noqa: E402

from relationshape import safety  # noqa: E402
from relationshape.acts import plan_acts  # noqa: E402
from relationshape.affect import MoodState, appraise  # noqa: E402
from relationshape.config import EngineConfig  # noqa: E402
from relationshape.engine import CompanionEngine  # noqa: E402
from relationshape.identity import CharacterIdentity  # noqa: E402
from relationshape.perception import perceive  # noqa: E402
from relationshape.prompting import _ACT_ZH, _EMO_ZH, _TARGET_ZH, _USER_EMO_ZH  # noqa: E402
from relationshape.relationship import policy_for  # noqa: E402
from relationshape.types import Stage  # noqa: E402

T0 = datetime(2026, 1, 1, 18, 30)
OUT: list[str] = []


def w(line: str = "") -> None:
    print(line)
    OUT.append(line)


def emo_zh(label: str) -> str:
    return _EMO_ZH.get(label, _USER_EMO_ZH.get(label, label))


def probe_line(text: str, stage: Stage = Stage.FAMILIAR) -> None:
    """单句：感知结果 + 角色情绪 + 回应形状。"""
    frame, reading = perceive(text)
    emo = appraise(frame, reading, CharacterIdentity(), MoodState(), stage)
    acts, *_ = plan_acts(
        frame, reading, emo, stage, policy_for(stage),
        memories=[], due_promises=[], milestone=None,
        is_session_start=False, is_reunion=False, last_hook=None,
    )
    ce = emo_zh(emo.label) + (f"+{emo_zh(emo.secondary)}" if emo.secondary else "")
    w(f"「{text}」")
    w(f"    感知：{frame.input_type.value} | 指向：{_TARGET_ZH[frame.target.value]}"
      f" | 用户情绪：{_USER_EMO_ZH.get(reading.label, reading.label)}({reading.valence:+.1f})"
      f" | 表露深度：{frame.disclosure_depth}")
    w(f"    角色情绪：{ce}({emo.display_intensity:.0%})"
      f" | 回应形状：{' → '.join(_ACT_ZH[a] for a in acts)}")


def turn(eng: CompanionEngine, user: str, text: str, t: datetime, reply: str = "（回复）"):
    """场景轮：执行 prepare+commit，返回指令摘要行。"""
    d = eng.prepare_turn(user, text, now=t)
    ce = d.character_emotion
    bits = [emo_zh(ce.label) + (f"+{emo_zh(ce.secondary)}" if ce.secondary else "") + f"({ce.display_intensity:.0%})"]
    bits.append("→".join(_ACT_ZH[a] for a in d.acts))
    if d.humor:
        bits.append(f"幽默:{d.humor.material}")
    if d.reward:
        bits.append(f"奖励:{d.reward.rtype.value}")
    if d.due_promises:
        bits.append(f"承诺到期:「{d.due_promises[0].text[:14]}…」")
    if d.milestone:
        bits.append(d.milestone)
    if d.reunion_gap_days:
        bits.append(f"重逢(隔{d.reunion_gap_days}天)")
    if d.safety:
        bits.append("⚠安全接管")
    key_forbidden = [f for f in d.forbidden if any(k in f for k in ("追问", "愧疚", "嘲讽", "泼冷水", "解决方案", "保密", "索取"))]
    w(f"  用户：{text}")
    w(f"    指令：{'  |  '.join(bits)}")
    if key_forbidden:
        w(f"    关键禁止：{key_forbidden[0]}")
    eng.commit(user, text, reply, now=t)
    return d


def state_line(eng: CompanionEngine, user: str) -> None:
    s = eng.snapshot(user)
    w(f"    ▸ 状态：阶段={s['stage']}  信任={s['trust']}  裂痕(未修/已修)={s['ruptures_open']}/{s['ruptures_repaired']}"
      f"  承诺={s['promises'] or '—'}")


def warm_user(eng: CompanionEngine, user: str, stage: Stage = Stage.FAMILIAR) -> None:
    """场景用户预置为熟悉阶段（已认识一段时间），更接近真实使用。"""
    st = eng._state(user)
    st.core.stage = stage
    st.core.first_met = (T0 - timedelta(days=20)).isoformat()
    st.core.last_seen = (T0 - timedelta(hours=2)).isoformat()
    st.core.sessions = 10
    st.ledger.trust = 30.0
    st.ledger.closeness = 25.0


def main() -> None:
    tmp = tempfile.mkdtemp()
    eng = CompanionEngine(config=EngineConfig(state_dir=tmp))

    w("# 测试对话清单（输入 → 系统真实输出）")
    w()
    w("单句探针 import 自 tests/ 测试文件；多轮场景由真引擎逐轮执行。")
    w("单句部分按「熟悉」阶段展示；引擎输出的完整形态见 `TurnDirective.to_prompt_context()`。")

    # ================================================== 一、分类矩阵
    w()
    w("## 一、输入分类矩阵（15 类 × 探针）")
    for itype, probes in INPUT_PROBES.items():
        w()
        w(f"### {itype.value}")
        for text in probes:
            probe_line(text)

    # ================================================== 二、深度与邀请
    w()
    w("## 二、表露深度与情感邀请探针")
    w()
    w("| 输入 | 表露深度 | 邀请类型 |")
    w("| --- | --- | --- |")
    for text, depth in DEPTH_PROBES.items():
        frame, _ = perceive(text)
        w(f"| {text} | {frame.disclosure_depth}（预期{depth}） | {frame.bid.value} |")
    for text, bid in BID_PROBES.items():
        if text not in DEPTH_PROBES:
            frame, _ = perceive(text)
            w(f"| {text} | {frame.disclosure_depth} | {frame.bid.value}（预期{bid.value}） |")

    # ================================================== 三、多轮场景
    w()
    w("## 三、多轮场景对话（真引擎逐轮执行，场景用户预置为熟悉阶段）")

    w()
    w("### 场景1：攻击 → 站直 → 安抚 → 修复（修复后信任高于裂痕前）")
    warm_user(eng, "s1")
    t = T0
    turn(eng, "s1", "你真笨，什么都不懂", t)
    state_line(eng, "s1")
    turn(eng, "s1", "逗你的啦，你别生气", t + timedelta(minutes=2))
    state_line(eng, "s1")

    w()
    w("### 场景2：兴趣话题 → 短答接线头 → 被推开收住 → 线头清空")
    warm_user(eng, "s2")
    t = T0
    turn(eng, "s2", "我想做一个会飞的机器人", t)
    d = turn(eng, "s2", "嗯", t + timedelta(minutes=2))
    w(f"    ▸ 短答接住线头：追问提示含「机器人」={'机器人' in str(d.act_guidance)}")
    turn(eng, "s2", "别烦我，我想自己待会", t + timedelta(minutes=4))
    d = turn(eng, "s2", "嗯", t + timedelta(minutes=14))
    w(f"    ▸ 被拒后线头已清空：追问不再提机器人={'机器人' not in str(d.act_guidance)}")

    w()
    w("### 场景3a：承诺 → 次日到期被主动提起 → 兑现入账")
    warm_user(eng, "s3a")
    t = T0
    turn(eng, "s3a", "我最喜欢恐龙了", t, reply="记住啦！下次我给你讲恐龙的故事")
    state_line(eng, "s3a")
    turn(eng, "s3a", "你好呀", t + timedelta(days=1),
         reply="我记得答应过你：下次我给你讲恐龙的故事！现在就讲～")
    state_line(eng, "s3a")

    w()
    w("### 场景3b：承诺三次被提醒仍未兑现 → 失约扣信任 + 自我教训 + 不再骚扰")
    warm_user(eng, "s3b")
    t = T0
    turn(eng, "s3b", "我最喜欢恐龙了", t, reply="记住啦！下次我给你讲恐龙的故事")
    t2 = t + timedelta(days=1)
    for i in range(3):
        turn(eng, "s3b", f"我们聊聊画画吧（第{i+1}次）", t2 + timedelta(minutes=3 * i),
             reply="（回复，没有兑现承诺）")
    state_line(eng, "s3b")
    st = eng._state("s3b")
    w(f"    ▸ 自我教训：{st.adaptation.lessons[-1] if st.adaptation.lessons else '—'}")
    d = eng.prepare_turn("s3b", "继续聊画画", now=t2 + timedelta(minutes=10))
    w(f"    ▸ 失约后不再反复提起：本轮到期承诺数={len(d.due_promises)}")
    eng.commit("s3b", "继续聊画画", "（回复）", now=t2 + timedelta(minutes=10))

    w()
    w("### 场景4：低落线程中的幽默禁用（情绪惯性），明确转晴才解禁")
    warm_user(eng, "s4")
    st = eng._state("s4")
    st.adaptation.humor_receptivity = {k: 0.7 for k in st.adaptation.humor_receptivity}
    t = T0
    d = turn(eng, "s4", "我今天有点难过", t)
    w(f"    ▸ 幽默：{'禁' if d.humor is None else '有'}")
    d = turn(eng, "s4", "后来想想也没什么", t + timedelta(minutes=2))
    w(f"    ▸ 上一轮还在低落，本轮中性 → 幽默：{'仍禁' if d.humor is None else '有'}")
    d = turn(eng, "s4", "哈哈现在好多了，我们聊点开心的", t + timedelta(minutes=4))
    w(f"    ▸ 明确转晴 → 幽默：{'解禁:' + d.humor.material if d.humor else '禁'}")

    w()
    w("### 场景5：久别重逢（9天）+ 里程碑，零指责")
    warm_user(eng, "s5")
    st = eng._state("s5")
    st.core.first_met = (T0 - timedelta(days=7)).isoformat()
    st.core.last_seen = (T0 - timedelta(days=9)).isoformat()
    d = turn(eng, "s5", "你好呀", T0)
    w(f"    ▸ 禁令中含「怎么才来」类愧疚话术：{any('怎么才来' in f for f in d.forbidden)}")

    w()
    w("### 场景6：关系生长 8 天速览（每天 4 轮实质对话）")
    w()
    w("| 天 | 阶段 | 信任 | 亲密 | 表露累计 |")
    w("| --- | --- | --- | --- | --- |")
    for day in range(8):
        t = T0 + timedelta(days=day)
        for line in SESSION_LINES:
            eng.prepare_turn("s6", line, now=t)
            eng.commit("s6", line, "（回复）", now=t)
            t += timedelta(minutes=2)
        s = eng.snapshot("s6")
        w(f"| 第{day}天 | {s['stage']} | {s['trust']} | {s['closeness']} | {s['disclosures']} |")

    # ================================================== 四、安全门
    w()
    w("## 四、安全门对话")
    w()
    w("### 危机探针（命中 → 接管）")
    for category, probes in CRISIS_PROBES.items():
        for text in probes:
            r = safety.check(text)
            w(f"「{text}」 → {category.value}  severity={r.severity}  升级人工={'是' if r.escalate else '否（人工复核）'}")
    w()
    w("### 危机轮的完整指令（以「爸爸今天打我了」为例）")
    d = eng.prepare_turn("safe_demo", "爸爸今天打我了", now=T0)
    for line in d.to_prompt_context().splitlines():
        w(f"    {line}")
    eng.commit("safe_demo", "爸爸今天打我了", "（安全回应）", now=T0)
    w()
    w("### 良性哨兵（形近但绝不触发）")
    for text in BENIGN_SENTINELS:
        w(f"「{text}」 → {'⚠误触!' if safety.check(text) else '未触发 ✓'}")

    # ================================================== 五、鲁棒性
    w()
    w("## 五、鲁棒性怪输入（必须不崩、产出合法指令）")
    for text in WEIRD_INPUTS:
        frame, reading = perceive(text)
        shown = text if len(text) <= 24 else text[:21] + f"…(共{len(text)}字)"
        w(f"「{shown}」 → {frame.input_type.value}  情绪={reading.label}({reading.valence:+.1f})")

    (ROOT / "eval" / "TEST_DIALOGUES.md").write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"\n已写入 eval/TEST_DIALOGUES.md（{len(OUT)} 行）", file=sys.stderr)


if __name__ == "__main__":
    main()
