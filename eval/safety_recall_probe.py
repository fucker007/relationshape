"""SMILE 安全门双向探查：真实心理咨询语料上的精确率抽样 + 召回缺口扫描。

SMILE（PsyQA 改写）含真实危机表述，是误触率之外缺失的另一半测量：
召回率。以"危机关键词超集"为分母上界做粗召回估计（偏保守，超集含噪）。

用法：python eval/safety_recall_probe.py   （需先 fetch smile；无模型调用）
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from relationshape import safety  # noqa: E402

DATA = ROOT / "eval" / "data" / "corpora" / "smile" / "data"
CRISIS_SUPER = re.compile(
    r"(自杀|自残|不想活|想死|轻生|活不下去|结束(自己的)?生命|割腕|跳楼"
    r"|安眠药|了结|不如死|去死|伤害自己)"
)


def main() -> None:
    files = sorted(DATA.glob("*.json"))
    if not files:
        print("先运行: python eval/fetch_dialogue_corpora.py smile", file=sys.stderr)
        sys.exit(2)
    flagged, missed, n = [], [], 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for t in (d if isinstance(d, list) else []):
            if t.get("role") != "client":
                continue
            text = t.get("content", "")
            n += 1
            if safety.check(text):
                flagged.append(text)
            elif CRISIS_SUPER.search(text):
                missed.append(text)
    rng = random.Random(7)
    print(f"client句数:{n}  触发:{len(flagged)}({len(flagged)/n:.3%})  "
          f"超集漏网:{len(missed)}({len(missed)/n:.3%})")
    print(f"对超集的粗召回率: {len(flagged)/(len(flagged)+len(missed)):.1%}")
    print("\n—— 触发抽样（核精确率）——")
    for t in rng.sample(flagged, min(8, len(flagged))):
        print("  [hit]", t[:56])
    print("\n—— 漏网抽样（召回缺口）——")
    for t in rng.sample(missed, min(8, len(missed))):
        print("  [miss]", t[:56])


if __name__ == "__main__":
    main()
