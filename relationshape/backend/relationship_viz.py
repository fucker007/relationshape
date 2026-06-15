"""Relationship-state summaries for the backend console."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from relationshape import CompanionEngine, EngineConfig
from relationshape.affect import mood_word
from relationshape.memory import _ROLE_WORDS
from relationshape.relationship import stage_label
from relationshape.types import STAGE_ORDER


STAGE_ZH = {
    "stranger": "陌生",
    "acquaintance": "相识",
    "familiar": "熟悉",
    "companion": "同伴",
    "confidant": "知己",
}


def _state_dir() -> str:
    return os.environ.get("RELATIONSHAPE_STATE_DIR", "runtime/relationshape")


def _engine() -> CompanionEngine:
    return CompanionEngine(config=EngineConfig(state_dir=_state_dir()))


def _user_ids() -> list[str]:
    path = Path(_state_dir())
    if not path.exists():
        return []
    return sorted(p.stem for p in path.glob("*.json"))


def _ep_strength(ep, cfg, now: datetime) -> tuple[float, int]:
    days = max(0.0, (now - datetime.fromisoformat(ep.created_at)).total_seconds() / 86400.0)
    half = cfg.memory_half_life_days * (1 + 0.8 * ep.recall_count)
    return ep.salience * (0.5 ** (days / half)), int(days)


def relationship_users() -> dict[str, Any]:
    eng = _engine()
    users = [_build_user_card(eng, user_id) for user_id in _user_ids()]
    users.sort(key=lambda item: (-item["stage_index"], -item["trust"]))
    dist = {stage.value: 0 for stage in STAGE_ORDER}
    for user in users:
        dist[user["stage"]] = dist.get(user["stage"], 0) + 1
    return {
        "state_dir": _state_dir(),
        "users": users,
        "aggregate": {
            "total_users": len(users),
            "total_memories": sum(u["episodes"] for u in users),
            "total_people": sum(u["people"] for u in users),
            "total_jokes": sum(u["inside_jokes"] for u in users),
            "total_sealed": sum(u["sealed"] for u in users),
            "avg_trust": round(sum(u["trust"] for u in users) / len(users), 1) if users else 0,
            "stage_dist": dist,
        },
    }


def relationship_detail(user_id: str) -> dict[str, Any]:
    eng = _engine()
    st = eng._state(user_id)
    cfg = eng.config
    now = datetime.now()
    days_known = (now - datetime.fromisoformat(st.core.first_met)).days if st.core.first_met else 0
    memories = []
    for ep in st.memory.episodes:
        if ep.sensitive:
            continue
        strength, ago = _ep_strength(ep, cfg, now)
        memories.append(
            {
                "t": ep.created_at[:16],
                "text": ep.text,
                "salience": round(ep.salience, 3),
                "strength": round(strength, 3),
                "recalls": ep.recall_count,
                "vulnerable": ep.vulnerability >= 3,
                "days_ago": ago,
                "valence": round(ep.valence, 2),
            }
        )
    memories.sort(key=lambda item: item["t"], reverse=True)
    nodes, edges = _graph(st, memories[:16])
    return {
        "user": _build_user_card(eng, user_id),
        "days_known": days_known,
        "timeline": list(reversed(st.timeline[-80:])),
        "memories": memories[:40],
        "graph": {"nodes": nodes, "edges": edges},
        "ledger": {
            "trust": round(st.ledger.trust, 1),
            "closeness": round(st.ledger.closeness, 1),
            "substantive": st.ledger.substantive_turns,
            "disclosures": st.ledger.disclosures,
            "deep": st.ledger.deep_disclosures,
            "ruptures_open": st.ledger.ruptures_open,
            "repaired": st.ledger.ruptures_repaired,
        },
        "culture": {
            "jokes": [j.label for j in st.adaptation.inside_jokes],
            "lessons": st.adaptation.lessons,
            "address": st.adaptation.address_form,
        },
    }


def _build_user_card(eng: CompanionEngine, user_id: str) -> dict[str, Any]:
    st = eng._state(user_id)
    now = datetime.now()
    days = (now - datetime.fromisoformat(st.core.first_met)).days if st.core.first_met else 0
    last_seen_days = (
        max(0, (now - datetime.fromisoformat(st.core.last_seen)).days) if st.core.last_seen else None
    )
    return {
        "user_id": user_id,
        "name": st.memory.user_name or user_id,
        "stage": st.core.stage.value,
        "stage_index": STAGE_ORDER.index(st.core.stage),
        "stage_label": stage_label(st.core.stage),
        "stage_zh": STAGE_ZH.get(st.core.stage.value, st.core.stage.value),
        "trust": round(st.ledger.trust, 1),
        "closeness": round(st.ledger.closeness, 1),
        "days_known": days,
        "sessions": st.core.sessions,
        "mood_word": mood_word(st.mood),
        "last_seen": st.core.last_seen,
        "last_seen_days": last_seen_days,
        "episodes": sum(1 for e in st.memory.episodes if not e.sensitive),
        "people": len([k for k in st.memory.people if k not in _ROLE_WORDS]),
        "preferences": len(st.memory.preferences),
        "promises_open": sum(1 for p in st.memory.promises if p.status == "open"),
        "inside_jokes": len(st.adaptation.inside_jokes),
        "sealed": sum(1 for e in st.memory.episodes if e.sensitive),
        "trust_spark": [e.get("trust", 0) for e in st.timeline if "trust" in e][-24:],
    }


def _graph(st, memories: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = st.memory.user_name or st.user_id
    nodes = [{"id": "user", "label": root, "type": "user", "weight": 9}]
    edges = []

    def add_node(node_id: str, label: str, node_type: str, weight: int = 3) -> None:
        if not any(n["id"] == node_id for n in nodes):
            nodes.append({"id": node_id, "label": label, "type": node_type, "weight": weight})
        edges.append({"source": "user", "target": node_id, "type": node_type})

    for pref in st.memory.preferences[:10]:
        add_node(f"like:{pref}", pref, "like", 4)
    for aversion in st.memory.aversions[:10]:
        add_node(f"dislike:{aversion}", aversion, "dislike", 4)
    for name, meta in list(st.memory.people.items())[:10]:
        if name not in _ROLE_WORDS:
            add_node(f"person:{name}", f"{name} / {meta.get('relation', '')}", "person", 5)
    for joke in st.adaptation.inside_jokes[:8]:
        add_node(f"joke:{joke.label}", joke.label, "joke", 4)
    for idx, memory in enumerate(memories[:10]):
        add_node(f"memory:{idx}", memory["text"][:36], "memory", max(2, int(memory["strength"] * 8)))
    return nodes, edges
