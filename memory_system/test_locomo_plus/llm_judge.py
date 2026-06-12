"""
LLM-as-Judge module for LoCoMo-Plus evaluation.

100% compatible with the official LoCoMo-Plus evaluation framework.
Scoring: correct=1.0, partial=0.5, wrong=0.0

Six categories with dedicated judge prompts:
  multi-hop, single-hop, temporal, common-sense, adversarial, Cognitive

Uses async OpenAI client (qwen-plus via dashscope-compatible API by default).
"""

import asyncio
import json
import os
import re
import time
from collections import defaultdict
from typing import Optional

# ---------------------------------------------------------------------------
# Prompt templates — copied EXACTLY from the official LoCoMo-Plus code
# (evaluation_framework/task_eval/prompt.py  PROMPT_TEMPLATES)
# ---------------------------------------------------------------------------

PROMPT_TEMPLATES = {
    "multi-hop": """
You are a Fact-Checking Judge.
Your task: Compare the model's prediction with the reference answer (multi-hop fact QA).

Labels:
- "correct": The answer matches the reference entities (names, places, times) exactly.
- "partial": The answer misses some details or contains minor inaccuracies but gets the main entity right.
- "wrong": The answer is factually incorrect or hallucinates details not in the reference.

Reference Answer:
{gold}

Model Prediction:
{pred}

Relevant Evidence:
{evidence}

Return your judgment strictly in JSON format:
{{"label": "correct"|"partial"|"wrong", "reason": "<short explanation>"}}
""",

    "single-hop": """
You are a Fact-Checking Judge.
Your task: Compare the model's prediction with the reference answer (single-hop fact QA).

Labels:
- "correct": The answer matches the reference entities exactly.
- "partial": The answer misses some details but gets the main entity right.
- "wrong": The answer is factually incorrect or hallucinates details not in the reference.

Reference Answer:
{gold}

Model Prediction:
{pred}

Relevant Evidence:
{evidence}

Return your judgment strictly in JSON format:
{{"label": "correct"|"partial"|"wrong", "reason": "<short explanation>"}}
""",

    "temporal": """
You are a Temporal Logic Judge.
Your task: Check the calculation, duration, or sequence of events.

Labels:
- "correct": The calculated time, duration, or date matches the reference exactly (semantic equivalents are allowed).
- "wrong": The calculation is incorrect, the sequence is reversed, or the specific time is wrong.

Reference Answer:
{gold}

Model Prediction:
{pred}

Relevant Evidence:
{evidence}

Return your judgment strictly in JSON format:
{{"label": "correct"|"wrong", "reason": "<short explanation>"}}
""",

    "common-sense": """
You are a Knowledge Logic Judge.
Your task: Assess if the prediction applies correct commonsense/world knowledge consistent with the reference.

Labels:
- "correct": The logic and inference are sound and match the reference conclusion.
- "partial": The reasoning is mostly correct but the final conclusion is vague or slightly off.
- "wrong": The reasoning contradicts commonsense or the reference.

Reference Answer:
{gold}

Model Prediction:
{pred}

Relevant Evidence:
{evidence}

Return your judgment strictly in JSON format:
{{"label": "correct"|"partial"|"wrong", "reason": "<short explanation>"}}
""",

    "adversarial": """
You are a Skeptical Judge evaluating robustness.
The question is inherently misleading (e.g., asks about something not in the conversation).
Your task: Judge whether the model's answer conveys that "this was not mentioned in the conversation" (or equivalent refusal).

Labels:
- "correct": The prediction clearly conveys that the information was not mentioned / cannot be answered from the conversation. Score it.
- "wrong": The prediction does NOT convey that meaning—e.g., it gives a concrete answer or does not refuse. Do not score.


Model Prediction:
{pred}

Return your judgment strictly in JSON format:
{{"label": "correct"|"wrong", "reason": "<short explanation>"}}
""",

    "Cognitive": """
You are a Memory Awareness Judge.
Your task: Judge whether the Model Prediction considers or is linked to the Evidence. If there is a clear connection, the answer is correct (score 1); if not, it is wrong (no score).

Labels:
- "correct": The prediction explicitly or implicitly reflects/uses the evidence (memory or constraint). Give 1 point.
- "wrong": The prediction does not show such a link to the evidence. No point.

Memory/Evidence:
{evidence}

Model Prediction:
{pred}

Return your judgment strictly in JSON format:
{{"label": "correct"|"wrong", "reason": "<Does the prediction relate to the evidence?>"}}
""",

    "default": """
You are an expert evaluator.
Your task: Compare the prediction with the reference.

Labels:
- "correct": Factually consistent with the reference.
- "partial": Contains correct info but is incomplete.
- "wrong": Factually incorrect.

Reference Answer:
{gold}

Model Prediction:
{pred}

Relevant Evidence:
{evidence}

Return your judgment strictly in JSON format:
{{"label": "correct"|"partial"|"wrong", "reason": "<short explanation>"}}
""",
}

# ---------------------------------------------------------------------------
# Scoring map — matches official: correct=1, partial=0.5, wrong=0
# ---------------------------------------------------------------------------

LABEL_TO_SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL = "qwen-plus"

# Maximum retries on transient / rate-limit errors
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 2.0  # seconds; exponential back-off multiplier


def _get_base_url() -> str:
    return (os.environ.get("OPENAI_BASE_URL") or _DEFAULT_BASE_URL).strip()


def _get_api_key() -> str:
    key = os.environ.get("MEMORY_QWEN_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not key.strip():
        raise ValueError(
            "No API key found. Set MEMORY_QWEN_API_KEY or OPENAI_API_KEY in the environment."
        )
    return key.strip()


def _get_model() -> str:
    return (os.environ.get("JUDGE_MODEL") or _DEFAULT_MODEL).strip()


# ---------------------------------------------------------------------------
# Async OpenAI client (lazy singleton)
# ---------------------------------------------------------------------------

_async_client = None


def _get_async_client():
    """Return a cached AsyncOpenAI client (created on first call)."""
    global _async_client
    if _async_client is None:
        from openai import AsyncOpenAI

        _async_client = AsyncOpenAI(
            api_key=_get_api_key(),
            base_url=_get_base_url(),
        )
    return _async_client


def reset_client():
    """Force re-creation of the async client (e.g. after env change)."""
    global _async_client
    _async_client = None


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def get_judge_prompt(
    category: str,
    evidence: str,
    pred: str,
    gold: str = "",
) -> str:
    """
    Build the judge prompt for *category* by filling the template with
    gold, pred, evidence.  Uses the default template for unknown categories.
    Exactly mirrors the official get_judge_prompt().
    """
    template = PROMPT_TEMPLATES.get(category) or PROMPT_TEMPLATES["default"]
    return template.format(
        gold=gold or "",
        pred=pred or "",
        evidence=evidence or "",
    )


# ---------------------------------------------------------------------------
# Response parsing  (mirrors official _parse_judge_response)
# ---------------------------------------------------------------------------


def parse_judge_response(raw: str) -> tuple:
    """
    Parse label and reason from the LLM judge output.

    Strategy (same as official):
      1. Regex for JSON-like {..."label": "...", "reason": "..."...}
      2. Fallback: json.loads on the full string
      3. Fallback: keyword scan (correct > wrong > partial)

    Returns (label: str, reason: str).
    """
    label, reason = "", ""
    raw = (raw or "").strip()
    try:
        # Strategy 1 — regex for JSON block
        m = re.search(
            r'\{[^{}]*"label"\s*:\s*["\']([^"\']+)["\']\s*[^{}]*"reason"\s*:\s*["\']([^"\']*)["\']',
            raw,
            re.DOTALL,
        )
        if m:
            label = m.group(1).strip()
            reason = (m.group(2) or "").strip()
        else:
            # Strategy 2 — plain json.loads
            obj = json.loads(raw)
            label = (obj.get("label") or "").strip()
            reason = (obj.get("reason") or "").strip()
    except Exception:
        # Strategy 3 — keyword fallback
        lower = raw.lower()
        if "correct" in lower:
            label = "correct"
        elif "wrong" in lower:
            label = "wrong"
        elif "partial" in lower:
            label = "partial"
        reason = raw[:200] if raw else ""
    return label, reason


# ---------------------------------------------------------------------------
# Label -> score
# ---------------------------------------------------------------------------


def label_to_score(label: str) -> float:
    """Map judge label to numeric score.  correct=1, partial=0.5, wrong=0.
    Unknown / empty labels map to 0."""
    return LABEL_TO_SCORE.get((label or "").strip().lower(), 0.0)


# ---------------------------------------------------------------------------
# Single-record judge (async)
# ---------------------------------------------------------------------------


async def judge_one(
    category: str,
    evidence: str,
    pred: str,
    gold: str = "",
    model: Optional[str] = None,
) -> dict:
    """
    Judge a single prediction asynchronously via the OpenAI-compatible API.

    Returns dict with keys: label, reason, score.
    Retries with exponential back-off on transient / rate-limit errors.
    """
    model = model or _get_model()
    prompt = get_judge_prompt(category, evidence, pred, gold)
    client = _get_async_client()

    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=512,
            )
            raw = (response.choices[0].message.content or "").strip()
            label, reason = parse_judge_response(raw)
            score = label_to_score(label)
            return {"label": label, "reason": reason, "score": score}
        except Exception as exc:
            last_error = exc
            err_str = str(exc).lower()
            # Rate-limit or transient server errors → retry
            is_retryable = any(
                kw in err_str
                for kw in ("rate", "limit", "429", "500", "502", "503", "timeout", "connection")
            )
            if is_retryable and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
            break

    # All retries exhausted — return error result
    return {
        "label": "",
        "reason": f"[judge_error after {_MAX_RETRIES} retries: {last_error}]",
        "score": 0.0,
    }


# ---------------------------------------------------------------------------
# Batch judge (async with concurrency control)
# ---------------------------------------------------------------------------


async def judge_batch(
    records: list,
    concurrency: int = 5,
    model: Optional[str] = None,
) -> list:
    """
    Judge a list of records in parallel with bounded concurrency.

    Each record is a dict that must contain at least:
      - category (str)
      - prediction (str)
    And optionally:
      - evidence (str)
      - ground_truth / answer (str)

    Returns a list of dicts (same order) — each record augmented with
    judge_label, judge_reason, judge_score.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _judge_with_sem(idx: int, rec: dict) -> tuple:
        async with sem:
            cat = rec.get("category") or "default"
            evidence = rec.get("evidence", "")
            pred = rec.get("prediction", "")
            gold = rec.get("ground_truth") or rec.get("answer", "") or ""
            result = await judge_one(cat, evidence, pred, gold, model=model)
            out = dict(rec)
            out["judge_label"] = result["label"]
            out["judge_reason"] = result["reason"]
            out["judge_score"] = result["score"]
            return idx, out

    tasks = [_judge_with_sem(i, r) for i, r in enumerate(records)]
    results = [None] * len(records)

    # Use asyncio.as_completed for progress indication
    done_count = 0
    total = len(records)
    for coro in asyncio.as_completed(tasks):
        idx, out = await coro
        results[idx] = out
        done_count += 1
        if total > 1 and (done_count % max(1, total // 10) == 0 or done_count == total):
            print(f"  Judge progress: {done_count}/{total}")

    return results


# ---------------------------------------------------------------------------
# Summary computation (mirrors official _compute_summary)
# ---------------------------------------------------------------------------


def compute_summary(results: list) -> dict:
    """
    Aggregate judge scores by category.

    Returns:
      {
        total_score: float,
        total_samples: int,
        max_possible: int,
        overall_avg: float,
        by_category: {
          <cat>: {score: float, count: int, avg: float}
        }
      }
    """
    total_score = 0.0
    by_cat = defaultdict(lambda: {"score": 0.0, "count": 0})

    for r in results:
        s = float(r.get("judge_score", 0.0))
        total_score += s
        cat = r.get("category") or "default"
        by_cat[cat]["score"] += s
        by_cat[cat]["count"] += 1

    n = len(results)
    summary = {
        "total_score": round(total_score, 2),
        "total_samples": n,
        "max_possible": n,
        "overall_avg": round(total_score / n, 4) if n else 0.0,
        "by_category": {},
    }
    for cat, v in sorted(by_cat.items()):
        cnt = v["count"]
        summary["by_category"][cat] = {
            "score": round(v["score"], 2),
            "count": cnt,
            "avg": round(v["score"] / cnt, 4) if cnt else 0.0,
        }
    return summary


# ---------------------------------------------------------------------------
# Pretty-print summary (mirrors official _print_summary)
# ---------------------------------------------------------------------------


def print_summary(summary: dict) -> None:
    """Print formatted score summary to stdout (terminal-friendly)."""
    print("")
    print("=" * 60)
    print("Judge score summary")
    print("=" * 60)
    print(f"  Total samples: {summary['total_samples']}")
    print(f"  Total score:   {summary['total_score']} / {summary['max_possible']}")
    print(f"  Average:       {summary['overall_avg']} (correct=1, partial=0.5, wrong=0)")
    print("-" * 60)
    print("  By category:")
    for cat, v in summary["by_category"].items():
        print(f"    {cat}: score {v['score']} / {v['count']} samples, avg {v['avg']}")
    print("=" * 60)
    print("")


# ---------------------------------------------------------------------------
# Synchronous convenience wrappers
# ---------------------------------------------------------------------------


def judge_one_sync(
    category: str,
    evidence: str,
    pred: str,
    gold: str = "",
    model: Optional[str] = None,
) -> dict:
    """Synchronous wrapper around judge_one()."""
    return asyncio.run(judge_one(category, evidence, pred, gold, model=model))


def judge_batch_sync(
    records: list,
    concurrency: int = 5,
    model: Optional[str] = None,
) -> list:
    """Synchronous wrapper around judge_batch()."""
    return asyncio.run(judge_batch(records, concurrency=concurrency, model=model))


# ---------------------------------------------------------------------------
# __main__  — simple self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("llm_judge.py  — LoCoMo-Plus compatible LLM-as-Judge")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Offline tests (no API call)
    # ------------------------------------------------------------------
    print("\n[1] Testing get_judge_prompt() for each category...")
    for cat in ["multi-hop", "single-hop", "temporal", "common-sense",
                "adversarial", "Cognitive", "unknown-cat"]:
        p = get_judge_prompt(cat, evidence="Alice went hiking", pred="Alice went hiking", gold="Alice went hiking")
        tag = "OK" if "{gold}" not in p and "{pred}" not in p else "FAIL"
        print(f"  {cat}: {tag}  (len={len(p)})")

    print("\n[2] Testing parse_judge_response()...")
    cases = [
        ('{"label": "correct", "reason": "matches"}', "correct", "matches"),
        ('{"label": "wrong", "reason": "nope"}', "wrong", "nope"),
        ('{"label": "partial", "reason": "close"}', "partial", "close"),
        ("some garbage with correct in it", "correct", ""),
        ("totally wrong answer", "wrong", ""),
        ("partial match", "partial", ""),
        ("", "", ""),
    ]
    for raw, expect_label, _ in cases:
        label, reason = parse_judge_response(raw)
        tag = "OK" if label == expect_label else "FAIL"
        print(f"  {tag}  input={raw[:50]!r}  -> label={label!r}")

    print("\n[3] Testing label_to_score()...")
    for lbl, expect in [("correct", 1.0), ("partial", 0.5), ("wrong", 0.0),
                         ("Correct", 1.0), ("WRONG", 0.0), ("", 0.0), ("unknown", 0.0)]:
        s = label_to_score(lbl)
        tag = "OK" if s == expect else "FAIL"
        print(f"  {tag}  label={lbl!r} -> score={s}")

    print("\n[4] Testing compute_summary()...")
    mock_results = [
        {"category": "multi-hop", "judge_score": 1.0},
        {"category": "multi-hop", "judge_score": 0.5},
        {"category": "temporal", "judge_score": 1.0},
        {"category": "temporal", "judge_score": 0.0},
        {"category": "adversarial", "judge_score": 1.0},
        {"category": "Cognitive", "judge_score": 0.0},
    ]
    summary = compute_summary(mock_results)
    print_summary(summary)

    # ------------------------------------------------------------------
    # 2. Live API test (only if API key is available)
    # ------------------------------------------------------------------
    api_key = os.environ.get("MEMORY_QWEN_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if api_key.strip():
        print("\n[5] Live API test (judge_one)...")
        try:
            result = judge_one_sync(
                category="single-hop",
                evidence="Bob mentioned he lives in San Francisco.",
                pred="Bob lives in San Francisco.",
                gold="San Francisco",
            )
            print(f"  label={result['label']}  score={result['score']}  reason={result['reason'][:80]}")
        except Exception as e:
            print(f"  API call failed: {e}")

        print("\n[6] Live API test (judge_batch, 3 records)...")
        try:
            test_records = [
                {
                    "category": "multi-hop",
                    "evidence": "Alice met Bob on Monday. Bob met Carol on Tuesday.",
                    "prediction": "Alice met Carol indirectly through Bob.",
                    "ground_truth": "Alice -> Bob -> Carol",
                },
                {
                    "category": "adversarial",
                    "evidence": "",
                    "prediction": "I don't have information about that in the conversation.",
                    "ground_truth": "",
                },
                {
                    "category": "Cognitive",
                    "evidence": "User previously mentioned a fear of heights.",
                    "prediction": "I remember you mentioned being afraid of heights.",
                    "ground_truth": "",
                },
            ]
            results = judge_batch_sync(test_records, concurrency=3)
            for r in results:
                print(f"  {r['category']}: label={r['judge_label']}  score={r['judge_score']}")
            s = compute_summary(results)
            print_summary(s)
        except Exception as e:
            print(f"  Batch call failed: {e}")
    else:
        print("\n[5-6] Skipped live API tests (no MEMORY_QWEN_API_KEY or OPENAI_API_KEY set).")

    print("\nDone.")
