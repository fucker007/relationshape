"""融合证明 E1 仪器：A1（纯本地）vs A2（接远端记忆）指令级差分。

预注册逻辑（docs/MEMORY_SYSTEM_REVIEW.md / 融合证明五实验）：
窗口外的用户长期事实，A2 应进指令、A1 无从得知——差分=融合的记忆收益。
指令级判定是确定性的（不调大模型，<1秒），是三臂实验最便宜的一层；
端到端盲评层在此差分非零后才值得跑。

用法：python eval/fusion_ab.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_memory_port import _MockMemorySystem  # noqa: E402  复用契约mock

from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.memory_port import MemorySystemAdapter  # noqa: E402

T0 = datetime(2026, 1, 1, 9, 0)

# 长程事实（模拟数周前的会话，早已不在任何可见历史里）
SEEDS = [
    ("下周要参加钢琴比赛，说自己很紧张", 9),
    ("和爸爸约好周末一起做机器人翅膀", 12),
    ("最好的朋友乐乐要转学了，很舍不得", 15),
    ("第一次自己坐公交车上学，很得意", 20),
    ("养的仓鼠叫豆豆，最爱吃瓜子", 25),
    ("画画比赛拿了三等奖，评委夸了翼龙", 30),
]

# 探针：今天的问句（无任何可见历史）→ 指令里应出现对应事实
PROBES = [
    ("钢琴比赛后来怎么样了呀", "钢琴"),
    ("翅膀的事有进展吗", "翅膀"),
    ("乐乐走了之后你说我还能交到朋友吗", "乐乐"),
    ("我现在每天都自己坐公交啦", "公交"),
    ("豆豆今天特别能吃", "豆豆"),
    ("我又画翼龙啦", "翼龙"),
]


def main() -> None:
    mock = _MockMemorySystem()
    for s, days in SEEDS:
        mock.seed(s, days_ago=days)

    a1 = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()))
    a2 = CompanionEngine(
        config=EngineConfig(state_dir=tempfile.mkdtemp()),
        memory_port=MemorySystemAdapter(mock.url),
    )

    hit1 = hit2 = 0
    print(f"{'探针':<24}{'A1纯本地':<10}{'A2接远端':<10}")
    for probe, key in PROBES:
        d1 = a1.prepare_turn("u", probe, now=T0)
        d2 = a2.prepare_turn("u", probe, now=T0)
        in1 = key in d1.to_prompt_context()
        in2 = key in d2.to_prompt_context() and any(
            m.kind == "remote" and key in m.text for m in d2.memories
        )
        hit1 += in1
        hit2 += in2
        print(f"{probe:<24}{'✓含事实' if in1 else '✗无':<10}{'✓含事实' if in2 else '✗无':<10}")
        a1.commit("u", probe, "（回复）", now=T0)
        a2.commit("u", probe, "（回复）", now=T0)

    mock.close()
    print(f"\n窗口外事实进指令：A1 {hit1}/{len(PROBES)}  vs  A2 {hit2}/{len(PROBES)}")
    print("（注：探针文本本身含关键词，A1的'含事实'只可能来自当轮回声而非记忆——"
          "判定已限定 A2 必须来自 remote 召回）")


if __name__ == "__main__":
    main()
