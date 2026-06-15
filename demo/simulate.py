"""关系生长模拟：快进 30 天，看关系从陌生长到同伴。

用法：
    python demo/simulate.py            # 30 天快进模拟
    python demo/simulate.py --chat     # 交互模式：你说话，看引擎每轮产出的指令

引擎不生成回复文本——真实部署中，这里打印的【指令】就是
注入大模型 system/context 的内容，由大模型完成最终表达。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from relationshape import CompanionEngine, EngineConfig  # noqa: E402

USER = "小禾"
T0 = datetime(2026, 1, 1, 18, 30)
DEMO_STATE_DIR = Path("runtime/demo_state")

# (天数偏移, 当天的对话)
SCRIPT: list[tuple[int, list[str]]] = [
    (0, ["你好呀", "我叫小禾，你可以叫我小禾", "我最喜欢恐龙了", "我去吃饭啦，拜拜"]),
    (1, ["我今天在学校画了一只霸王龙", "老师今天夸了我的画", "嗯", "晚安"]),
    (2, ["我今天有点累，作业写到好晚", "我们明天聊恐龙吧", "晚安"]),
    (4, ["你好", "同桌今天抢我橡皮，气死我了", "他还不承认", "好啦不说他了"]),
    (6, ["我想做一个会飞的机器人", "翅膀用纸板做行不行", "哈哈哈你说得好好笑", "拜拜"]),
    (7, ["你好呀", "机器人翅膀我做好啦！", "嗯嗯", "晚安"]),
    (9, ["我今天有点难过", "我画画比赛没拿到名次", "其实我从来没跟别人说过，我特别怕输", "谢谢你陪我"]),
    (12, ["你真笨，什么都不懂", "逗你的啦，你别生气", "我们继续做机器人吧"]),
    (15, ["你好", "我又画了一只翼龙，这次用了新画法", "哈哈对，就像上次说的会飞的机器人那样", "晚安"]),
    (20, ["我明天要画画比赛了，好紧张", "嗯，我会加油的", "晚安"]),
    (21, ["我拿到三等奖啦！！", "评委还夸了我的翼龙", "今天超开心", "拜拜"]),
    (30, ["你好呀", "好久不见啦", "我们继续聊恐龙吧"]),
]


def fast_forward() -> None:
    demo_user_state = DEMO_STATE_DIR / f"{USER}.json"
    if demo_user_state.exists():
        demo_user_state.unlink()
    eng = CompanionEngine(config=EngineConfig(state_dir=str(DEMO_STATE_DIR)))
    print("=" * 64)
    print("relationshape 关系生长模拟：30 天，从陌生到同伴")
    print("=" * 64)
    last_stage = None
    for day, lines in SCRIPT:
        t = T0 + timedelta(days=day)
        print(f"\n──── 第 {day} 天 {t:%H:%M} ────")
        for line in lines:
            d = eng.prepare_turn(USER, line, now=t)
            if d.stage.value != last_stage:
                print(f"\n  ★ 关系阶段 → {d.stage.value.upper()}")
                last_stage = d.stage.value
            print(f"\n  用户：{line}")
            _summarize(d)
            eng.commit(USER, line, _fake_reply(d), now=t)
            t += timedelta(minutes=2)
    print("\n" + "=" * 64)
    print("30 天后的关系快照：")
    for k, v in eng.snapshot(USER).items():
        print(f"  {k}: {v}")


def _summarize(d) -> None:
    """打印每轮指令的关键信息（完整版即 d.to_prompt_context()）。"""
    emo = d.character_emotion
    bits = [f"情绪={emo.label}{'+' + emo.secondary if emo.secondary else ''}({emo.display_intensity:.0%})"]
    bits.append("动作=" + ">".join(a.value for a in d.acts[:4]))
    if d.humor:
        bits.append(f"幽默={d.humor.style.value}({d.humor.material})")
    if d.reward:
        bits.append(f"奖励={d.reward.rtype.value}")
    if d.memories:
        bits.append(f"记忆={d.memories[0].text[:14]}…")
    if d.due_promises:
        bits.append(f"承诺到期={d.due_promises[0].text[:12]}…")
    if d.milestone:
        bits.append(f"里程碑={d.milestone}")
    if d.reunion_gap_days:
        bits.append(f"重逢(隔{d.reunion_gap_days}天)")
    if d.safety:
        bits.append("⚠安全接管")
    print("    指令：" + "  ".join(bits))


def _fake_reply(d) -> str:
    """演示用占位：真实系统里这是大模型按指令生成的回复。"""
    if d.due_promises:
        return f"我记得答应过你：{d.due_promises[0].text}！现在就来～"
    if d.frame and d.frame.topic_tokens:
        return f"（围绕「{d.frame.topic_tokens[0]}」按指令回复）"
    return "（按指令回复）"


def chat() -> None:
    eng = CompanionEngine(config=EngineConfig(state_dir="runtime/chat_state"))
    print("交互模式：输入你的话，回车看引擎指令；Ctrl-D 退出")
    while True:
        try:
            line = input("\n你说：").strip()
        except EOFError:
            break
        if not line:
            continue
        d = eng.prepare_turn("you", line)
        print("\n──── 注入大模型的指令 ────")
        print(d.to_prompt_context())
        eng.commit("you", line, "（大模型回复占位）")


if __name__ == "__main__":
    if "--chat" in sys.argv:
        chat()
    else:
        fast_forward()
