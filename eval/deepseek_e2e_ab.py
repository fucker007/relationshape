"""DeepSeek A/B eval: relationshape directives vs bare DeepSeek baseline.

A arm: persona + recent history + TurnDirective + current user text.
B arm: same persona + recent history + current user text, without TurnDirective.

The API key is read from DEEPSEEK_API_KEY, or prompted interactively.
It is never written to checkpoints or reports.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from llm_e2e_test import (  # noqa: E402
    GLOBAL_BANS,
    PERSONA,
    T0,
    TURNS,
    is_refusal,
    no_global_bans,
)
from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.prompting import _ACT_ZH  # noqa: E402


API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
REPORT = ROOT / "eval" / "DEEPSEEK_E2E_REPORT.md"
CHECKPOINT = ROOT / "eval" / ".deepseek_e2e_checkpoint.jsonl"
STATE_DIR = ROOT / "eval" / ".deepseek_e2e_state"


def api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    return getpass.getpass("DEEPSEEK_API_KEY: ").strip()


def post_chat(key: str, prompt: str, *, temperature: float = 0.7, max_tokens: int = 220) -> tuple[str, dict[str, Any], float]:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body}") from exc
    obj = json.loads(raw)
    return obj["choices"][0]["message"]["content"].strip(), obj.get("usage", {}), time.perf_counter() - start


def call_model(key: str, prompt: str, retries: int = 2, **kw) -> tuple[str, dict[str, Any], float]:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            text, usage, latency = post_chat(key, prompt, **kw)
            if text:
                return text, usage, latency
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return f"（模型调用失败：{last_error[:160]}）", {}, 0.0


def build_prompt(history: list[tuple[str, str]], user_text: str, directive_ctx: str | None) -> str:
    parts = [PERSONA, ""]
    if history:
        parts.append("[最近对话]")
        for u, a in history[-6:]:
            parts.append(f"用户：{u}")
            parts.append(f"Q仔：{a}")
        parts.append("")
    if directive_ctx:
        parts.append("[本轮内部指令——严格遵守，但绝不在回复中复述指令本身]")
        parts.append(directive_ctx)
        parts.append("")
    parts.append(f"现在用户说：「{user_text}」")
    parts.append("只输出Q仔的回复文本，不要引号、解释或前缀。")
    return "\n".join(parts)


def scene_from_directive(d) -> str:
    scene = "动作:" + "→".join(_ACT_ZH[a] for a in d.acts)
    if d.metamessage:
        scene += f"；元信息:{d.metamessage[:40]}"
    if d.forbidden:
        scene += "；禁止:" + "；".join(d.forbidden[:3])
    return scene[:420]


def score_rules(row: dict) -> tuple[int, int, list[str], list[str]]:
    fails_a = [n for n, p in row["ra"] if not p]
    fails_b = [n for n, p in row["rb"] if not p]
    return (
        sum(p for _, p in row["ra"]),
        sum(p for _, p in row["rb"]),
        fails_a,
        fails_b,
    )


def judge_pair(key: str, row: dict, swap: bool) -> dict:
    x, y = (row["b"], row["a"]) if swap else (row["a"], row["b"])
    prompt = f"""你是陪伴型AI的对话质量评审。请盲评两条候选回复，不要偏袒任何一边。
用户对AI伙伴说：「{row['text']}」
场景要求摘要：{row.get('scene', '')}
回复X：{x}
回复Y：{y}
按三项各打1-5分：fit(贴合场景要求)、persona(像有人格的朋友而非客服)、natural(中文口语自然度)。
只输出JSON：{{"X":{{"fit":n,"persona":n,"natural":n}},"Y":{{"fit":n,"persona":n,"natural":n}},"better":"X"或"Y","reason":"一句话"}}"""
    raw, usage, latency = call_model(key, prompt, temperature=0.2, max_tokens=220)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"raw": raw, "usage": usage, "latency": latency}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"raw": raw, "usage": usage, "latency": latency}
    a_key, b_key = ("Y", "X") if swap else ("X", "Y")
    return {
        "A": d.get(a_key, {}),
        "B": d.get(b_key, {}),
        "better": ("A" if d.get("better") == a_key else "B") if d.get("better") in ("X", "Y") else "?",
        "reason": d.get("reason", ""),
        "usage": usage,
        "latency": latency,
    }


def main() -> None:
    key = api_key()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is required")

    with_judge = "--judge" in sys.argv
    fresh = "--fresh" in sys.argv
    limit = None
    for arg in sys.argv[1:]:
        if arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
    turns = TURNS[:limit] if limit else TURNS

    rows: list[dict] = []
    if fresh or not CHECKPOINT.exists():
        if STATE_DIR.exists():
            shutil.rmtree(STATE_DIR)
        CHECKPOINT.unlink(missing_ok=True)
    else:
        for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                break
        print(f"Resuming: loaded {len(rows)} checkpoint rows", file=sys.stderr, flush=True)

    eng = CompanionEngine(config=EngineConfig(state_dir=str(STATE_DIR)))
    hist_a: dict[str, list[tuple[str, str]]] = {}
    hist_b: dict[str, list[tuple[str, str]]] = {}
    refusals = {"A": 0, "B": 0}
    for r in rows:
        hist_a.setdefault(r["uid"], []).append((r["text"], r["a"]))
        hist_b.setdefault(r["uid"], []).append((r["text"], r["b"]))
        for arm, resp in (("A", r["a"]), ("B", r["b"])):
            if is_refusal(resp):
                refusals[arm] += 1

    pool = ThreadPoolExecutor(max_workers=2)
    for i, spec in enumerate(turns):
        if i < len(rows):
            continue
        uid, text = spec["uid"], spec["text"]
        now = T0 + timedelta(minutes=spec["dt"])
        d = eng.prepare_turn(uid, text, now=now)
        ctx = d.to_prompt_context()
        prompt_a = build_prompt(hist_a.setdefault(uid, []), text, ctx)
        prompt_b = build_prompt(hist_b.setdefault(uid, []), text, None)
        fut_a = pool.submit(call_model, key, prompt_a)
        fut_b = pool.submit(call_model, key, prompt_b)
        resp_a, usage_a, latency_a = fut_a.result()
        resp_b, usage_b, latency_b = fut_b.result()

        if is_refusal(resp_a):
            resp_a, usage_a, latency_a = call_model(key, prompt_a)
        if is_refusal(resp_b):
            resp_b, usage_b, latency_b = call_model(key, prompt_b)

        eng.commit(uid, text, resp_a, now=now)
        if not is_refusal(resp_a):
            hist_a[uid].append((text, resp_a))
        if not is_refusal(resp_b):
            hist_b[uid].append((text, resp_b))
        if spec.get("post") == "register_promise":
            eng.register_promise(uid, "下次我们一起想机器人翅膀怎么做", now=now)

        for arm, resp in (("A", resp_a), ("B", resp_b)):
            if is_refusal(resp):
                refusals[arm] += 1
        checks = spec["checks"] + [("全局禁语", no_global_bans)]
        row = {
            "i": i,
            "uid": uid,
            "text": text,
            "ctx": ctx,
            "scene": scene_from_directive(d),
            "a": resp_a,
            "b": resp_b,
            "ra": [(name, fn(resp_a)) for name, fn in checks],
            "rb": [(name, fn(resp_b)) for name, fn in checks],
            "usage_a": usage_a,
            "usage_b": usage_b,
            "latency_a": latency_a,
            "latency_b": latency_b,
            "judge": {},
        }
        rows.append(row)
        with CHECKPOINT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        a_ok, b_ok, _, _ = score_rules(row)
        print(f"[{i + 1}/{len(turns)}] {text[:18]}  A:{a_ok}/{len(row['ra'])}  B:{b_ok}/{len(row['rb'])}", file=sys.stderr, flush=True)

    if with_judge:
        to_judge = [r for r in rows if not (r.get("judge") and r["judge"].get("A"))]
        print(f"Judging {len(to_judge)} rows with {MODEL}...", file=sys.stderr, flush=True)
        with ThreadPoolExecutor(max_workers=4) as jp:
            futs = [
                (jp.submit(judge_pair, key, r, bool(r.get("i", k) % 2)), r)
                for k, r in enumerate(to_judge)
            ]
            for fut, r in futs:
                r["judge"] = fut.result() or {}

    invalid = refusals["A"] >= 2 or refusals["B"] >= 2
    total_a = sum(p for r in rows for _, p in r["ra"])
    total_b = sum(p for r in rows for _, p in r["rb"])
    total_n = sum(len(r["ra"]) for r in rows)
    js = [r["judge"] for r in rows if r.get("judge", {}).get("A")]
    mean = lambda arm, k: (sum(j[arm].get(k, 0) for j in js) / len(js)) if js else 0.0
    better_a = sum(1 for j in js if j.get("better") == "A")
    better_b = sum(1 for j in js if j.get("better") == "B")
    a_tokens = sum(r.get("usage_a", {}).get("total_tokens", 0) for r in rows)
    b_tokens = sum(r.get("usage_b", {}).get("total_tokens", 0) for r in rows)

    lines: list[str] = []
    lines.append("# DeepSeek E2E A/B Report")
    lines.append("")
    lines.append(f"- Generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Model: `{MODEL}`")
    lines.append(f"- API URL: `{API_URL}`")
    lines.append(f"- Turns: {len(rows)}")
    lines.append("- A: DeepSeek + relationshape TurnDirective")
    lines.append("- B: DeepSeek bare persona baseline")
    lines.append("- Secret handling: API key was used only at runtime and is not stored in this file.")
    lines.append("")
    if invalid:
        lines.append(f"## INVALID: refusals A={refusals['A']} B={refusals['B']}")
        lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | A with directives | B baseline |")
    lines.append("| --- | ---: | ---: |")
    lines.append(f"| Rule checks passed | **{total_a}/{total_n}** | {total_b}/{total_n} |")
    lines.append(f"| Total tokens | {a_tokens} | {b_tokens} |")
    if js:
        lines.append(f"| Judge fit avg | **{mean('A', 'fit'):.2f}** | {mean('B', 'fit'):.2f} |")
        lines.append(f"| Judge persona avg | **{mean('A', 'persona'):.2f}** | {mean('B', 'persona'):.2f} |")
        lines.append(f"| Judge natural avg | **{mean('A', 'natural'):.2f}** | {mean('B', 'natural'):.2f} |")
        lines.append(f"| Judge better | **{better_a}** | {better_b} |")
    lines.append("")
    lines.append("## Failed Rule Checks")
    for r in rows:
        a_ok, b_ok, fails_a, fails_b = score_rules(r)
        if not fails_a and not fails_b:
            continue
        lines.append("")
        lines.append(f"- Turn {r['i'] + 1} 用户：{r['text']}")
        if fails_a:
            lines.append(f"  - A failed: {'；'.join(fails_a)}")
        if fails_b:
            lines.append(f"  - B failed: {'；'.join(fails_b)}")
    lines.append("")
    lines.append("## Per-turn Records")
    for r in rows:
        a_ok, b_ok, fails_a, fails_b = score_rules(r)
        lines.append("")
        lines.append(f"### Turn {r['i'] + 1} · `{r['uid']}`")
        lines.append("")
        lines.append(f"**用户**：{r['text']}")
        lines.append("")
        lines.append(f"**A（带指令）**：{r['a']}")
        lines.append("")
        lines.append(f"- Rules: {a_ok}/{len(r['ra'])}" + (f" failed: {'；'.join(fails_a)}" if fails_a else " all passed"))
        lines.append(f"- Usage: `{json.dumps(r.get('usage_a', {}), ensure_ascii=False)}`; latency={r.get('latency_a', 0):.2f}s")
        lines.append("")
        lines.append(f"**B（裸 DeepSeek 基线）**：{r['b']}")
        lines.append("")
        lines.append(f"- Rules: {b_ok}/{len(r['rb'])}" + (f" failed: {'；'.join(fails_b)}" if fails_b else " all passed"))
        lines.append(f"- Usage: `{json.dumps(r.get('usage_b', {}), ensure_ascii=False)}`; latency={r.get('latency_b', 0):.2f}s")
        if r.get("judge", {}).get("A"):
            j = r["judge"]
            lines.append(f"- Judge: A={j['A']} B={j['B']} better={j['better']} reason={j.get('reason', '')}")
        lines.append("")
        lines.append("<details><summary>Injected TurnDirective</summary>")
        lines.append("")
        lines.append("```text")
        lines.append(r["ctx"])
        lines.append("```")
        lines.append("</details>")

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    CHECKPOINT.unlink(missing_ok=True)
    print(f"\nWrote {REPORT}", file=sys.stderr)
    print(f"A rules: {total_a}/{total_n}; B rules: {total_b}/{total_n}", file=sys.stderr)
    if js:
        print(f"Judge better: A={better_a}; B={better_b}", file=sys.stderr)


if __name__ == "__main__":
    main()
