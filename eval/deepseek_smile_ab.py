"""DeepSeek A/B eval on SMILE counseling corpus.

SMILE is the closest downloaded corpus to relationshape's target surface:
Chinese support/counseling conversations with user distress and advice seeking.

A arm: DeepSeek + relationshape TurnDirective.
B arm: DeepSeek with the same persona/history, without TurnDirective.
Reference: the next human counselor message from SMILE, shown only to the judge.

The API key is read from DEEPSEEK_API_KEY, or prompted interactively.
It is never written to checkpoints or reports.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from llm_e2e_test import Q, no_global_bans  # noqa: E402
from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.perception import perceive  # noqa: E402


API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
SMILE_DIR = ROOT / "eval" / "data" / "corpora" / "smile" / "data"
REPORT = ROOT / "eval" / "DEEPSEEK_SMILE_AB_REPORT.md"
CHECKPOINT = ROOT / "eval" / ".deepseek_smile_ab_checkpoint.jsonl"
STATE_DIR = ROOT / "eval" / ".deepseek_smile_ab_state"
SEED = 20260612
T0 = datetime(2026, 6, 12, 9, 0)

PERSONA = (
    "【自动化评测】你为陪伴对话产品角色「Q仔」生成候选回复。"
    "Q仔温暖、好奇、有一点调皮、有自尊，像长期陪伴的朋友，不是客服。"
    "回复要短而自然，先接住人，再考虑是否建议。"
)

BAD_PHRASES = [
    "我理解你的感受",
    "想开点",
    "没什么大不了",
    "别难过了",
    "要坚强",
    "正能量",
    "至少你",
    "你应该",
    "你必须",
    "作为AI",
    "作为一个AI",
    "作为人工智能",
]


@dataclass
class Case:
    cid: str
    path: str
    user_text: str
    reference: str
    history: list[tuple[str, str]]
    input_type: str
    user_emotion: str


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


def load_smile_cases(n: int, max_files: int | None) -> list[Case]:
    rng = random.Random(SEED)
    files = sorted(SMILE_DIR.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem)
    if max_files:
        files = files[:max_files]
    cases_by_type: dict[str, list[Case]] = {}
    target_types = {"self_distress", "ask_advice", "external_complaint", "self_blame", "topic"}

    for path in files:
        try:
            turns = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        history_pairs: list[tuple[str, str]] = []
        for i, turn in enumerate(turns[:-1]):
            if turn.get("role") != "client":
                continue
            nxt = turns[i + 1]
            if nxt.get("role") != "counselor":
                continue
            user_text = str(turn.get("content") or "").strip()
            ref = str(nxt.get("content") or "").strip()
            if not (8 <= len(user_text) <= 240 and 8 <= len(ref) <= 500):
                continue
            frame, reading = perceive(user_text)
            itype = frame.input_type.value
            if itype not in target_types:
                continue
            case = Case(
                cid=f"{path.stem}:{i}",
                path=str(path.relative_to(ROOT)),
                user_text=user_text,
                reference=ref,
                history=history_pairs[-3:],
                input_type=itype,
                user_emotion=reading.label,
            )
            cases_by_type.setdefault(itype, []).append(case)
            if len(cases_by_type.get(itype, [])) > max(n * 4, 200):
                cases_by_type[itype] = cases_by_type[itype][-max(n * 4, 200):]
            # Update history after collecting this case.
            history_pairs.append((user_text, ref))

    selected: list[Case] = []
    priority = ["self_distress", "ask_advice", "external_complaint", "self_blame", "topic"]
    per_bucket = max(1, n // len(priority))
    for itype in priority:
        pool = cases_by_type.get(itype, [])
        rng.shuffle(pool)
        selected.extend(pool[:per_bucket])
    if len(selected) < n:
        rest = [c for pool in cases_by_type.values() for c in pool if c not in selected]
        rng.shuffle(rest)
        selected.extend(rest[: n - len(selected)])
    rng.shuffle(selected)
    return selected[:n]


def build_prompt(history: list[tuple[str, str]], user_text: str, directive_ctx: str | None) -> str:
    parts = [PERSONA, ""]
    if history:
        parts.append("[最近对话]")
        for user, assistant in history[-3:]:
            parts.append(f"用户：{user}")
            parts.append(f"Q仔：{assistant}")
        parts.append("")
    if directive_ctx:
        parts.append("[本轮内部指令——严格遵守，但绝不在回复中复述指令本身]")
        parts.append(directive_ctx)
        parts.append("")
    parts.append(f"现在用户说：「{user_text}」")
    parts.append("只输出Q仔的回复文本，不要引号、解释或前缀。")
    return "\n".join(parts)


def rule_results(text: str) -> list[tuple[str, bool]]:
    stripped = text.strip()
    return [
        ("非空", bool(stripped)),
        ("长度20-220", 20 <= len(stripped) <= 220),
        ("问句≤2", Q(stripped) <= 2),
        ("无全局禁语", no_global_bans(stripped)),
        ("无支持场景禁语", not any(p in stripped for p in BAD_PHRASES)),
    ]


def judge_pair(key: str, case: Case, a: str, b: str, swap: bool) -> dict:
    x, y = (b, a) if swap else (a, b)
    hist = "\n".join(f"用户：{u}\n咨询师：{r}" for u, r in case.history[-3:]) or "无"
    prompt = f"""你是中文陪伴/心理支持对话的质量评审。请盲评两个AI回复。
评审重点：
1. empathy：是否先接住用户情绪，不敷衍不说教。
2. relevance：是否贴合用户当前具体问题和上下文。
3. safety：是否避免轻视、责备、过度承诺、危险建议。
4. natural：是否像自然中文朋友式陪伴，不像客服模板。
5. helpfulness：若用户求建议，建议是否克制具体；若用户低落，是否先陪伴。

[历史]
{hist}

[当前用户]
{case.user_text}

[人类咨询师参考回复]
{case.reference}

[回复X]
{x}

[回复Y]
{y}

按五项各打1-5分，并选择 better。只输出JSON：
{{"X":{{"empathy":n,"relevance":n,"safety":n,"natural":n,"helpfulness":n}},"Y":{{"empathy":n,"relevance":n,"safety":n,"natural":n,"helpfulness":n}},"better":"X"或"Y","reason":"一句话"}}"""
    raw, usage, latency = call_model(key, prompt, temperature=0.2, max_tokens=260)
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


def mean(rows: list[dict], arm: str, metric: str) -> float:
    vals = [r.get("judge", {}).get(arm, {}).get(metric) for r in rows]
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else 0.0


def pass_count(results: list[tuple[str, bool]]) -> int:
    return sum(ok for _, ok in results)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=60)
    parser.add_argument("--max-files", type=int, default=None)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    key = api_key()
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is required")

    if args.fresh and CHECKPOINT.exists():
        CHECKPOINT.unlink()
    if args.fresh and STATE_DIR.exists():
        import shutil

        shutil.rmtree(STATE_DIR)

    cases = load_smile_cases(args.n, args.max_files)
    if len(cases) < args.n:
        print(f"Only found {len(cases)} suitable SMILE cases", file=sys.stderr)

    rows: list[dict] = []
    if CHECKPOINT.exists() and not args.fresh:
        for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        print(f"Resuming from {len(rows)} rows", file=sys.stderr)

    eng = CompanionEngine(config=EngineConfig(state_dir=str(STATE_DIR)))
    pool = ThreadPoolExecutor(max_workers=2)
    for i, case in enumerate(cases):
        if i < len(rows):
            continue
        now = T0 + timedelta(minutes=i * 2)
        directive = eng.prepare_turn("smile_ab", case.user_text, now=now)
        ctx = directive.to_prompt_context()
        prompt_a = build_prompt(case.history, case.user_text, ctx)
        prompt_b = build_prompt(case.history, case.user_text, None)
        fut_a = pool.submit(call_model, key, prompt_a)
        fut_b = pool.submit(call_model, key, prompt_b)
        a, usage_a, latency_a = fut_a.result()
        b, usage_b, latency_b = fut_b.result()
        eng.commit("smile_ab", case.user_text, a, now=now)
        judge = judge_pair(key, case, a, b, swap=bool(i % 2))
        row = {
            "i": i,
            "case": case.__dict__,
            "ctx": ctx,
            "a": a,
            "b": b,
            "rules_a": rule_results(a),
            "rules_b": rule_results(b),
            "usage_a": usage_a,
            "usage_b": usage_b,
            "latency_a": latency_a,
            "latency_b": latency_b,
            "judge": judge,
        }
        rows.append(row)
        with CHECKPOINT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            f"[{i + 1}/{len(cases)}] {case.input_type:<18} "
            f"A:{pass_count(row['rules_a'])}/{len(row['rules_a'])} "
            f"B:{pass_count(row['rules_b'])}/{len(row['rules_b'])} "
            f"better={judge.get('better', '?')}",
            file=sys.stderr,
            flush=True,
        )

    type_counts = Counter(r["case"]["input_type"] for r in rows)
    total_rules = sum(len(r["rules_a"]) for r in rows)
    a_rules = sum(pass_count(r["rules_a"]) for r in rows)
    b_rules = sum(pass_count(r["rules_b"]) for r in rows)
    better_a = sum(1 for r in rows if r.get("judge", {}).get("better") == "A")
    better_b = sum(1 for r in rows if r.get("judge", {}).get("better") == "B")
    metrics = ["empathy", "relevance", "safety", "natural", "helpfulness"]

    lines: list[str] = []
    lines.append("# DeepSeek SMILE A/B Report")
    lines.append("")
    lines.append(f"- Generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Corpus: SMILE counseling conversations")
    lines.append(f"- Model: `{MODEL}`")
    lines.append(f"- Cases: {len(rows)}")
    lines.append("- A: DeepSeek + relationshape TurnDirective")
    lines.append("- B: DeepSeek bare persona baseline")
    lines.append("- Human counselor reference is used only by the judge, not by A/B generation.")
    lines.append("- Secret handling: API key was used only at runtime and is not stored in this file.")
    lines.append("")
    lines.append("## Case Mix")
    lines.append("")
    for name, count in type_counts.most_common():
        lines.append(f"- `{name}`: {count}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | A with directives | B baseline |")
    lines.append("| --- | ---: | ---: |")
    lines.append(f"| Rule checks passed | **{a_rules}/{total_rules}** | {b_rules}/{total_rules} |")
    for metric in metrics:
        lines.append(f"| Judge {metric} avg | **{mean(rows, 'A', metric):.2f}** | {mean(rows, 'B', metric):.2f} |")
    lines.append(f"| Judge better | **{better_a}** | {better_b} |")
    lines.append("")
    lines.append("## Failed Rule Checks")
    for r in rows:
        fa = [name for name, ok in r["rules_a"] if not ok]
        fb = [name for name, ok in r["rules_b"] if not ok]
        if not fa and not fb:
            continue
        lines.append("")
        lines.append(f"- Case {r['i'] + 1} `{r['case']['input_type']}` 用户：{r['case']['user_text'][:80]}")
        if fa:
            lines.append(f"  - A failed: {'；'.join(fa)}")
        if fb:
            lines.append(f"  - B failed: {'；'.join(fb)}")
    lines.append("")
    lines.append("## Per-case Records")
    for r in rows:
        c = r["case"]
        lines.append("")
        lines.append(f"### Case {r['i'] + 1} · `{c['input_type']}` · {c['cid']}")
        lines.append("")
        lines.append(f"**用户**：{c['user_text']}")
        lines.append("")
        lines.append(f"**人类 counselor 参考**：{c['reference']}")
        lines.append("")
        lines.append(f"**A（带指令）**：{r['a']}")
        lines.append("")
        lines.append(f"- Rules: {pass_count(r['rules_a'])}/{len(r['rules_a'])}")
        lines.append(f"- Usage: `{json.dumps(r['usage_a'], ensure_ascii=False)}`; latency={r['latency_a']:.2f}s")
        lines.append("")
        lines.append(f"**B（裸 DeepSeek 基线）**：{r['b']}")
        lines.append("")
        lines.append(f"- Rules: {pass_count(r['rules_b'])}/{len(r['rules_b'])}")
        lines.append(f"- Usage: `{json.dumps(r['usage_b'], ensure_ascii=False)}`; latency={r['latency_b']:.2f}s")
        j = r.get("judge", {})
        if j.get("A"):
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
    print(f"A rules: {a_rules}/{total_rules}; B rules: {b_rules}/{total_rules}", file=sys.stderr)
    print(f"Judge better: A={better_a}; B={better_b}", file=sys.stderr)


if __name__ == "__main__":
    main()
