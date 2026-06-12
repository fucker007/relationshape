"""
Data loader for the LoCoMo-Plus benchmark dataset.

Loads and parses two JSON sources:
  - locomo10.json : 10 long conversations with 1986 QA pairs (categories 1-5)
  - locomo_plus.json : 401 cognitive QA pairs (causal/state/goal/value)

Usage:
    from locomo_loader import load_locomo10, load_locomo_plus
    samples = load_locomo10()
    cognitive = load_locomo_plus()
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCOMO_CATEGORY_NAMES: Dict[int, str] = {
    1: "multi-hop",
    2: "temporal",
    3: "common-sense",
    4: "single-hop",
    5: "adversarial",
}

CATEGORY_SIXTH: str = "cognitive"

DEFAULT_DATA_DIR: Path = Path(__file__).parent / "Locomo-Plus" / "data"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    """A single dialogue turn."""
    speaker: str
    dia_id: str
    text: str


@dataclass
class SessionData:
    """One session inside a conversation."""
    session_idx: int
    date_time: str
    turns: List[Turn] = field(default_factory=list)


@dataclass
class QAPair:
    """A question-answer pair with evidence and category."""
    question: str
    answer: str
    evidence: List[str]
    category: str        # human-readable name, e.g. 'single-hop'
    raw_category: int    # original integer 1-5


@dataclass
class LocomoSample:
    """One complete conversation sample from locomo10."""
    sample_id: str
    speaker_a: str
    speaker_b: str
    sessions: List[SessionData] = field(default_factory=list)
    qa_pairs: List[QAPair] = field(default_factory=list)


@dataclass
class CognitiveSample:
    """One cognitive QA pair from locomo_plus."""
    relation_type: str       # causal | state | goal | value
    cue_dialogue: str
    trigger_query: str
    time_gap: str
    linked_sample_idx: int   # index into the locomo10 sample list (-1 if unknown)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SESSION_KEY_RE = re.compile(r"^session_(\d+)$")
_EVIDENCE_RE = re.compile(r"^D(\d+):(\d+)$")


def _parse_sessions(conversation: dict) -> List[SessionData]:
    """Extract ordered session data from a conversation dict."""
    # Discover session indices
    indices: List[int] = []
    for key in conversation:
        m = _SESSION_KEY_RE.match(key)
        if m:
            indices.append(int(m.group(1)))
    indices.sort()

    sessions: List[SessionData] = []
    for idx in indices:
        turns_raw = conversation.get(f"session_{idx}", [])
        date_time = conversation.get(f"session_{idx}_date_time", "")
        turns = [
            Turn(speaker=t["speaker"], dia_id=t["dia_id"], text=t["text"])
            for t in turns_raw
        ]
        sessions.append(SessionData(session_idx=idx, date_time=date_time, turns=turns))
    return sessions


def _parse_qa(qa_list: list) -> List[QAPair]:
    """Parse raw QA dicts into QAPair objects."""
    pairs: List[QAPair] = []
    for item in qa_list:
        raw_cat = int(item["category"])
        cat_name = LOCOMO_CATEGORY_NAMES.get(raw_cat, f"unknown-{raw_cat}")
        answer = item.get("answer", "")
        if answer is None:
            answer = ""
        pairs.append(QAPair(
            question=str(item["question"]),
            answer=str(answer),
            evidence=list(item.get("evidence", [])),
            category=cat_name,
            raw_category=raw_cat,
        ))
    return pairs


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

def load_locomo10(data_dir: Optional[Path] = None) -> List[LocomoSample]:
    """Load and parse locomo10.json into a list of LocomoSample."""
    data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    path = data_dir / "locomo10.json"
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    samples: List[LocomoSample] = []
    for entry in raw:
        conv = entry["conversation"]
        sample = LocomoSample(
            sample_id=entry["sample_id"],
            speaker_a=conv.get("speaker_a", ""),
            speaker_b=conv.get("speaker_b", ""),
            sessions=_parse_sessions(conv),
            qa_pairs=_parse_qa(entry.get("qa", [])),
        )
        samples.append(sample)
    return samples


def load_locomo_plus(data_dir: Optional[Path] = None) -> List[CognitiveSample]:
    """Load and parse locomo_plus.json into a list of CognitiveSample."""
    data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    path = data_dir / "locomo_plus.json"
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    samples: List[CognitiveSample] = []
    for i, entry in enumerate(raw):
        samples.append(CognitiveSample(
            relation_type=entry["relation_type"],
            cue_dialogue=entry["cue_dialogue"],
            trigger_query=entry["trigger_query"],
            time_gap=entry.get("time_gap", ""),
            linked_sample_idx=-1,  # no explicit link in the data
        ))
    return samples


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def get_session_turns_as_dialogue(
    sample: LocomoSample,
    session_idx: int,
) -> List[Dict[str, str]]:
    """Return a session's turns as [{role: 'user'/'assistant', content: str}].

    speaker_a -> 'user', speaker_b -> 'assistant'.
    """
    session: Optional[SessionData] = None
    for s in sample.sessions:
        if s.session_idx == session_idx:
            session = s
            break
    if session is None:
        raise ValueError(
            f"Session {session_idx} not found in sample {sample.sample_id}. "
            f"Available: {[s.session_idx for s in sample.sessions]}"
        )

    dialogue: List[Dict[str, str]] = []
    for turn in session.turns:
        if turn.speaker == sample.speaker_a:
            role = "user"
        elif turn.speaker == sample.speaker_b:
            role = "assistant"
        else:
            # Fallback: alternate
            role = "user" if not dialogue or dialogue[-1]["role"] == "assistant" else "assistant"
        dialogue.append({"role": role, "content": turn.text})
    return dialogue


def get_all_qa_by_category(
    samples: List[LocomoSample],
) -> Dict[str, List[QAPair]]:
    """Group all QA pairs across samples by their category name."""
    by_cat: Dict[str, List[QAPair]] = defaultdict(list)
    for sample in samples:
        for qa in sample.qa_pairs:
            by_cat[qa.category].append(qa)
    return dict(by_cat)


def evidence_to_text(
    sample: LocomoSample,
    evidence_list: List[str],
) -> str:
    """Convert evidence references like 'D1:3' to 'Speaker: text' lines.

    Dn maps to session n (1-based), k maps to the k-th turn (1-based).
    Returns a newline-joined string of 'Speaker: text' entries.
    """
    # Build a lookup: (session_idx, turn_pos_1based) -> Turn
    turn_lookup: Dict[tuple, Turn] = {}
    for session in sample.sessions:
        for i, turn in enumerate(session.turns, start=1):
            turn_lookup[(session.session_idx, i)] = turn

    lines: List[str] = []
    for ref in evidence_list:
        m = _EVIDENCE_RE.match(ref.strip())
        if not m:
            lines.append(f"[unresolved: {ref}]")
            continue
        sess_idx = int(m.group(1))
        turn_pos = int(m.group(2))
        turn = turn_lookup.get((sess_idx, turn_pos))
        if turn:
            lines.append(f"{turn.speaker}: {turn.text}")
        else:
            lines.append(f"[missing: {ref}]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI stats
# ---------------------------------------------------------------------------

def _print_stats() -> None:
    """Print dataset statistics for quick inspection."""
    print("=" * 60)
    print("  LoCoMo-Plus Dataset Statistics")
    print("=" * 60)

    # --- locomo10 ---
    try:
        samples = load_locomo10()
    except FileNotFoundError as e:
        print(f"\n[ERROR] Could not load locomo10.json: {e}")
        samples = []

    if samples:
        total_qa = sum(len(s.qa_pairs) for s in samples)
        total_turns = sum(len(t.turns) for s in samples for t in s.sessions)
        total_sessions = sum(len(s.sessions) for s in samples)

        print(f"\nlocomo10.json")
        print(f"  Conversations : {len(samples)}")
        print(f"  Total sessions: {total_sessions}")
        print(f"  Total turns   : {total_turns}")
        print(f"  Total QA pairs: {total_qa}")
        print()

        by_cat = get_all_qa_by_category(samples)
        print("  QA by category:")
        for cat_id in sorted(LOCOMO_CATEGORY_NAMES):
            name = LOCOMO_CATEGORY_NAMES[cat_id]
            count = len(by_cat.get(name, []))
            print(f"    {cat_id}. {name:14s}: {count:4d}")

        print()
        print("  Per-conversation breakdown:")
        for s in samples:
            print(
                f"    {s.sample_id:8s}  speakers=({s.speaker_a}, {s.speaker_b})  "
                f"sessions={len(s.sessions):2d}  qa={len(s.qa_pairs):3d}"
            )

        # Demo: evidence resolution for first QA with evidence
        print()
        demo_sample = samples[0]
        for qa in demo_sample.qa_pairs:
            if qa.evidence:
                print(f"  Evidence demo (sample {demo_sample.sample_id}):")
                print(f"    Q: {qa.question}")
                print(f"    A: {qa.answer}")
                print(f"    Refs: {qa.evidence}")
                text = evidence_to_text(demo_sample, qa.evidence)
                for line in text.split("\n"):
                    print(f"    >> {line}")
                break

    # --- locomo_plus ---
    print()
    try:
        cognitive = load_locomo_plus()
    except FileNotFoundError as e:
        print(f"[ERROR] Could not load locomo_plus.json: {e}")
        cognitive = []

    if cognitive:
        print(f"locomo_plus.json")
        print(f"  Cognitive QA pairs: {len(cognitive)}")
        rel_counts: Dict[str, int] = defaultdict(int)
        for c in cognitive:
            rel_counts[c.relation_type] += 1
        print("  By relation type:")
        for rt in sorted(rel_counts):
            print(f"    {rt:10s}: {rel_counts[rt]:4d}")

    print()
    print("=" * 60)


if __name__ == "__main__":
    _print_stats()
