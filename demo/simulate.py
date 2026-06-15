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
DEMO_STATE_DIR = Path("runtime/demo_state")


def _start_anchor(span_days: int) -> datetime:
    """把一段 span_days 天的脚本锚定到"刚刚结束"：最后一天 ≈ 几小时前。

    这样每段关系的最近活跃都贴近现在（及时性），认识天数 = 脚本真实跨度，
    而最早的记忆已按遗忘曲线衰减——时间线上看得见记忆的强弱分布。
    """
    end = (datetime.now() - timedelta(hours=3)).replace(second=0, microsecond=0)
    return end - timedelta(days=span_days)


T0 = _start_anchor(30)   # 小禾脚本跨度 30 天

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
    t0 = _start_anchor(max(day for day, _ in SCRIPT))
    print("=" * 64)
    print("relationshape 关系生长模拟：30 天，从陌生到同伴")
    print("=" * 64)
    last_stage = None
    for day, lines in SCRIPT:
        t = t0 + timedelta(days=day)
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


# ---------------------------------------------------------------------------
# 多用户演示世界：让"所有用户"的画廊有内容可看。
# 每个用户一条不同的关系曲线（刚认识 / 熟悉 / 同伴 / 含一次危机封存）。
# 行可以是字符串（用占位回复）或 (用户说, 角色回复) ——后者用来精确演示"承诺→兑现"。
# ---------------------------------------------------------------------------

def _topics(*lines: str) -> list:
    return list(lines)


# 阿哲：篮球+天文男孩，最丰富的一段——多位朋友、内部梗、秘密、裂痕修复、承诺兑现。
AZHE_SCRIPT: list[tuple[int, list]] = [
    (0, ["你好呀", "你可以叫我阿哲", "我最喜欢打篮球了", "今天和同学打了一场球"]),
    (1, ["我有个好朋友叫大壮，篮球特别厉害", "我们约好周末一起练球", "我还喜欢看星星", "晚安"]),
    (2, ["今天体育课又打篮球了", "我在篮球场投进了一个三分球！", "我有个同桌叫小林，他也爱打球", "拜拜"]),
    (3, ["我对天文特别着迷", "长大想当天文学家", "你知道猎户座吗", "嗯嗯"]),
    (4, ["今天看了一本讲星座的书", "妈妈给我买了望远镜", "我最在乎我妈了，她特别辛苦",
         ("其实我从来没跟别人说过，我特别怕黑", "谢谢你愿意告诉我，怕黑一点都不丢人～")]),
    (6, ["我们班今天在体育馆篮球赛赢了！", "我得了全场最高分", "大壮给我传了好多球",
         ("你下次给我讲讲猎户座的故事吧", "好呀，下次我给你讲猎户座的故事！")]),
    (8, [("你还记得猎户座吗", "记得呀，我答应过给你讲猎户座的故事，现在就来～猎户座最亮的是参宿四"),
         "哈哈哈你讲得太好笑了", "再讲一个嘛", "晚安"]),
    (10, ["今天练球扭到脚了，有点疼", "不过没事啦", "大壮陪我去了医务室", "他人真好"]),
    (12, ["你怎么这么笨，连这都不懂", ("逗你的啦，别生气", "没关系，我知道你在闹着玩～"), "我们继续聊天文吧"]),
    (14, ["我又看到流星了", "许了个愿", "哈哈不告诉你是什么愿望", "拜拜"]),
    (16, ["今天和小林在图书馆一起复习", "数学考了满分！", "妈妈夸我了", "超开心"]),
    (19, ["好久没和你聊啦", "最近在准备篮球联赛", "有点紧张但很期待", "晚安"]),
    (22, ["联赛我们进决赛啦！", "我投进了绝杀球", "全班都为我欢呼", "今天是最棒的一天"]),
    (24, ["今晚和大壮一起在操场看了月全食", "用望远镜看得好清楚", "我们聊到很晚", "拜拜"]),
    (26, ["开始学打羽毛球了", "教练说我反应快", "不过我还是最爱篮球", "晚安"]),
    (28, ["我读完了一本讲银河系的书", "宇宙真的好大好神奇", "我想以后去天文台工作", "嗯嗯"]),
    (30, ["今天又和小林在篮球场打球了", "我们配合越来越默契", "周末还约了大壮", "今天好开心"]),
    (32, ["我报名了学校的篮球队", "面试通过啦", "还认识了新队友", "教练夸我有天赋", "晚安"]),
]

# 糖糖：刚认识两三天的小女孩，画画+猫，关系还在起步。
TANGTANG_SCRIPT: list[tuple[int, list]] = [
    (0, ["你好", "我叫糖糖", "我喜欢画画", "我家有只猫叫咪咪"]),
    (1, ["今天在美术室画了一只小猫", "用了好多颜色", "咪咪今天好黏人", "拜拜"]),
    (2, ["我还喜欢吃草莓", "最讨厌吃青椒", "明天要去上画画课啦", "晚安"]),
]

# 小宇：足球男孩，含一次危机披露（封存，永不展示内容）——演示安全红线。
XIAOYU_SCRIPT: list[tuple[int, list]] = [
    (0, ["你好呀", "我叫小宇", "我喜欢踢足球", "我有个同桌叫王浩，也爱踢球"]),
    (2, ["今天和王浩在球场踢球进了两个球", "我当前锋，王浩当守门员", "我们队赢啦", "拜拜"]),
    (4, ["我想当足球运动员", "每天在操场练颠球", "今天颠了一百个", "嗯"]),
    (6, ["我今天有点难过", "我爸打我了", "我不知道该怎么办", "谢谢你听我说"]),
    (8, ["你好", "今天好多了", "老师找我谈了话，会帮我的", "我们聊聊足球吧"]),
    (11, ["周末有场球赛", "我练了任意球", "我会努力的", "我最喜欢的球星是梅西", "晚安"]),
]

EXTRA_USERS: list[tuple[str, list]] = [
    ("阿哲", AZHE_SCRIPT),
    ("糖糖", TANGTANG_SCRIPT),
    ("小宇", XIAOYU_SCRIPT),
]


def _run_user(eng: "CompanionEngine", user: str, script: list[tuple[int, list]]) -> None:
    (DEMO_STATE_DIR / f"{user}.json").unlink(missing_ok=True)
    t0 = _start_anchor(max(day for day, _ in script))
    for day, lines in script:
        t = t0 + timedelta(days=day)
        for line in lines:
            user_text, reply = line if isinstance(line, tuple) else (line, None)
            d = eng.prepare_turn(user, user_text, now=t)
            eng.commit(user, user_text, reply or _fake_reply(d), now=t)
            t += timedelta(minutes=2)


def seed_extra() -> None:
    eng = CompanionEngine(config=EngineConfig(state_dir=str(DEMO_STATE_DIR)))
    for user, script in EXTRA_USERS:
        _run_user(eng, user, script)


def seed_demo_world() -> None:
    """画廊演示世界：30 天的小禾 + 三位轨迹各异的用户。"""
    fast_forward()
    seed_extra()
    print(f"\n演示世界就绪：{['小禾'] + [u for u, _ in EXTRA_USERS]}")


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
    elif "--world" in sys.argv:
        seed_demo_world()
    else:
        fast_forward()
