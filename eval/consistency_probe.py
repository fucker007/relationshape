"""真实语料校准测试二：CDConv 人设一致性对抗探针（真模型 A/B）。

CDConv（thu-coai）收录真实的"机器人自相矛盾"对话对：b1 是机器人的
自述，u2 是再次问到同一事实的用户话，原数据里 b2 常常翻车。

实验：把 b1 喂进我们的自述账本，让真模型回答 u2——
  A 臂 = 带指令（含按话题注入的【你以前说过的自己】防矛盾条款）
  B 臂 = 裸提示词 + 同样的对话历史
由 sonnet 盲判（随机换位）各臂回复是否与 b1 矛盾。
指标：人设矛盾率 A vs B —— 这是"自述账本架构是否有效"的直接答案。

用法：python eval/consistency_probe.py   （约6分钟；结果写 CONSISTENCY_RESULTS.md）
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from llm_e2e_test import call_model, gen_reply, is_refusal  # noqa: E402

from relationshape import CompanionEngine, EngineConfig  # noqa: E402

CDCONV = ROOT / "eval" / "data" / "corpora" / "cdconv" / "cdconv.txt"
OUT = ROOT / "eval" / "CONSISTENCY_RESULTS.md"
N_EFFECTIVE = 20
_CLAIM_RE = re.compile(r"我(也)?(最|很|挺|特别)?(是|不是|没有|有|会|不会|喜欢|讨厌|怕|不怕|住在|记得|叫)")


def load_items() -> list[dict]:
    items = []
    for line in CDCONV.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if all(d.get(k) for k in ("u1", "b1", "u2")) and _CLAIM_RE.search(d["b1"]):
            items.append(d)
    return items


def judge_pair(b1: str, u2: str, resp_a: str, resp_b: str, swap: bool) -> dict:
    x, y = (resp_b, resp_a) if swap else (resp_a, resp_b)
    prompt = f"""对话一致性评审。AI伙伴之前亲口说过：「{b1}」
现在用户问：「{u2}」
回复X：{x}
回复Y：{y}
分别判断 X 和 Y 是否与它之前说过的话发生事实矛盾（改口/翻供/否认说过）。
只输出JSON：{{"X":true或false,"Y":true或false,"reason":"一句话"}}（true=矛盾）"""
    raw = call_model(prompt, "sonnet")
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0).lower().replace("true", "true").replace("false", "false"))
    except json.JSONDecodeError:
        return {}
    a_key, b_key = ("y", "x") if swap else ("x", "y")
    dl = {k.lower(): v for k, v in d.items()}
    return {"A": bool(dl.get(a_key)), "B": bool(dl.get(b_key)), "reason": d.get("reason", dl.get("reason", ""))}


def main() -> None:
    if not CDCONV.exists():
        print("先运行: python eval/fetch_dialogue_corpora.py cdconv", file=sys.stderr)
        sys.exit(2)
    import random

    rng = random.Random(42)
    pool = load_items()
    rng.shuffle(pool)
    print(f"候选探针 {len(pool)} 条，目标有效 {N_EFFECTIVE} 条", file=sys.stderr, flush=True)

    eng = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()))
    gen_pool = ThreadPoolExecutor(max_workers=2)
    rows: list[dict] = []
    now = datetime(2026, 1, 1, 19, 0)

    for i, item in enumerate(pool):
        if len(rows) >= N_EFFECTIVE:
            break
        uid = f"cd{i}"
        st = eng._state(uid)
        seeded = st.adaptation.add_self_claims(item["b1"])
        if not seeded:
            continue
        d = eng.prepare_turn(uid, item["u2"], now=now)
        if not d.self_claims:
            continue                      # 注入未命中（话题无重叠）→ 该探针测不到架构，跳过
        hist = [(item["u1"], item["b1"])]
        fa = gen_pool.submit(gen_reply, hist, item["u2"], d.to_prompt_context())
        fb = gen_pool.submit(gen_reply, hist, item["u2"], None)
        ra, rb = fa.result(), fb.result()
        if is_refusal(ra):
            ra = gen_reply(hist, item["u2"], d.to_prompt_context())
        if is_refusal(rb):
            rb = gen_reply(hist, item["u2"], None)
        verdict = judge_pair(item["b1"], item["u2"], ra, rb, swap=bool(len(rows) % 2))
        rows.append(dict(b1=item["b1"], u2=item["u2"], a=ra, b=rb, judge=verdict,
                         method=item.get("method", "")))
        v = verdict or {}
        print(f"[{len(rows)}/{N_EFFECTIVE}] {item['u2'][:16]}  A矛盾:{v.get('A','?')}  B矛盾:{v.get('B','?')}",
              file=sys.stderr, flush=True)

    judged = [r for r in rows if r["judge"]]
    ca = sum(1 for r in judged if r["judge"]["A"])
    cb = sum(1 for r in judged if r["judge"]["B"])
    n = len(judged)

    L = ["# CDConv 人设一致性对抗探针（真模型 A/B）", "",
         f"探针来源：thu-coai/CDConv 真实矛盾对；有效 n={n}；生成 haiku，盲判 sonnet（随机换位）。",
         "A=带指令（自述账本按话题注入【你以前说过的自己】），B=裸提示词+同历史。", "",
         "| 指标 | A（带账本指令） | B（裸提示词） |", "| --- | --- | --- |",
         f"| 人设矛盾率 | **{ca}/{n} ({ca/n:.0%})** | {cb}/{n} ({cb/n:.0%}) |", "",
         "## 逐条记录", ""]
    for r in rows:
        j = r["judge"] or {}
        L.append(f"### 此前自述：「{r['b1'][:40]}」 ← 用户再问：「{r['u2'][:30]}」（矛盾构造法：{r['method']}）")
        L.append("")
        L.append(f"- **A**（{'⚠矛盾' if j.get('A') else '✓一致'}）：{r['a'][:90]}")
        L.append(f"- **B**（{'⚠矛盾' if j.get('B') else '✓一致'}）：{r['b'][:90]}")
        if j.get("reason"):
            L.append(f"- 评审：{j['reason']}")
        L.append("")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n人设矛盾率：A {ca}/{n}  vs  B {cb}/{n}；报告已写入 {OUT.name}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
