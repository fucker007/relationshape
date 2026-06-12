"""
api/routers/dashboard.py — Memory Visualization Dashboard

Routes:
  GET  /dashboard/              → dashboard HTML
  GET  /dashboard/api/stats     → aggregate counts
  GET  /dashboard/api/owners    → owner list with person counts
  GET  /dashboard/api/persons   → persons for an owner
  GET  /dashboard/api/person/{id} → full person detail
  GET  /dashboard/api/sessions  → Redis session keys
  GET  /dashboard/api/session/{id} → session messages
  POST /dashboard/api/recall    → invoke graph_recall
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_DB_DSN = "postgresql://memory:memory@localhost:5434/memory"
_STATIC_DIR = Path(__file__).parent.parent / "static"


async def _get_conn() -> asyncpg.Connection:
    return await asyncpg.connect(_DB_DSN)


# ── HTML ─────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard_html():
    html_path = _STATIC_DIR / "dashboard.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

@router.get("/graph", response_class=HTMLResponse)
async def graph_html():
    html_path = _STATIC_DIR / "graph.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="graph.html not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/api/stats")
async def api_stats(request: Request):
    conn = await _get_conn()
    try:
        persons = await conn.fetchval("SELECT COUNT(*) FROM person_nodes")
        events = await conn.fetchval("SELECT COUNT(*) FROM events")
        relationships = await conn.fetchval("SELECT COUNT(*) FROM relationships")
        owners = await conn.fetchval("SELECT COUNT(DISTINCT owner_id) FROM person_nodes")
    finally:
        await conn.close()

    redis = request.app.state.redis
    try:
        keys = await redis._r.keys("session:*")
        sessions = len(keys)
    except Exception:
        sessions = 0

    return {
        "persons": persons,
        "events": events,
        "relationships": relationships,
        "sessions": sessions,
        "owners": owners,
    }


# ── Owners ────────────────────────────────────────────────────────────────────

@router.get("/api/owners")
async def api_owners():
    conn = await _get_conn()
    try:
        rows = await conn.fetch(
            """
            SELECT owner_id::text, COUNT(*) AS person_count
            FROM person_nodes
            GROUP BY owner_id
            ORDER BY person_count DESC
            """
        )
    finally:
        await conn.close()
    return [{"owner_id": r["owner_id"], "person_count": r["person_count"]} for r in rows]


# ── Persons ───────────────────────────────────────────────────────────────────

@router.get("/api/persons")
async def api_persons(owner_id: str = ""):
    conn = await _get_conn()
    try:
        if owner_id:
            rows = await conn.fetch(
                """
                SELECT
                    p.person_id::text,
                    p.owner_id::text,
                    p.name,
                    p.role,
                    p.created_at,
                    (SELECT COUNT(*) FROM events e WHERE p.person_id = ANY(e.participant_ids)) AS event_count,
                    COALESCE(jsonb_array_length(p.preferences), 0) +
                    COALESCE(jsonb_array_length(p.behaviors), 0) +
                    COALESCE(jsonb_array_length(p.aversions), 0) AS attr_count
                FROM person_nodes p
                WHERE p.owner_id = $1::uuid
                ORDER BY p.created_at DESC
                """,
                owner_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT
                    p.person_id::text,
                    p.owner_id::text,
                    p.name,
                    p.role,
                    p.created_at,
                    (SELECT COUNT(*) FROM events e WHERE p.person_id = ANY(e.participant_ids)) AS event_count,
                    COALESCE(jsonb_array_length(p.preferences), 0) +
                    COALESCE(jsonb_array_length(p.behaviors), 0) +
                    COALESCE(jsonb_array_length(p.aversions), 0) AS attr_count
                FROM person_nodes p
                ORDER BY p.created_at DESC
                LIMIT 100
                """
            )
    finally:
        await conn.close()

    return [
        {
            "person_id": r["person_id"],
            "owner_id": r["owner_id"],
            "name": r["name"],
            "role": r["role"],
            "event_count": r["event_count"],
            "attr_count": r["attr_count"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


# ── Person Detail ─────────────────────────────────────────────────────────────

def _parse_jsonb(val: Any) -> Any:
    if val is None:
        return []
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return val


@router.get("/api/person/{person_id}")
async def api_person_detail(person_id: str):
    conn = await _get_conn()
    try:
        person = await conn.fetchrow(
            """
            SELECT
                person_id::text, owner_id::text, name, role,
                identity, preferences, behaviors, aversions, current_focus,
                created_at
            FROM person_nodes
            WHERE person_id = $1::uuid
            """,
            person_id,
        )
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        events = await conn.fetch(
            """
            SELECT
                event_id::text, event_type, action, title, summary,
                participant_names, event_time, importance, emotion_summary,
                created_at
            FROM events
            WHERE $1::uuid = ANY(participant_ids)
            ORDER BY event_time DESC NULLS LAST
            LIMIT 20
            """,
            person_id,
        )

        rels = await conn.fetch(
            """
            SELECT
                r.id::text, r.relation_type, r.sentiment,
                r.from_person_id::text, r.to_person_id::text,
                pf.name AS from_name, pt.name AS to_name
            FROM relationships r
            LEFT JOIN person_nodes pf ON pf.person_id = r.from_person_id
            LEFT JOIN person_nodes pt ON pt.person_id = r.to_person_id
            WHERE r.from_person_id = $1::uuid OR r.to_person_id = $1::uuid
            ORDER BY r.created_at DESC
            LIMIT 30
            """,
            person_id,
        )
    finally:
        await conn.close()

    return {
        "person_id": person["person_id"],
        "owner_id": person["owner_id"],
        "name": person["name"],
        "role": person["role"],
        "identity": _parse_jsonb(person["identity"]),
        "preferences": _parse_jsonb(person["preferences"]),
        "behaviors": _parse_jsonb(person["behaviors"]),
        "aversions": _parse_jsonb(person["aversions"]),
        "current_focus": _parse_jsonb(person["current_focus"]),
        "created_at": person["created_at"].isoformat() if person["created_at"] else None,
        "events": [
            {
                "event_id": e["event_id"],
                "event_type": e["event_type"],
                "action": e["action"],
                "title": e["title"],
                "summary": e["summary"],
                "participant_names": e["participant_names"] or [],
                "event_time": e["event_time"].isoformat() if e["event_time"] else None,
                "importance": float(e["importance"]) if e["importance"] is not None else 0.5,
                "emotion_summary": e["emotion_summary"],
            }
            for e in events
        ],
        "relationships": [
            {
                "id": r["id"],
                "from_person_id": r["from_person_id"],
                "to_person_id": r["to_person_id"],
                "from_name": r["from_name"],
                "to_name": r["to_name"],
                "relation_type": r["relation_type"],
                "sentiment": float(r["sentiment"]) if r["sentiment"] is not None else 0.0,
            }
            for r in rels
        ],
    }


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.get("/api/sessions")
async def api_sessions(request: Request):
    redis = request.app.state.redis
    try:
        keys = await redis._r.keys("session:*")
    except Exception as e:
        logger.error(f"Redis keys error: {e}")
        return []

    result = []
    for key in sorted(keys)[:50]:
        key_str = key.decode() if isinstance(key, bytes) else key
        session_id = key_str.replace("session:", "")
        try:
            items = await redis._r.lrange(key, 0, -1)
            messages = [json.loads(m) for m in items]
            preview = ""
            for m in messages:
                if m.get("role") == "user":
                    preview = m.get("content", "")[:60]
                    break
            result.append({
                "session_id": session_id,
                "message_count": len(messages),
                "preview": preview,
            })
        except Exception:
            result.append({"session_id": session_id, "message_count": 0, "preview": ""})

    return result


# ── Session Detail ────────────────────────────────────────────────────────────

@router.get("/api/session/{session_id}")
async def api_session_detail(session_id: str, request: Request):
    redis = request.app.state.redis
    try:
        items = await redis._r.lrange(f"session:{session_id}", 0, -1)
        return [json.loads(m) for m in items]
    except Exception as e:
        logger.error(f"Redis session error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Recall Test ───────────────────────────────────────────────────────────────

class RecallRequest(BaseModel):
    owner_id: str
    user_name: str
    message: str
    session_id: str | None = None


@router.post("/api/recall")
async def api_recall(req: RecallRequest, request: Request):
    from retrieval.graph_recall import graph_recall
    from storage.pg_store import GraphStore

    gs: GraphStore = request.app.state.gs

    # Resolve person_id
    try:
        node = await gs.get_person_by_name(req.owner_id, req.user_name)
        if not node:
            node = await gs.upsert_person_node(req.owner_id, req.user_name, "primary")
        person_id = str(node["person_id"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Person lookup failed: {e}")

    t0 = time.perf_counter()
    try:
        result = await graph_recall(gs, req.owner_id, person_id, req.message)
    except Exception as e:
        logger.error(f"graph_recall error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    latency = (time.perf_counter() - t0) * 1000

    return {
        "person_id": person_id,
        "intent": result.intent,
        "confidence": result.confidence,
        "latency_ms": latency,
        "profile_summary": result.profile_summary,
        "source": result.source,
        "events": [
            {
                "event_type": e.get("event_type", ""),
                "summary": e.get("summary", ""),
                "event_time": str(e.get("event_time", "")),
                "participants": e.get("participant_names", []),
                "importance": e.get("importance", 0.5),
            }
            for e in (result.events or [])
        ],
    }
