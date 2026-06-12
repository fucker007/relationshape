"""
Graph Memory API - 对齐 chat.py 的 Person Graph 架构
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import get_gs, get_llm
from llm.client import LLMClient
from storage.pg_store import GraphStore

router = APIRouter(prefix="/graph", tags=["graph-memories"])


# ── Request/Response Models ──

class GraphRecallRequest(BaseModel):
    owner_id: str
    person_id: str
    message: str


class GraphRecallResponse(BaseModel):
    intent: str
    source: str
    confidence: str
    profile_summary: str | None = None
    events: list[dict] = Field(default_factory=list)
    latency_ms: float


class WindowedExtractRequest(BaseModel):
    owner_id: str
    person_id: str
    session_id: str
    user_name: str
    session_buffer: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]


class WindowedExtractResponse(BaseModel):
    task_id: str
    status: str = "queued"


# ── Endpoints ──

@router.post("/recall", response_model=GraphRecallResponse)
async def graph_recall_api(
    req: GraphRecallRequest,
    gs: Annotated[GraphStore, Depends(get_gs)],
) -> GraphRecallResponse:
    """召回：对齐 chat.py 的 graph_recall()"""
    from retrieval.graph_recall import graph_recall

    t0 = time.perf_counter()
    result = await graph_recall(gs, req.owner_id, req.person_id, req.message)
    latency = (time.perf_counter() - t0) * 1000

    return GraphRecallResponse(
        intent=result.intent,
        source=result.source,
        confidence=result.confidence,
        profile_summary=result.profile_summary,
        events=result.events,
        latency_ms=latency,
    )


@router.post("/extract", response_model=WindowedExtractResponse)
async def windowed_extract_api(
    req: WindowedExtractRequest,
    gs: Annotated[GraphStore, Depends(get_gs)],
    llm: Annotated[LLMClient, Depends(get_llm)],
) -> WindowedExtractResponse:
    """窗口提取：对齐 chat.py 的 background_process()"""
    task_id = str(uuid.uuid4())

    # 异步执行提取（不阻塞响应）
    asyncio.create_task(_background_extract(
        task_id, llm, gs, req.owner_id, req.person_id,
        req.session_id, req.user_name, req.session_buffer
    ))

    return WindowedExtractResponse(task_id=task_id, status="queued")


async def _background_extract(
    task_id: str, llm, gs, owner_id: str, primary_pid: str,
    session_id: str, user_name: str, session_buffer: list[dict]
):
    """后台提取任务 - 完全对齐 chat.py 的 background_process()"""
    try:
        from pipeline.windowed_extractor import WindowedExtractor
        from pipeline.event_extractor import _normalize_time
        from pipeline.relation_extractor import _is_valid_person_name, _infer_relation
        from pipeline.profile_updater import ProfileUpdater
        from pipeline.focus_tracker import FocusTracker
        from pipeline.embedding import embed_text
        from datetime import datetime as dt, timezone as tz, date
        import time as time_module

        updater = ProfileUpdater(gs)
        focus_tracker = FocusTracker(gs)
        extractor = WindowedExtractor(client=llm)

        # 窗口切分
        all_turns = session_buffer
        window = all_turns[-WindowedExtractor.WINDOW_SIZE:]
        context_turns = window[:-WindowedExtractor.TRIGGER_EVERY]
        target_turns = window[-WindowedExtractor.TRIGGER_EVERY:]

        if not target_turns:
            return

        # Step 1: 窗口提取
        result = await extractor.extract(
            context_turns=context_turns,
            target_turns=target_turns,
            user_name=user_name,
        )

        # Step 2: 人物节点写入
        all_names: set[str] = set()
        for ev in result.events:
            all_names.update(ev.participants)
        for attr in result.attributes:
            if attr.target != "self":
                all_names.add(attr.target)
        all_names.discard(user_name)

        name_to_pid: dict[str, str] = {user_name: primary_pid}
        for name in all_names:
            if not _is_valid_person_name(name):
                continue
            node = await gs.upsert_person_node(owner_id, name, "secondary")
            pid = str(node["person_id"])
            name_to_pid[name] = pid
            rtype = _infer_relation(name, " ".join(t["content"] for t in target_turns))
            await gs.upsert_relationship(
                owner_id, primary_pid, pid,
                relation_type=rtype, sentiment_delta=0.0,
            )

        # Step 3: 事件写入
        emotion_to_sentiment = {
            "positive": 0.1, "negative": -0.15, "neutral": 0.0, "mixed": 0.0
        }

        for ev in result.events:
            time_expr = ev.time_expr or ""
            target_text = " ".join(t["content"] for t in ev.raw_turns if t.get("role") == "user")
            when_str, when_raw, normalized = _normalize_time(
                time_expr or target_text, ref=date.today())

            try:
                if when_str and when_str not in ("最近", "过去") and len(when_str) == 10:
                    event_time = dt.strptime(when_str, "%Y-%m-%d").replace(
                        hour=12, tzinfo=tz.utc)
                else:
                    event_time = dt.now(tz.utc)
            except:
                event_time = dt.now(tz.utc)

            # 代词消解
            from pipeline.coreference import resolve_pronouns_llm
            context_persons = list(name_to_pid.keys())
            resolved_text = await resolve_pronouns_llm(
                target_text, context_persons, target_turns, llm
            )

            embedding = await embed_text(resolved_text)

            # participant ids
            p_names = [n for n in ev.participants if n in name_to_pid]
            if user_name not in p_names:
                p_names = [user_name] + p_names
            participant_pids = [name_to_pid[n] for n in p_names if n in name_to_pid]

            emotion_label = ev.emotion_detail or (
                "开心" if ev.emotion == "positive" else
                "难过" if ev.emotion == "negative" else "平静"
            )

            from uuid import uuid4
            event_id = uuid4()
            event_dict = {
                "event_id": event_id,
                "owner_id": owner_id,
                "session_id": str(session_id),
                "event_time": event_time,
                # 截断各 varchar 限制字段，防止超长写入失败
                "event_time_raw": (when_raw or time_expr or "最近")[:50],
                "event_type": (ev.event_type or "daily")[:30],
                "action": (ev.action or "")[:50],
                "title": (ev.action or "")[:12],
                # Use the LLM-generated semantic summary (third-person, one sentence)
                # rather than the raw resolved_text. ev.summary is produced by the
                # event structurer's LLM call and is more concise and retrieval-friendly.
                # resolved_text is still preserved in source_message for full-text reference.
                "summary": ev.summary or resolved_text,
                "participant_ids": participant_pids,
                "participant_names": p_names,
                "scene": (ev.scene or "")[:100] if ev.scene else None,
                "emotion_summary": (emotion_label or "")[:50],
                "importance": 0.7 if ev.emotion == "negative" else 0.5,
                "belief_impact": None,
                "impact": [],
                "embedding": embedding,
                "raw_turns": ev.raw_turns,
                "source_message": target_text[:200],
            }
            await gs.insert_event(event_dict)

            # 关系 sentiment 更新（容错：失败不影响已写入的 event）
            try:
                event_dict["source_text"] = target_text
                await updater.update_from_event(
                    owner_id, primary_pid, event_dict, participant_pids)
            except Exception as _upd_e:
                logger.warning(
                    f"[graph_extract] update_from_event failed (event saved): {_upd_e}"
                )

        # Step 4: 属性更新
        for attr in result.attributes:
            attr_dict = {
                "field": attr.field,
                "key": attr.key,
                "value": attr.value,
                "target": attr.target,
            }
            target = attr.target

            if attr.field == "relationship":
                rel_target = target if target != "self" else None
                if rel_target and _is_valid_person_name(rel_target):
                    sec_node = await gs.get_person_by_name(owner_id, rel_target)
                    if not sec_node:
                        sec_node = await gs.upsert_person_node(owner_id, rel_target, "secondary")
                    from pipeline.relation_extractor import _map_relation_value
                    rtype = _map_relation_value(attr.value)
                    await gs.upsert_relationship(
                        owner_id, primary_pid, str(sec_node["person_id"]),
                        relation_type=rtype, sentiment_delta=0.2,
                    )
                continue

            if target == "self":
                await updater.update_from_attributes(
                    owner_id, primary_pid, [attr_dict])
            elif _is_valid_person_name(target):
                sec_node = await gs.get_person_by_name(owner_id, target)
                if not sec_node:
                    sec_node = await gs.upsert_person_node(owner_id, target, "secondary")
                await updater.update_from_attributes(
                    owner_id, str(sec_node["person_id"]), [attr_dict])

        # Step 4.5: 关系声明正则兜底
        target_text_joined = " ".join(
            t["content"] for t in target_turns if t["role"] == "user")
        await updater.update_from_declaration(
            owner_id, primary_pid, target_text_joined)

        # Step 5: Focus 追踪
        focus_text = " ".join(t["content"] for t in target_turns if t["role"] == "user")
        related = [n for n in name_to_pid if n != user_name]
        await focus_tracker.update(primary_pid, focus_text, related)

    except Exception as e:
        import logging
        logging.error(f"background_extract failed: {e}", exc_info=True)
