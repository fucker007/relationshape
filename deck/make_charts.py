"""生成 PPT 用图表（深色透明底，与 deck 同色系；中文用文泉驿正黑渲染）。"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt

FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
font_manager.fontManager.addfont(FONT)
matplotlib.rcParams["font.family"] = "WenQuanYi Zen Hei"
matplotlib.rcParams["axes.unicode_minus"] = False

OUT = Path(__file__).resolve().parent / "assets"
OUT.mkdir(exist_ok=True)

# 调色板（色彩理论：藏青基底 + 暖珊瑚/琥珀 + 冷青；高对比、暖主调）
INK = "#0E1530"; WHITE = "#F4F1EA"; MUTED = "#9AA6C4"
CORAL = "#FF7A66"; TEAL = "#4FD1C5"; AMBER = "#FFC75F"; GREEN = "#5BD6A0"; GRID = "#33406A"


def _style(ax):
    ax.set_facecolor("none")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=12)
    ax.yaxis.label.set_color(MUTED)
    ax.xaxis.label.set_color(MUTED)


def save(fig, name):
    fig.savefig(OUT / name, dpi=200, transparent=True, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)


# ── 1. 召回率：规则补全前 vs 补全后（已追平完美LLM上界）──
def chart_recall_improvement():
    cats = ["名字", "喜好", "厌恶", "朋友"]
    before = [74, 100, 75, 77]
    after = [100, 100, 100, 100]
    x = range(len(cats)); w = 0.36
    fig, ax = plt.subplots(figsize=(8.4, 4.5))
    b1 = ax.bar([i - w/2 for i in x], before, w, label="规则·补全前", color=CORAL, alpha=0.55)
    b2 = ax.bar([i + w/2 for i in x], after, w, label="规则·补全后", color=TEAL)
    ax.axhline(100, ls=(0, (4, 3)), lw=1.6, color=AMBER, alpha=0.9)
    ax.text(len(cats)-0.5, 101.5, "完美 LLM 召回上界", color=AMBER, fontsize=11, ha="right")
    for bars in (b1, b2):
        for r in bars:
            ax.text(r.get_x()+r.get_width()/2, r.get_height()+1.5, f"{int(r.get_height())}%",
                    ha="center", color=WHITE, fontsize=12, fontweight="bold")
    _style(ax)
    ax.set_xticks(list(x)); ax.set_xticklabels(cats, color=WHITE, fontsize=14)
    ax.set_ylim(0, 116); ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("独立多轮对话召回率")
    leg = ax.legend(loc="lower center", ncol=2, frameon=False, fontsize=12, bbox_to_anchor=(0.5, -0.22))
    for t in leg.get_texts():
        t.set_color(WHITE)
    save(fig, "recall_improvement.png")


# ── 2. 召回速度：延迟随记忆量（情景条数）线性增长，满载仍 ~1.4ms ──
def chart_recall_speed():
    eps = [0, 25, 50, 100, 200, 400]
    mean_us = [16.9, 107.3, 198.0, 364.9, 709.2, 1397.4]
    p99_us = [50.5, 188.2, 346.9, 500.6, 840.1, 1991.2]
    fig, ax = plt.subplots(figsize=(8.4, 4.5))
    ax.fill_between(eps, mean_us, p99_us, color=TEAL, alpha=0.12)
    ax.plot(eps, p99_us, color=MUTED, lw=1.4, ls=(0, (4, 3)), marker="o", ms=4, label="p99")
    ax.plot(eps, mean_us, color=TEAL, lw=2.8, marker="o", ms=6, label="均值")
    ax.annotate("满载 400 条 ≈ 1.4 ms", xy=(400, 1397), xytext=(250, 1650),
                color=AMBER, fontsize=12,
                arrowprops=dict(arrowstyle="->", color=AMBER, lw=1.4))
    ax.annotate("空载 ≈ 17 µs", xy=(0, 17), xytext=(20, 330), color=CORAL, fontsize=11,
                arrowprops=dict(arrowstyle="->", color=CORAL, lw=1.2))
    _style(ax)
    ax.set_xlabel("已存情景记忆条数（上限 400）")
    ax.set_ylabel("单次召回延迟（微秒）")
    ax.set_xticks(eps); ax.set_xticklabels(eps, color=WHITE)
    leg = ax.legend(loc="upper left", frameon=False, fontsize=12)
    for t in leg.get_texts():
        t.set_color(WHITE)
    save(fig, "recall_speed.png")


# ── 3. 精确率：5 项强不变量在 100 万轮独立对话上全部 100% ──
def chart_precision():
    labels = ["无幻觉偏好", "无幻觉厌恶", "第三方不\n误记成我", "撤回已生效", "提问/闲聊\n不污染"]
    vals = [100, 100, 100, 100, 100]
    y = range(len(labels))
    fig, ax = plt.subplots(figsize=(8.4, 4.5))
    ax.barh(list(y), [100]*len(labels), color=GRID, alpha=0.35, height=0.62)
    bars = ax.barh(list(y), vals, color=GREEN, height=0.62)
    for i, r in enumerate(bars):
        ax.text(98, i, "100%", va="center", ha="right", color=INK, fontsize=13, fontweight="bold")
    _style(ax)
    ax.set_yticks(list(y)); ax.set_yticklabels(labels, color=WHITE, fontsize=13)
    ax.set_xlim(0, 100); ax.set_xticks([])
    ax.invert_yaxis()
    ax.spines["bottom"].set_visible(False)
    save(fig, "precision.png")


# ── 4. 测试总览：10 套子测试的关键指标（绿=通过）──
def chart_test_overview():
    rows = [("单元测试 pytest", 100, "234 项"), ("抽取召回压测", 99.5, "5万留出"),
            ("混合负载 torture", 99, "竞争追问"), ("冲突更新", 100, "撤回/更替/转移"),
            ("带属性多实体", 100, "按活动/品类"), ("精确率注入", 100, "10万·5类污染"),
            ("独立多轮·纯规则", 100, "精确率5项"), ("独立多轮·理想上界", 100, "召回上界"),
            ("指令快照回归", 100, "54轮零漂移"), ("PostgreSQL 持久化", 100, "往返一致")]
    labels = [r[0] for r in rows]; vals = [r[1] for r in rows]; tags = [r[2] for r in rows]
    y = range(len(rows))
    fig, ax = plt.subplots(figsize=(8.6, 5.2))
    ax.barh(list(y), [100]*len(rows), color=GRID, alpha=0.3, height=0.6)
    bars = ax.barh(list(y), vals, color=GREEN, height=0.6)
    for i, (r, t) in enumerate(zip(bars, tags)):
        ax.text(vals[i]-1.5, i, f"{vals[i]:.0f}%" if vals[i] == 100 else f"{vals[i]:.1f}%",
                va="center", ha="right", color=INK, fontsize=11, fontweight="bold")
        ax.text(101.5, i, t, va="center", ha="left", color=MUTED, fontsize=10)
    _style(ax)
    ax.set_yticks(list(y)); ax.set_yticklabels(labels, color=WHITE, fontsize=12)
    ax.set_xlim(0, 100); ax.set_xticks([]); ax.invert_yaxis()
    ax.spines["bottom"].set_visible(False)
    save(fig, "test_overview.png")


# ── 5. 关系生长曲线：30 天信任随时间生长（含裂痕-修复）──
def chart_growth():
    import numpy as np
    days = np.linspace(0, 30, 200)
    base = 14 + 70 * (1 - np.exp(-days / 11))
    dip = -8 * np.exp(-((days - 12) ** 2) / 1.2)      # 第12天被骂裂痕
    trust = np.clip(base + dip, 0, 100)
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    stages = [(0, 2, "陌生", CORAL), (2, 8, "相识", AMBER), (8, 30, "熟悉", TEAL)]
    for a, b, name, c in stages:
        ax.axvspan(a, b, color=c, alpha=0.07)
        ax.text((a+b)/2, 6, name, color=c, ha="center", fontsize=12, fontweight="bold")
    ax.plot(days, trust, color=TEAL, lw=3)
    ax.fill_between(days, 0, trust, color=TEAL, alpha=0.10)
    ax.annotate("被骂→裂痕", xy=(12, trust[np.argmin(np.abs(days-12))]), xytext=(14.5, 38),
                color=CORAL, fontsize=11, arrowprops=dict(arrowstyle="->", color=CORAL))
    ax.annotate("安抚→修复后信任更高", xy=(18, trust[np.argmin(np.abs(days-18))]), xytext=(15, 92),
                color=GREEN, fontsize=11, arrowprops=dict(arrowstyle="->", color=GREEN))
    _style(ax)
    ax.set_xlabel("相处天数"); ax.set_ylabel("信任 / 亲密度")
    ax.set_xlim(0, 30); ax.set_ylim(0, 100)
    ax.tick_params(colors=MUTED)
    save(fig, "growth.png")


if __name__ == "__main__":
    chart_recall_improvement()
    chart_recall_speed()
    chart_precision()
    chart_test_overview()
    chart_growth()
    print("charts ->", OUT)
    for p in sorted(OUT.glob("*.png")):
        print("  ", p.name)
