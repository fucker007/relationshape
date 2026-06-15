"""Evaluate downloaded dialogue corpora against relationshape.

This script has two layers:
1. Gold metrics where a corpus has labels that map to relationshape primitives
   (currently CPED sentiment/emotion/question labels).
2. Replay health metrics for every downloaded corpus: parse success, safety
   trigger rate, input-type distribution, emotion distribution, question rate,
   and prepare_turn latency.

No external model/API is called.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from relationshape import CompanionEngine, EngineConfig, safety  # noqa: E402
from relationshape.perception import perceive  # noqa: E402
from relationshape.types import InputType  # noqa: E402


DATA = ROOT / "eval" / "data" / "corpora"
REPORT = ROOT / "eval" / "CORPORA_EVAL_REPORT.md"
SEED_NOTE = "deterministic streaming order"


@dataclass
class Sample:
    corpus: str
    text: str
    split: str = ""
    gold_sentiment: str | None = None
    gold_emotion: str | None = None
    gold_question: bool | None = None


def iter_json_array(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def iter_cped() -> Iterator[Sample]:
    base = DATA / "cped" / "data" / "CPED"
    for split in ("train", "valid", "test"):
        path = base / f"{split}_split.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                text = (row.get("Utterance") or "").strip()
                if text:
                    da = (row.get("DA") or "").lower()
                    yield Sample(
                        corpus="cped",
                        split=split,
                        text=text,
                        gold_sentiment=(row.get("Sentiment") or "").strip().lower() or None,
                        gold_emotion=(row.get("Emotion") or "").strip().lower() or None,
                        gold_question=("question" in da),
                    )


def iter_esconv() -> Iterator[Sample]:
    for name in ("ESConv.json", "FailedESConv.json"):
        path = DATA / "esconv" / name
        if not path.exists():
            continue
        split = name.removesuffix(".json")
        for conv in iter_json_array(path):
            emotion = str(conv.get("emotion_type") or "").strip().lower() or None
            for turn in conv.get("dialog", []):
                speaker = str(turn.get("speaker") or "").lower()
                if speaker in {"seeker", "speaker"}:
                    text = str(turn.get("content") or "").strip()
                    if text:
                        yield Sample("esconv", text=text, split=split, gold_emotion=emotion)


def _load_smile_file(path: Path) -> list[str]:
    try:
        turns = iter_json_array(path)
    except json.JSONDecodeError:
        return []
    texts = []
    for turn in turns:
        if turn.get("role") == "client":
            text = str(turn.get("content") or "").strip()
            if text:
                texts.append(text)
    return texts


def iter_smile(limit_files: int | None = None) -> Iterator[Sample]:
    base = DATA / "smile" / "data"
    if not base.exists():
        return
    files = sorted(base.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem)
    if limit_files is not None:
        files = files[:limit_files]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for texts in pool.map(_load_smile_file, files, chunksize=64):
            for text in texts:
                yield Sample("smile", text=text, split="data")


def iter_cdconv() -> Iterator[Sample]:
    path = DATA / "cdconv" / "cdconv.txt"
    if not path.exists():
        return
    seen: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for key in ("u1", "b1", "u2", "b2"):
                text = str(row.get(key) or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    yield Sample("cdconv", text=text, split=str(row.get("file") or ""))


def iter_kdconv() -> Iterator[Sample]:
    base = DATA / "kdconv" / "data"
    if not base.exists():
        return
    for path in sorted(base.glob("*/*.json")):
        if path.name.startswith("kb_"):
            continue
        domain = path.parent.name
        split = path.stem
        try:
            dialogs = iter_json_array(path)
        except json.JSONDecodeError:
            continue
        for dialog in dialogs:
            for msg in dialog.get("messages", []):
                text = str(msg.get("message") or "").strip()
                if text:
                    yield Sample(f"kdconv/{domain}", text=text, split=split)


def iter_charactereval() -> Iterator[Sample]:
    path = DATA / "charactereval" / "data" / "test_data.jsonl"
    if not path.exists():
        return
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        rows = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    for row in rows:
        role = str(row.get("role") or "")
        context = str(row.get("context") or "")
        for line in context.splitlines():
            line = line.strip()
            if not line or "：" not in line:
                continue
            speaker, text = line.split("：", 1)
            text = text.strip()
            if text:
                split = role if speaker == role else "context"
                yield Sample("charactereval", text=text, split=split)


def all_corpora(limit_smile_files: int | None) -> list[tuple[str, Iterable[Sample]]]:
    return [
        ("cped", iter_cped()),
        ("esconv", iter_esconv()),
        ("smile", iter_smile(limit_files=limit_smile_files)),
        ("cdconv", iter_cdconv()),
        ("kdconv", iter_kdconv()),
        ("charactereval", iter_charactereval()),
    ]


def pred_sentiment(valence: float) -> str:
    if valence > 0.05:
        return "positive"
    if valence < -0.05:
        return "negative"
    return "neutral"


CPED_EMOTION_MAP = {
    "happy": "happy",
    "content": "happy",
    "warm": "happy",
    "excited": "happy",
    "angry": "angry",
    "annoyed": "disgust",
    "sad": "sad",
    "lonely": "sad",
    "tired": "sad",
    "distress": "sad",
    "anxious": "worried",
    "neutral": "neutral",
    "bored": "neutral",
}


def macro_f1(golds: list[str], preds: list[str], labels: list[str]) -> float:
    scores = []
    for label in labels:
        tp = sum(1 for g, p in zip(golds, preds) if g == label and p == label)
        fp = sum(1 for g, p in zip(golds, preds) if g != label and p == label)
        fn = sum(1 for g, p in zip(golds, preds) if g == label and p != label)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def acc(golds: list, preds: list) -> float:
    return sum(1 for g, p in zip(golds, preds) if g == p) / len(golds) if golds else 0.0


def pct(n: int, d: int) -> float:
    return n / d if d else 0.0


def top(counter: Counter, k: int = 8) -> str:
    total = sum(counter.values())
    if not total:
        return "-"
    return "；".join(f"{name}:{count}({count/total:.1%})" for name, count in counter.most_common(k))


def evaluate_corpus(name: str, samples: Iterable[Sample], limit: int | None) -> dict:
    eng = CompanionEngine(config=EngineConfig(state_dir=tempfile.mkdtemp()))
    n = 0
    chars = []
    latencies = []
    input_types: Counter = Counter()
    emotions: Counter = Counter()
    valences: Counter = Counter()
    safety_hits: Counter = Counter()
    acts: Counter = Counter()
    constraints = 0
    forbidden = 0
    substantive = 0
    questions = 0
    errors = 0

    cped_sent_gold: list[str] = []
    cped_sent_pred: list[str] = []
    cped_emo_gold: list[str] = []
    cped_emo_pred: list[str] = []
    cped_q_gold: list[bool] = []
    cped_q_pred: list[bool] = []

    for sample in samples:
        if limit is not None and n >= limit:
            break
        text = sample.text.strip()
        if not text:
            continue
        n += 1
        chars.append(len(text))
        try:
            t0 = time.perf_counter()
            directive = eng.prepare_turn(f"eval_{name}", text)
            latencies.append(time.perf_counter() - t0)
            frame = directive.frame
            reading = directive.user_emotion
            if frame is not None:
                input_types[frame.input_type.value] += 1
                if frame.substantive:
                    substantive += 1
                if frame.is_question:
                    questions += 1
                if sample.gold_question is not None:
                    cped_q_gold.append(sample.gold_question)
                    cped_q_pred.append(frame.is_question)
            if reading is not None:
                emotions[reading.label] += 1
                valences[pred_sentiment(reading.valence)] += 1
                if sample.gold_sentiment:
                    cped_sent_gold.append(sample.gold_sentiment)
                    cped_sent_pred.append(pred_sentiment(reading.valence))
                if sample.gold_emotion and name == "cped":
                    cped_emo_gold.append(sample.gold_emotion)
                    cped_emo_pred.append(CPED_EMOTION_MAP.get(reading.label, "neutral"))
            if directive.safety is not None:
                safety_hits[directive.safety.category.value] += 1
            for act in directive.acts:
                acts[act.value] += 1
            constraints += len(directive.constraints)
            forbidden += len(directive.forbidden)
        except Exception:  # noqa: BLE001
            errors += 1
        if n and n % 10000 == 0:
            print(f"    {name}: processed {n} samples...", flush=True)

    sent_labels = sorted(set(cped_sent_gold) | set(cped_sent_pred))
    emo_labels = sorted(set(cped_emo_gold) | set(cped_emo_pred))
    result = {
        "name": name,
        "n": n,
        "errors": errors,
        "avg_chars": statistics.mean(chars) if chars else 0.0,
        "p95_chars": statistics.quantiles(chars, n=100)[94] if len(chars) >= 100 else (max(chars) if chars else 0),
        "avg_ms": statistics.mean(latencies) * 1000 if latencies else 0.0,
        "p95_ms": statistics.quantiles(latencies, n=100)[94] * 1000 if len(latencies) >= 100 else ((max(latencies) * 1000) if latencies else 0.0),
        "safety_rate": pct(sum(safety_hits.values()), n),
        "safety_hits": safety_hits,
        "question_rate": pct(questions, n),
        "substantive_rate": pct(substantive, n),
        "input_types": input_types,
        "emotions": emotions,
        "valences": valences,
        "acts": acts,
        "avg_constraints": constraints / n if n else 0.0,
        "avg_forbidden": forbidden / n if n else 0.0,
        "cped_sent_acc": acc(cped_sent_gold, cped_sent_pred),
        "cped_sent_f1": macro_f1(cped_sent_gold, cped_sent_pred, sent_labels),
        "cped_emo_acc": acc(cped_emo_gold, cped_emo_pred),
        "cped_emo_f1": macro_f1(cped_emo_gold, cped_emo_pred, emo_labels),
        "cped_q_acc": acc(cped_q_gold, cped_q_pred),
        "cped_label_n": len(cped_sent_gold),
    }
    return result


def write_report(results: list[dict], args: argparse.Namespace) -> None:
    lines: list[str] = []
    lines.append("# Downloaded Corpora Replay Evaluation")
    lines.append("")
    lines.append(f"- Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- Data root: `{DATA}`")
    lines.append(f"- Order: {SEED_NOTE}")
    lines.append(f"- Limit per corpus: `{args.limit_per_corpus or 'full'}`")
    lines.append(f"- SMILE file limit: `{args.limit_smile_files or 'full'}`")
    lines.append("")
    lines.append("## What This Measures")
    lines.append("")
    lines.append("- CPED has labels, so the report includes sentiment/emotion/question accuracy and macro-F1.")
    lines.append("- Other corpora are dialogue/persona/knowledge corpora without labels that directly map to relationshape, so they are replayed for health metrics: parse count, prepare success, latency, safety trigger rate, input-type distribution, emotion distribution, and act distribution.")
    lines.append("- No external model/API is called.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Corpus | n | errors | avg chars | avg ms | p95 ms | safety | questions | substantive | top input types | top emotions |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |")
    for r in results:
        lines.append(
            f"| {r['name']} | {r['n']} | {r['errors']} | {r['avg_chars']:.1f} | "
            f"{r['avg_ms']:.3f} | {r['p95_ms']:.3f} | {r['safety_rate']:.3%} | "
            f"{r['question_rate']:.1%} | {r['substantive_rate']:.1%} | "
            f"{top(r['input_types'], 4)} | {top(r['emotions'], 4)} |"
        )
    lines.append("")
    cped = next((r for r in results if r["name"] == "cped"), None)
    if cped and cped["cped_label_n"]:
        lines.append("## CPED Label Metrics")
        lines.append("")
        lines.append(f"- labeled rows: {cped['cped_label_n']}")
        lines.append("")
        lines.append("| Target | Accuracy | Macro-F1 |")
        lines.append("| --- | ---: | ---: |")
        lines.append(f"| Sentiment | {cped['cped_sent_acc']:.3f} | {cped['cped_sent_f1']:.3f} |")
        lines.append(f"| Emotion | {cped['cped_emo_acc']:.3f} | {cped['cped_emo_f1']:.3f} |")
        lines.append(f"| Dialog act question | {cped['cped_q_acc']:.3f} | - |")
        lines.append("")
    lines.append("## Details")
    for r in results:
        lines.append("")
        lines.append(f"### {r['name']}")
        lines.append("")
        lines.append(f"- samples: {r['n']}")
        lines.append(f"- errors: {r['errors']}")
        lines.append(f"- avg/p95 chars: {r['avg_chars']:.1f}/{r['p95_chars']:.0f}")
        lines.append(f"- avg/p95 prepare_turn latency: {r['avg_ms']:.3f}ms/{r['p95_ms']:.3f}ms")
        lines.append(f"- safety trigger rate: {r['safety_rate']:.3%} ({top(r['safety_hits'], 6)})")
        lines.append(f"- valence: {top(r['valences'], 6)}")
        lines.append(f"- input types: {top(r['input_types'], 12)}")
        lines.append(f"- emotions: {top(r['emotions'], 12)}")
        lines.append(f"- acts: {top(r['acts'], 12)}")
        lines.append(f"- avg constraints/forbidden per turn: {r['avg_constraints']:.2f}/{r['avg_forbidden']:.2f}")
    lines.append("")
    lines.append("## Interpretation Notes")
    lines.append("")
    lines.append("- High `topic` rates on KDConv/CharacterEval/CDConv are expected: these are knowledge/persona/dialogue-continuity corpora, not emotional-support corpora.")
    lines.append("- ESConv is English; this project currently uses lightweight Chinese-oriented rules, so ESConv replay is mainly a robustness/latency smoke test, not a fair emotional accuracy benchmark.")
    lines.append("- SMILE is closer to the target support domain; higher self-distress/ask-advice rates there are useful for checking empathy-path coverage.")
    lines.append("- Safety trigger rate is a triage signal. High rates should be inspected manually because some corpora contain real crisis statements.")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-per-corpus", type=int, default=None, help="optional sample cap for each corpus")
    parser.add_argument("--limit-smile-files", type=int, default=None, help="optional cap on number of SMILE json files")
    args = parser.parse_args()

    results = []
    for name, samples in all_corpora(args.limit_smile_files):
        print(f"Evaluating {name}...", flush=True)
        result = evaluate_corpus(name, samples, args.limit_per_corpus)
        results.append(result)
        print(
            f"  n={result['n']} errors={result['errors']} avg={result['avg_ms']:.3f}ms "
            f"safety={result['safety_rate']:.3%}",
            flush=True,
        )
    write_report(results, args)
    print(f"\nWrote {REPORT}")


if __name__ == "__main__":
    main()
