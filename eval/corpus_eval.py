"""真实语料校准测试一：CPED 逐句情绪金标 vs 感知层。

CPED（scutcyr/CPED）：电视剧多轮对话，每句人工标注 13 类情绪 + 3 类极性。
这是第一份**非自产**的逐句金标——终结"裁判兼运动员"问题的开始。

口径与既往一致：严格 Acc / 宏F1（弃权与不可输出类计错）+ 多数类与
字符朴素贝叶斯对照；另报 3 类极性（粒度更粗、跨域更公平）。
域差声明：电视剧成人对白 ≠ 第二人称陪伴对话，读下限不读上限。

用法：python eval/corpus_eval.py    （需先 fetch cped；结果追加写 CORPUS_RESULTS.md）
"""

from __future__ import annotations

import csv
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from run_public_eval import CharNB  # noqa: E402  复用最弱学习基线

from relationshape.perception import perceive  # noqa: E402

CPED = ROOT / "eval" / "data" / "corpora" / "cped" / "data" / "CPED"
OUT = ROOT / "eval" / "CORPUS_RESULTS.md"
SEED, TEST_N, NB_POOL = 42, 3000, 20000

# 我们的标签 → CPED 13 类
OURS_TO_CPED = {
    "happy": "happy", "content": "relaxed", "warm": "positive-other",
    "angry": "anger", "annoyed": "anger",
    "sad": "sadness", "lonely": "depress", "tired": "depress", "distress": "depress",
    "anxious": "worried", "bored": "negative-other", "neutral": "neutral",
}
CPED_CLASSES = ["happy", "relaxed", "grateful", "positive-other", "neutral", "anger",
                "disgust", "fear", "sadness", "depress", "worried", "astonished", "negative-other"]

POS = {"happy", "relaxed", "grateful", "positive-other"}
NEG = {"anger", "disgust", "fear", "sadness", "depress", "worried", "negative-other"}


def load(split: str) -> list[tuple[str, str]]:
    rows = list(csv.reader(open(CPED / f"{split}_split.csv", encoding="utf-8")))
    hdr = [h.lstrip("﻿") for h in rows[0]]
    ei, ui = hdr.index("Emotion"), hdr.index("Utterance")
    return [(r[ui].strip(), r[ei].strip()) for r in rows[1:] if r[ui].strip() and r[ei].strip()]


def metrics(golds, preds, labels):
    n = len(golds)
    acc = sum(p == g for g, p in zip(golds, preds)) / n
    f1s = []
    for lb in labels:
        tp = sum(1 for g, p in zip(golds, preds) if g == lb and p == lb)
        fp = sum(1 for g, p in zip(golds, preds) if g != lb and p == lb)
        fn = sum(1 for g, p in zip(golds, preds) if g == lb and p != lb)
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * pr * rc / (pr + rc) if pr + rc else 0.0)
    return acc, sum(f1s) / len(f1s)


def main() -> None:
    if not CPED.exists():
        print("先运行: python eval/fetch_dialogue_corpora.py cped", file=sys.stderr)
        sys.exit(2)
    rng = random.Random(SEED)
    test = rng.sample(load("test"), TEST_N)
    train_pool = rng.sample(load("train"), NB_POOL)
    golds = [g for _, g in test]

    # ---- 13 类情绪 ----
    ours = [OURS_TO_CPED.get(perceive(t)[1].label, "neutral") for t, _ in test]
    majority = Counter(g for _, g in train_pool).most_common(1)[0][0]
    nb = CharNB(); nb.fit(train_pool)
    nbp = [nb.predict(t) for t, _ in test]
    rows13 = [
        ("ours（规则感知层）", *metrics(golds, ours, CPED_CLASSES)),
        ("majority", *metrics(golds, [majority] * TEST_N, CPED_CLASSES)),
        ("char-NB", *metrics(golds, nbp, CPED_CLASSES)),
    ]

    # ---- 3 类极性（粗粒度，跨域更公平）----
    def to_pol(lb): return "pos" if lb in POS else ("neg" if lb in NEG else "neu")
    gp = [to_pol(g) for g in golds]

    def ours_pol(t):
        v = perceive(t)[1].valence
        return "pos" if v > 0.05 else ("neg" if v < -0.05 else "neu")
    rows3 = [
        ("ours（效价）", *metrics(gp, [ours_pol(t) for t, _ in test], ["pos", "neu", "neg"])),
        ("majority", *metrics(gp, [to_pol(majority)] * TEST_N, ["pos", "neu", "neg"])),
        ("char-NB", *metrics(gp, [to_pol(p) for p in nbp], ["pos", "neu", "neg"])),
    ]

    L = ["# 真实语料校准结果", "", f"## CPED 逐句情绪（test 抽样 n={TEST_N}, seed={SEED}）", "",
         "| 系统 | 13类 Acc | 13类宏F1 |", "| --- | --- | --- |"]
    for name, a, f in rows13:
        L.append(f"| {name} | {a:.3f} | {f:.3f} |")
        print(f"{name:<18} 13类 Acc={a:.3f}  宏F1={f:.3f}")
    L += ["", "| 系统 | 3类极性 Acc | 宏F1 |", "| --- | --- | --- |"]
    for name, a, f in rows3:
        L.append(f"| {name} | {a:.3f} | {f:.3f} |")
        print(f"{name:<18} 3类极性 Acc={a:.3f}  宏F1={f:.3f}")
    L += ["", "域差声明：CPED 为电视剧成人对白，与第二人称陪伴对话有显著域差；",
          "本结果是感知原语的下限校准，不是产品内表现。"]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n已写入 {OUT.name}")


if __name__ == "__main__":
    main()
