"""召回速度基准：每轮"调记忆"花多少时间，以及随记忆量怎么增长。

口径：引擎不生成文本。本基准只测**召回**那一段——prepare_turn 在关键路径上调用的
  memory.recall（情景）+ recall_preferences（偏好）+ recall_semantic_facts（语义事实）
  + user_profile_facts（人物档案）。这几步合起来就是"调一次记忆"。

热点：recall() 是 O(情景条数)，且每条都现算字符二元组做主题重叠（无缓存），情景上限 400。
所以召回延迟随记忆量线性增长——这里就量它在 0~400 条下的 p50/p95/p99 和吞吐。

用法：python eval/benchmark_recall.py [--iters 3000]
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.memory import MemoryBank  # noqa: E402

T0 = datetime(2026, 1, 1, 9, 0)
HALF_LIFE = 14.0

TOPICS = ["恐龙", "钢琴", "机器人", "画画", "足球", "乐高", "天文", "游泳", "围棋", "做饭",
          "滑板", "书法", "篮球", "摄影", "跑步", "骑车", "种菜", "钓鱼", "魔方", "羽毛球"]
TPL = ["我今天在学{0}，特别好玩", "我最喜欢{0}了", "{0}课上老师夸了我",
       "我想做一个关于{0}的作品", "今天{0}练得有点累", "我跟同学聊了{0}的事",
       "周末我和爸爸一起玩{0}", "我在{0}比赛里拿了名次", "{0}让我觉得很放松"]
PROBES = ["我们聊聊恐龙吧", "我今天有点难过", "你还记得我喜欢什么吗", "同桌今天抢我橡皮",
          "我考了满分", "我想做会飞的机器人", "你好呀", "我最近在练钢琴", "我讨厌什么来着",
          "谁是我的朋友"]


def _bank(n_eps: int, rng: random.Random) -> MemoryBank:
    b = MemoryBank()
    b.preferences = rng.sample(TOPICS, min(5, len(TOPICS)))
    b.aversions = ["香菜", "打雷", "苦瓜"]
    b.user_name = "小明"
    for i in range(n_eps):
        topic = rng.choice(TOPICS)
        text = rng.choice(TPL).format(topic)
        created = T0 + timedelta(days=rng.uniform(0, 60), minutes=i)
        b.add_episode(text, valence=rng.uniform(-0.5, 0.8), arousal=rng.uniform(0.1, 0.7), now=created)
    return b


def _pctl(xs, p):
    return statistics.quantiles(xs, n=100)[p - 1] if len(xs) > 1 else xs[0]


def _time_recall(bank: MemoryBank, probes, now, iters, rng):
    """计时一次完整召回束（情景+偏好+语义+档案）。返回每次的微秒列表 + 分项均值。"""
    full, comp = [], {"episodic": [], "preferences": [], "semantic": [], "profile": []}
    for _ in range(iters):
        q = rng.choice(probes)
        t0 = time.perf_counter_ns()
        r1 = bank.recall(q, now, 3, HALF_LIFE)
        t1 = time.perf_counter_ns()
        bank.recall_preferences(q)
        t2 = time.perf_counter_ns()
        bank.recall_semantic_facts(q)
        t3 = time.perf_counter_ns()
        bank.user_profile_facts()
        t4 = time.perf_counter_ns()
        full.append((t4 - t0) / 1000.0)
        comp["episodic"].append((t1 - t0) / 1000.0)
        comp["preferences"].append((t2 - t1) / 1000.0)
        comp["semantic"].append((t3 - t2) / 1000.0)
        comp["profile"].append((t4 - t3) / 1000.0)
    return full, comp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=3000)
    a = ap.parse_args()
    rng = random.Random(7)
    now = T0 + timedelta(days=60)
    sizes = [0, 25, 50, 100, 200, 400]

    print(f"召回速度基准 · 每档 {a.iters} 次 · 一次召回 = 情景+偏好+语义+档案")
    print("=" * 76)
    print(f"  {'情景条数':<8}{'均值':>9}{'p50':>9}{'p95':>9}{'p99':>9}{'吞吐/秒':>12}")
    print("-" * 76)
    rows = []
    for n in sizes:
        bank = _bank(n, rng)
        _time_recall(bank, PROBES, now, 200, rng)          # 预热
        full, comp = _time_recall(bank, PROBES, now, a.iters, rng)
        mean = statistics.mean(full)
        rows.append((n, mean, comp))
        print(f"  {n:<8}{mean:>8.1f}µ{_pctl(full,50):>8.1f}µ{_pctl(full,95):>8.1f}µ"
              f"{_pctl(full,99):>8.1f}µ{1_000_000/mean:>11,.0f}")
    print("-" * 76)
    # 分项（取最大负载 400 条，看时间花在哪）
    n, mean, comp = rows[-1]
    print(f"  分项耗时@{n}条（均值µs）："
          + "  ".join(f"{k}={statistics.mean(v):.1f}" for k, v in comp.items()))

    # 端到端对照：完整 prepare_turn（含感知+召回+建指令）在 400 条负载下
    cfg = EngineConfig(state_dir="/tmp/bench_recall")
    eng = CompanionEngine(config=cfg)
    eng.store.save = lambda st: None
    st = eng._state("bench_u")
    st.memory = _bank(400, rng)
    e2e = []
    for _ in range(1000):
        q = rng.choice(PROBES)
        t0 = time.perf_counter_ns()
        eng.prepare_turn("bench_u", q, now=now)
        e2e.append((time.perf_counter_ns() - t0) / 1000.0)
        st.pending = None
    print(f"\n  端到端 prepare_turn@400条（含感知+召回+建指令）："
          f"均值 {statistics.mean(e2e)/1000:.2f}ms · p95 {_pctl(e2e,95)/1000:.2f}ms · p99 {_pctl(e2e,99)/1000:.2f}ms")
    print("=" * 76)


if __name__ == "__main__":
    main()
