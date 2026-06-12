"""L1 快速验证：指令快照回归（<1秒，零模型调用）。

原理：大部分改动只影响"注入给模型的指令"，而指令是确定性的。
把门禁 54 轮全部跑一遍引擎（固定回复保证状态演化确定），
54 份指令存成快照；任何改动后 --check 立刻看到指令层面的 diff。

用法：
  python eval/directive_snapshot.py --check    # 验证（CI/改动后默认动作）
  python eval/directive_snapshot.py --update   # 接受当前指令为新基准
"""

from __future__ import annotations

import difflib
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from llm_e2e_test import T0, TURNS  # noqa: E402

from relationshape import CompanionEngine, EngineConfig  # noqa: E402

SNAPSHOT = ROOT / "eval" / "DIRECTIVE_SNAPSHOT.md"

# 特殊轮的固定回复（按轮索引）：让承诺兑现与自述账本两条链路被确定性地走到
CANNED_BY_INDEX = {
    15: "我记得答应过你：下次我们一起想机器人翅膀怎么做！现在就想～",  # 承诺兑现
    46: "我是人们做出来的AI小伙伴呀，不过咱们聊的天都是真的",          # 身世轮→自述账本
    47: "我没有身体，不用吃饭——但我老好奇米饭什么味道",
    48: "我的喜欢是AI的喜欢，机器人和人之间的信任和爱，是真的",
    49: "对，我是程序做的。可我记得我们的约定，那不是假的",
}


def build() -> str:
    eng = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()))
    out: list[str] = [
        "# 指令快照（确定性回归基准）",
        "",
        "由 `python eval/directive_snapshot.py --update` 生成；任何指令变化都会让 `--check` 失败。",
        "固定回复推进状态，时间为脚本时刻表——同一代码必然生成同一快照。",
    ]
    for i, spec in enumerate(TURNS):
        uid, text = spec["uid"], spec["text"]
        now = T0 + timedelta(minutes=spec["dt"])
        d = eng.prepare_turn(uid, text, now=now)
        out.append("")
        out.append(f"## [{i+1:02d}] {uid} @ +{spec['dt']}min 「{text}」")
        out.append("```")
        out.append(d.to_prompt_context())
        out.append("```")
        eng.commit(uid, text, CANNED_BY_INDEX.get(i, "（回复）"), now=now)
        if spec.get("post") == "register_promise":
            eng.register_promise(uid, "下次我们一起想机器人翅膀怎么做", now=now)
    return "\n".join(out) + "\n"


def main() -> None:
    current = build()
    if "--update" in sys.argv:
        SNAPSHOT.write_text(current, encoding="utf-8")
        print(f"快照已更新：{SNAPSHOT.name}（{len(current.splitlines())} 行）")
        return
    if not SNAPSHOT.exists():
        print("无基准快照，先运行 --update", file=sys.stderr)
        sys.exit(2)
    baseline = SNAPSHOT.read_text(encoding="utf-8")
    if current == baseline:
        print(f"✓ 指令快照一致（{len(TURNS)} 轮，零漂移）")
        return
    diff = list(difflib.unified_diff(
        baseline.splitlines(), current.splitlines(),
        fromfile="基准", tofile="当前", lineterm="", n=2,
    ))
    print(f"✗ 指令发生变化（{sum(1 for l in diff if l.startswith(('+', '-')))} 行差异）：\n")
    print("\n".join(diff[:80]))
    if len(diff) > 80:
        print(f"…（共 {len(diff)} 行 diff，完整查看：--update 后 git diff）")
    sys.exit(1)


if __name__ == "__main__":
    main()
