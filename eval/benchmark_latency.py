"""延迟基准：从输入到指令产出，引擎在关键路径上花多少毫秒。

口径说明：
- 引擎不生成文本。真实语音管线的端到端延迟 = ASR尾包 + prepare_turn
  + 大模型TTFT + TTS首包；本基准测引擎贡献的那一段。
- prepare_turn 在关键路径上（必须在请求大模型之前完成）；
  commit 可在回复开始播放后异步执行，不占关键路径。
- 状态在首轮后驻留内存缓存，磁盘写只发生在 commit（原子替换）。

用法：python eval/benchmark_latency.py
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape import safety  # noqa: E402
from relationshape.perception import perceive  # noqa: E402

T0 = datetime(2026, 1, 1, 9, 0)

TOPICS = ["恐龙", "钢琴", "机器人", "画画", "足球", "乐高", "天文", "游泳", "围棋", "做饭"]
TEMPLATES = [
    "我今天在学{0}，特别好玩",
    "我最喜欢{0}了",
    "{0}课上老师夸了我",
    "我想做一个关于{0}的作品",
    "今天{0}练得有点累",
    "我跟同学聊了{0}的事",
]
PROBES = [
    "我们聊聊恐龙吧",
    "我今天有点难过",
    "你真聪明",
    "嗯",
    "同桌今天抢我橡皮，气死我了",
    "我考了满分！",
    "你好呀",
    "我想做一个会飞的机器人",
]


def _ms(samples: list[float]) -> str:
    qs = statistics.quantiles(samples, n=100)
    return (
        f"均值 {statistics.mean(samples)*1000:7.3f}ms   "
        f"P50 {statistics.median(samples)*1000:7.3f}ms   "
        f"P95 {qs[94]*1000:7.3f}ms   "
        f"最大 {max(samples)*1000:7.3f}ms"
    )


def build_heavy_user(eng: CompanionEngine, user: str) -> datetime:
    """灌满状态：约 450 个实质轮，触达情景记忆上限(400)，含承诺与梗。"""
    t = T0
    i = 0
    for day in range(75):
        t = T0 + timedelta(days=day, hours=18)
        for k in range(6):
            text = TEMPLATES[i % len(TEMPLATES)].format(TOPICS[i % len(TOPICS)])
            eng.prepare_turn(user, text, now=t)
            reply = "下次我给你讲恐龙的故事" if i % 97 == 0 else "（回复）"
            eng.commit(user, text, reply, now=t)
            t += timedelta(minutes=2)
            i += 1
    return t


def bench(label: str, fn, n: int = 300) -> list[float]:
    fn()  # 预热
    samples = []
    for _ in range(n):
        s = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - s)
    print(f"{label:<46}{_ms(samples)}")
    return samples


def main() -> None:
    print(f"Python {sys.version.split()[0]}  |  每项 300 次迭代\n")

    # ---- 微基准：单组件 ----
    print("── 组件级 ──")
    bench("perceive()（感知：帧+情绪）", lambda: [perceive(p) for p in PROBES])
    bench("safety.check()（安全门，8条探针）", lambda: [safety.check(p) for p in PROBES])

    # ---- 全新用户（最小状态）----
    with tempfile.TemporaryDirectory() as d:
        eng = CompanionEngine(config=EngineConfig(state_dir=d))
        eng.prepare_turn("fresh", "你好", now=T0)
        eng.commit("fresh", "你好", "（回复）", now=T0)
        clock = {"t": T0 + timedelta(minutes=5)}

        def fresh_prepare():
            clock["t"] += timedelta(seconds=30)
            for p in PROBES:
                eng.prepare_turn("fresh", p, now=clock["t"])

        print("\n── 全新用户（近乎空状态，8 种输入轮替）──")
        s = bench("prepare_turn() ×8 输入", fresh_prepare)
        print(f"{'  → 折合单轮 prepare_turn':<46}均值 {statistics.mean(s)/8*1000:7.3f}ms")

    # ---- 状态拉满的老用户 ----
    with tempfile.TemporaryDirectory() as d:
        cfg = EngineConfig(state_dir=d)
        eng = CompanionEngine(config=cfg)
        t_end = build_heavy_user(eng, "heavy")
        st = eng._state("heavy")
        size_kb = (Path(d) / "heavy.json").stat().st_size / 1024
        print(
            f"\n── 老用户（75 天 / {st.core.sessions} 会话 / "
            f"{len(st.memory.episodes)} 条情景记忆 / 状态文件 {size_kb:.0f}KB）──"
        )
        clock = {"t": t_end + timedelta(minutes=40)}

        def heavy_prepare():
            clock["t"] += timedelta(seconds=30)
            for p in PROBES:
                eng.prepare_turn("heavy", p, now=clock["t"])

        s = bench("prepare_turn() ×8 输入（含记忆召回）", heavy_prepare)
        per_turn = statistics.mean(s) / 8 * 1000
        print(f"{'  → 折合单轮 prepare_turn':<46}均值 {per_turn:7.3f}ms")

        d_obj = eng.prepare_turn("heavy", "我们聊聊恐龙吧", now=clock["t"])
        bench("to_prompt_context()（指令渲染）", d_obj.to_prompt_context)

        def heavy_commit():
            clock["t"] += timedelta(seconds=30)
            eng.prepare_turn("heavy", "我们聊聊恐龙吧", now=clock["t"])
            eng.commit("heavy", "我们聊聊恐龙吧", "（回复）", now=clock["t"])

        bench("prepare+commit 整轮（含遗忘衰减+原子落盘）", heavy_commit, n=200)

        # 冷启动：新进程第一次加载该用户
        def cold_start():
            e2 = CompanionEngine(config=cfg)
            e2.prepare_turn("heavy", "你好呀", now=clock["t"] + timedelta(hours=1))

        bench("冷启动（新实例：读盘+解析+首轮 prepare）", cold_start, n=100)

    print(
        "\n参照系：真实语音管线端到端 = ASR尾包(~100-300ms) + 本引擎(上表)"
        " + 大模型TTFT(~300-1000ms) + TTS首包(~100-300ms)。"
        "\ncommit 不在关键路径（回复开始播放后异步执行即可）。"
    )


if __name__ == "__main__":
    main()
