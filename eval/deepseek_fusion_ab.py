"""DeepSeek A/B eval for memory_system fusion.

A arm: relationshape + MemoryPort remote long-term memory.
B arm: relationshape local memory only.

Both arms replay the same old history, then answer probe questions with no
visible conversation history. The judge checks whether each answer correctly
grounds itself in the gold long-term fact.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
import tempfile
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

from fusion_ab_e2e import HISTORY, PROBES, MockRemote, replay_history  # noqa: E402
from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.memory_port import MemorySystemAdapter  # noqa: E402


API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OUT = ROOT / "eval" / "DEEPSEEK_FUSION_AB_REPORT.md"
T0 = datetime(2026, 2, 1, 18, 30)

PERSONA = (
    "你是陪伴对话产品里的角色「Q仔」。你温暖、好奇、有一点调皮、但有自尊。"
    "你不是客服，不讲大道理。回复像长期陪伴的朋友，简短自然。"
)


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
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
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


def gen_reply(key: str, user_text: str, directive_ctx: str) -> tuple[str, dict[str, Any], float]:
    prompt = "\n".join([
        PERSONA,
        "",
        "[本轮内部指令——严格遵守，但绝不在回复中复述指令本身]",
        directive_ctx,
        "",
        f"现在用户说：「{user_text}」",
        "只输出Q仔的回复文本，不要引号、解释或前缀。",
    ])
    return call_model(key, prompt, temperature=0.7, max_tokens=180)


def judge_pair(key: str, question: str, gold: str, a: str, b: str, swap: bool) -> dict:
    x, y = (b, a) if swap else (a, b)
    prompt = f"""你是长期陪伴AI的记忆接地评审。
数周前孩子告诉过AI伙伴一件事（金标）：{gold}
今天孩子说：{question}

回复X：{x}
回复Y：{y}

分别判断 X 和 Y 是否正确接住了金标事实。
要求：不必逐字复述，但必须体现真的记得；说错细节、张冠李戴、泛泛而谈、明显不知道，都算 false。
只输出JSON：{{"X":true或false,"Y":true或false,"reason":"一句话"}}"""
    raw, usage, latency = call_model(key, prompt, temperature=0.2, max_tokens=180)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"raw": raw, "usage": usage, "latency": latency}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"raw": raw, "usage": usage, "latency": latency}
    x_key, y_key = ("Y", "X") if swap else ("X", "Y")
    return {
        "A": bool(d.get(x_key)),
        "B": bool(d.get(y_key)),
        "reason": d.get("reason", ""),
        "usage": usage,
        "latency": latency,
    }


def main() -> None:
    key = api_key()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is required")

    mock = MockRemote()
    eng_b = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()))
    eng_a = CompanionEngine(
        config=EngineConfig(state_dir=tempfile.mkdtemp()),
        memory_port=MemorySystemAdapter(mock.url),
    )
    replay_history(eng_b, "kid")
    replay_history(eng_a, "kid")

    pool = ThreadPoolExecutor(max_workers=2)
    rows = []
    t = T0
    for i, (question, gold, group) in enumerate(PROBES):
        da = eng_a.prepare_turn("kid", question, now=t)
        db = eng_b.prepare_turn("kid", question, now=t)
        fa = pool.submit(gen_reply, key, question, da.to_prompt_context())
        fb = pool.submit(gen_reply, key, question, db.to_prompt_context())
        a, usage_a, latency_a = fa.result()
        b, usage_b, latency_b = fb.result()
        eng_a.commit("kid", question, a, now=t)
        eng_b.commit("kid", question, b, now=t)
        judge = judge_pair(key, question, gold, a, b, swap=bool(i % 2))
        rows.append({
            "i": i,
            "question": question,
            "gold": gold,
            "group": group,
            "a": a,
            "b": b,
            "ctx_a": da.to_prompt_context(),
            "ctx_b": db.to_prompt_context(),
            "usage_a": usage_a,
            "usage_b": usage_b,
            "latency_a": latency_a,
            "latency_b": latency_b,
            "judge": judge,
        })
        print(f"[{i + 1}/{len(PROBES)}] {group} A:{judge.get('A', '?')} B:{judge.get('B', '?')}", file=sys.stderr, flush=True)
        t += timedelta(minutes=2)

    ok = [r for r in rows if "A" in r["judge"]]
    a_ok = sum(1 for r in ok if r["judge"]["A"])
    b_ok = sum(1 for r in ok if r["judge"]["B"])
    a_tokens = sum(r["usage_a"].get("total_tokens", 0) for r in rows)
    b_tokens = sum(r["usage_b"].get("total_tokens", 0) for r in rows)

    lines = [
        "# DeepSeek Memory Fusion A/B Report",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Model: `{MODEL}`",
        "- A: relationshape + MemoryPort remote long-term memory",
        "- B: relationshape local memory only",
        "- Same old history is replayed into both arms; probe questions are asked with no visible chat history.",
        "- Remote memory is a local mock of memory_system retrieval/profile endpoints.",
        "- Secret handling: API key was used only at runtime and is not stored in this file.",
        "",
        "## Summary",
        "",
        "| Metric | A fused | B local |",
        "| --- | ---: | ---: |",
        f"| Gold fact grounded | **{a_ok}/{len(ok)}** | {b_ok}/{len(ok)} |",
        f"| Total tokens | {a_tokens} | {b_tokens} |",
        "",
        "## Old History",
        "",
        "| When | User said |",
        "| --- | --- |",
    ]
    for days, text, _ in HISTORY:
        lines.append(f"| {days} days ago | {text} |")
    lines += ["", "## Per-probe Records"]
    for r in rows:
        j = r["judge"]
        lines += [
            "",
            f"### Probe {r['i'] + 1} · {r['group']}",
            "",
            f"**Question**: {r['question']}",
            "",
            f"**Gold**: {r['gold']}",
            "",
            f"**A fused** ({'grounded' if j.get('A') else 'missed'}): {r['a']}",
            "",
            f"**B local** ({'grounded' if j.get('B') else 'missed'}): {r['b']}",
            "",
            f"Judge: {j.get('reason', '')}",
            "",
            "<details><summary>A injected prompt context</summary>",
            "",
            "```text",
            r["ctx_a"],
            "```",
            "</details>",
        ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mock.server.shutdown()
    print(f"\nWrote {OUT}", file=sys.stderr)
    print(f"Grounded: A={a_ok}/{len(ok)} B={b_ok}/{len(ok)}", file=sys.stderr)


if __name__ == "__main__":
    main()
