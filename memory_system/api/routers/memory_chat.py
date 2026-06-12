"""
api/routers/memory_chat.py — 统一记忆接口

用户只需调用一个接口：
  POST /memory/chat
  传入本轮对话 (user_message + assistant_message)
  系统自动：召回 → 累积 → 每5轮提取

完全对齐 chat.py 的 background_process 流程。
"""
from __future__ import annotations

import asyncio
import uuid
import json
import logging
import os
import time
import traceback
from dataclasses import asdict
from datetime import date, datetime as dt, timezone as tz
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.deps import get_gs, get_llm, get_pg, get_redis
from llm.client import LLMClient
from storage.pg_store import GraphStore, PgStore
from storage.redis_store import RedisStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["memory"])


# ── Request / Response ────────────────────────────────────────────────

class MemoryChatRequest(BaseModel):
    owner_id: str
    user_name: str
    user_message: str
    assistant_message: str
    session_id: str | None = None


class RecallData(BaseModel):
    profile_summary: str = ""
    events: list[dict] = Field(default_factory=list)
    session_context: list[dict] = Field(default_factory=list)  # L0.5 当前会话最近 N 轮
    intent: str = "general"
    confidence: str = "empty"
    source: str = ""
    latency_ms: float = 0.0


class MemoryChatResponse(BaseModel):
    session_id: str
    person_id: str
    recall: RecallData


# ── 召回衰减合并 ─────────────────────────────────────────────────────

_DECAY_FACTOR = 0.6       # 每轮权重衰减到 60%
_DECAY_THRESHOLD = 0.15   # 低于此值的旧事件淘汰
_DECAY_MAX_ITEMS = 10     # 缓存最多保留条数


def _merge_recall_events(
    new_events: list[dict],
    old_cache: list[dict],
) -> list[dict]:
    """
    合并新召回事件与衰减缓存。

    规则：
    - 旧事件 weight × DECAY_FACTOR，低于 THRESHOLD 淘汰
    - 新事件 weight = _relevance（语义相关性，由 graph_recall 标注）
    - 同一 event_id 重新命中：weight = max(衰减后旧权重, 新 relevance)
      · 高相关命中（relevance≥0.7）→ 续命到高权重
      · 低相关命中（relevance=0.35）→ 无法续命，继续衰减
    - 按 weight DESC 排序，取 top MAX_ITEMS
    """
    pool: dict[str, dict] = {}

    # 1. 旧缓存先衰减
    for e in old_cache:
        eid = e.get("event_id")
        if not eid:
            continue
        decayed = e.get("weight", 0.5) * _DECAY_FACTOR
        if decayed >= _DECAY_THRESHOLD:
            pool[eid] = {**e, "weight": round(decayed, 4)}

    # 2. 新召回：按 _relevance 赋权重（不再无条件 1.0）
    _RENEW_THRESHOLD = 0.5  # 只有相关性 ≥ 0.5 的重新命中才能续命
    for e in new_events:
        eid = e.get("event_id")
        if not eid:
            eid = str(e.get("event_id", ""))
        if not eid:
            continue
        eid = str(eid)
        relevance = float(e.get("_relevance", 0.5))

        if eid in pool:
            if relevance >= _RENEW_THRESHOLD:
                # 高相关命中 → 续命到 relevance 或保持旧权重（取大者）
                pool[eid]["weight"] = round(max(pool[eid]["weight"], relevance), 4)
            # 低相关命中（<0.5）→ 不续命，旧权重继续衰减（pool 中已是衰减值）
            pool[eid].update({k: v for k, v in e.items() if k not in ("weight", "_relevance")})
        else:
            # 全新事件：以 relevance 为初始权重
            pool[eid] = {**e, "weight": round(relevance, 4)}

        pool[eid]["event_id"] = eid

    return sorted(
        pool.values(), key=lambda x: x.get("weight", 0), reverse=True
    )[:_DECAY_MAX_ITEMS]


# ── 后台提取 ──────────────────────────────────────────────────────────

# 限制同时提取的任务数，防止后台任务打爆 LLM / DB
_EXTRACT_MAX_CONCURRENT = int(os.environ.get("EXTRACT_MAX_CONCURRENT", "20"))
_extract_semaphore: asyncio.Semaphore | None = None


def _get_extract_semaphore() -> asyncio.Semaphore:
    global _extract_semaphore
    if _extract_semaphore is None:
        _extract_semaphore = asyncio.Semaphore(_EXTRACT_MAX_CONCURRENT)
    return _extract_semaphore


async def _background_extract(
    llm: LLMClient,
    gs: GraphStore,
    owner_id: str,
    primary_pid: str,
    session_id: str,
    user_name: str,
    buffer: list[dict],
    task_id: str | None = None,
    pg: "PgStore | None" = None,
):
    """
    后台提取 — 完全对齐 chat.py::background_process

    P0-1 Outbox: 若传入 task_id+pg，则负责状态迁移 pending→processing→done/failed。
    未传时兼容旧行为（纯内存 task）。
    """
    sem = _get_extract_semaphore()
    async with sem:
        if task_id and pg:
            try:
                await pg.update_task_status(task_id, "processing")
            except Exception as _e:
                logger.warning(f"[outbox] update processing failed: {_e}")
        try:
            await _do_extract(llm, gs, owner_id, primary_pid, session_id, user_name, buffer)
            if task_id and pg:
                try:
                    await pg.update_task_status(task_id, "done")
                except Exception as _e:
                    logger.warning(f"[outbox] update done failed: {_e}")
        except Exception as e:
            logger.error(f"[extract] task={task_id} session={session_id[:8]} failed: {e}")
            if task_id and pg:
                try:
                    await pg.update_task_status(task_id, "failed", error=str(e)[:500])
                except Exception as _e:
                    logger.warning(f"[outbox] update failed status failed: {_e}")
            raise


async def _do_extract(
    llm: LLMClient,
    gs: GraphStore,
    owner_id: str,
    primary_pid: str,
    session_id: str,
    user_name: str,
    buffer: list[dict],
):
    """实际提取逻辑"""
    try:
        from pipeline.windowed_extractor import WindowedExtractor
        from pipeline.event_extractor import _normalize_time
        from pipeline.relation_extractor import _is_valid_person_name, _infer_relation
        from pipeline.profile_updater import ProfileUpdater, _map_relation_value
        from pipeline.focus_tracker import FocusTracker
        from pipeline.embedding import embed_text

        updater = ProfileUpdater(gs)
        focus_tracker = FocusTracker(gs)
        extractor = WindowedExtractor(client=llm)

        t0 = time.perf_counter()

        # 窗口切分
        window = buffer[-WindowedExtractor.WINDOW_SIZE:]

        # 关键修复：TRIGGER_EVERY 是“用户消息轮数”，不是原始 turn 数。
        # 之前直接取 window[-TRIGGER_EVERY:]，实际只拿到最后 5 条原始 turn，
        # 在 user/assistant 交替场景下通常只包含 2~3 条 user 消息，导致自动 extract
        # 比手动 extract 少看了一半目标内容。
        user_turn_indices = [
            i for i, t in enumerate(window)
            if t.get("role") == "user" and t.get("content", "").strip()
        ]
        if not user_turn_indices:
            return

        target_user_indices = set(user_turn_indices[-WindowedExtractor.TRIGGER_EVERY:])
        first_target_idx = min(target_user_indices)

        # context 保留目标窗口之前的所有 turn（包含 assistant，帮助理解上下文）
        context_turns = window[:first_target_idx]
        # target 只保留最后 TRIGGER_EVERY 条 user 消息
        target_turns = [
            t for i, t in enumerate(window)
            if i in target_user_indices and t.get("role") == "user"
        ]

        if not target_turns:
            return

        # Step 1: 窗口事件提取
        # P1 优化：去除 batch attribute_extractor（realtime_identity 每轮都跑，此处重复）
        # identity 写入路径由 _realtime_identity() 在 chat endpoint 侧每轮负责，
        # batch extract 只关心 events。省掉一次 LLM（串行或并发都减 ~600ms p50）。
        import json as _json2

        primary_node = await gs.get_person_node(primary_pid)
        current_identity: dict = {}
        if primary_node:
            current_identity = primary_node["identity"] if isinstance(
                primary_node["identity"], dict
            ) else _json2.loads(primary_node["identity"] or "{}")

        # Layer 1 去重：查询已有事件摘要，注入 prompt 防止重复提取
        try:
            recent_events = await gs.query_events(owner_id, limit=10)
            existing_summaries = [
                e.get("summary", "")[:80] for e in recent_events
                if e.get("summary")
            ]
        except Exception:
            existing_summaries = []

        result = await extractor.extract(
            context_turns=context_turns,
            target_turns=target_turns,
            user_name=user_name,
            existing_events=existing_summaries or None,
        )
        extract_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[extract] session={session_id[:8]} "
            f"{len(result.events)} events, {len(result.attributes)} attrs, "
            f"existing={len(existing_summaries)} ({extract_ms:.0f}ms)"
        )

        # Step 2: 初始化人名映射（不再从 ev.participants 创建节点——LLM participants 不可靠）
        # 节点创建统一交给 Step 3 的 person_name_extractor LLM skill
        from pipeline.person_normalizer import normalize_person_name

        _real_name = (current_identity or {}).get("name")

        name_to_pid: dict[str, str] = {user_name: primary_pid}
        if _real_name:
            name_to_pid[_real_name] = primary_pid

        # Step 3: 事件写入
        emotion_to_sentiment = {
            "positive": 0.1, "negative": -0.15, "neutral": 0.0, "mixed": 0.0
        }

        # P1 优化：per-event 的 coreference + embed + person_name 三路 LLM/GPU 调用
        # 对同一批 events 之间互相独立，用 gather 并发化。
        # N=3 事件时延迟从 ~6s (串行) 降到 ~2s。
        # DB 写入（upsert_person_node / insert_event / upsert_relationship）保留串行，
        # 因为 name_to_pid 跨 event 共享，并发写会制造多人同名节点重复。
        from pipeline.coreference import resolve_pronouns_llm
        from llm.skills.person_name_extractor import extract_person_names

        user_only_turns = [t for t in target_turns if t.get("role") == "user"]

        async def _prep_event(ev):
            """并发安全：仅 LLM + embedding，无 DB 写"""
            base_summary = ev.summary
            coref_persons = [user_name] + [
                n for n in ev.participants if n != user_name
            ]
            coref_persons = [
                n for n in coref_persons if n not in ("助手", "机器人", "小嗨", "AI")
            ]
            try:
                resolved_text = await resolve_pronouns_llm(
                    base_summary, coref_persons, user_only_turns, llm
                )
            except Exception as _ce:
                logger.warning(f"[extract] coreference failed, fallback to raw: {_ce}")
                resolved_text = base_summary

            # embed + person_name 也可并发
            async def _emb():
                try:
                    return await embed_text(resolved_text)
                except Exception as _ee:
                    logger.warning(f"[extract] embed failed: {_ee}")
                    return None

            async def _names():
                names: set[str] = set()
                try:
                    mentioned = await extract_person_names(resolved_text, llm)
                    for p in mentioned:
                        pname = p.get("name", "")
                        if not pname or p.get("confidence", 0) < 0.7:
                            continue
                        names.add(normalize_person_name(pname))
                except Exception:
                    pass
                return names

            emb_val, names_val = await asyncio.gather(_emb(), _names())
            return ev, resolved_text, emb_val, names_val

        _prep_t0 = time.perf_counter()
        prepped = await asyncio.gather(
            *[_prep_event(ev) for ev in result.events],
            return_exceptions=False,
        )
        _prep_ms = (time.perf_counter() - _prep_t0) * 1000
        logger.info(
            f"[extract] step3_prep session={session_id[:8]} "
            f"n={len(prepped)} parallel_llm+embed={_prep_ms:.0f}ms"
        )

        for ev, resolved_text, emb, all_participant_names_set in prepped:
            time_expr = ev.time_expr or ""
            target_text = " ".join(
                t["content"] for t in ev.raw_turns if t.get("role") == "user"
            )
            when_str, when_raw, normalized = _normalize_time(
                time_expr or target_text, ref=date.today()
            )
            try:
                if when_str and when_str not in ("最近", "过去") and len(when_str) == 10:
                    event_time = dt.strptime(when_str, "%Y-%m-%d").replace(
                        hour=12, tzinfo=tz.utc
                    )
                else:
                    event_time = dt.now(tz.utc)
            except ValueError:
                event_time = dt.now(tz.utc)

            all_participant_names = set(all_participant_names_set)

            # 排除用户自己和助手
            all_participant_names.discard(user_name)
            if _real_name:
                all_participant_names.discard(_real_name)
            all_participant_names -= {"助手", "机器人", "小嗨", "AI"}

            # 为每个真实人物创建节点 + 建关系
            for pn in all_participant_names:
                if pn not in name_to_pid and _is_valid_person_name(pn):
                    node = await gs.upsert_person_node(owner_id, pn, "secondary")
                    pid = str(node["person_id"])
                    name_to_pid[pn] = pid
                    rtype = _infer_relation(
                        pn, " ".join(t["content"] for t in target_turns)
                    )
                    await gs.upsert_relationship(
                        owner_id, primary_pid, pid,
                        relation_type=rtype, sentiment_delta=0.0,
                    )

            p_names = [user_name] + [n for n in all_participant_names if n in name_to_pid]
            participant_pids = [
                name_to_pid[n] for n in p_names if n in name_to_pid
            ]

            emotion_label = ev.emotion_detail or (
                "开心" if ev.emotion == "positive" else
                "难过" if ev.emotion == "negative" else "平静"
            )

            event_id = uuid4()
            event_dict = {
                "event_id": event_id,
                "owner_id": owner_id,
                "session_id": session_id,
                "event_time": event_time,
                "event_time_raw": when_raw or time_expr or "最近",
                "event_type": ev.event_type,
                "action": ev.action,
                "title": ev.action[:12],
                "summary": resolved_text,
                "participant_ids": participant_pids,
                "participant_names": p_names,
                "scene": ev.scene,
                "emotion_summary": emotion_label,
                "importance": 0.7 if ev.emotion == "negative" else 0.5,
                "belief_impact": None,
                "impact": [],
                "embedding": emb,
                "raw_turns": ev.raw_turns,
                "source_message": target_text[:200],
            }

            # Layer 2 去重：embedding 相似度检查，>0.85 视为重复
            _is_dup = False
            if emb:
                try:
                    similar = await gs.vector_search_events(owner_id, emb, limit=1)
                    if similar and float(similar[0].get("similarity", 0)) > 0.85:
                        logger.info(
                            f"[dedup] 跳过重复事件: sim={similar[0]['similarity']:.2f} "
                            f"existing=\"{similar[0].get('summary', '')[:40]}\" "
                            f"new=\"{resolved_text[:40]}\""
                        )
                        _is_dup = True
                except Exception as _dup_e:
                    logger.warning(f"[dedup] 去重检查失败: {_dup_e}")

            if not _is_dup:
                try:
                    await gs.insert_event(event_dict)
                except Exception as _ins_e:
                    logger.error(
                        f"[extract] insert_event failed (skipping this event): {_ins_e}"
                        f" | event_type={event_dict.get('event_type')!r}"
                        f" title={event_dict.get('title')!r}"
                        f" action={event_dict.get('action')!r}"
                    )
                    continue  # 跳过这条 event，继续处理下一条

                # T2 修复: 事件写入成功后，把 emotion + summary 写进 relationships.state
                # sentiment_delta 基于情绪强度 (负向事件更激烈)
                _emotion_delta = (
                    -0.8 if ev.emotion == "negative"
                    else 0.5 if ev.emotion == "positive"
                    else 0.0
                )
                for _pn in all_participant_names:
                    _sec_pid = name_to_pid.get(_pn)
                    if not _sec_pid:
                        continue
                    try:
                        await gs.upsert_relationship(
                            owner_id, primary_pid, _sec_pid,
                            relation_type=_infer_relation(_pn, resolved_text),
                            sentiment_delta=_emotion_delta,
                            event_id=str(event_id),
                            event_time=event_time,
                            last_emotion=emotion_label,
                            last_summary=resolved_text,
                        )
                    except Exception as _rel_e:
                        logger.warning(
                            f"[extract] relationship state update failed: {_rel_e}"
                        )

            # 关系 sentiment 更新（无论是否重复都更新关系）
            # 用 try/except 包裹，emotion/profile 更新失败不应回滚已写入的 event
            try:
                event_dict["source_text"] = target_text
                await updater.update_from_event(
                    owner_id, primary_pid, event_dict, participant_pids
                )
            except Exception as _upd_e:
                logger.warning(
                    f"[extract] update_from_event failed (event already saved): {_upd_e}"
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

            # 用户自报真实姓名时，同步更新 person_nodes.name 列
            if (attr.field == "identity" and attr.key in ("name", "姓名", "叫")
                    and target == "self" and _is_valid_person_name(attr.value)):
                await gs.rename_person(primary_pid, attr.value)
                logger.info(f"[extract] 用户自报姓名，更新 name: {user_name} → {attr.value}")

            if attr.field == "relationship":
                rel_target = target if target != "self" else None
                if rel_target and _is_valid_person_name(rel_target):
                    sec_node = await gs.get_person_by_name(owner_id, rel_target)
                    if not sec_node:
                        sec_node = await gs.upsert_person_node(
                            owner_id, rel_target, "secondary"
                        )
                    rtype = _map_relation_value(attr.value)
                    await gs.upsert_relationship(
                        owner_id, primary_pid, str(sec_node["person_id"]),
                        relation_type=rtype, sentiment_delta=0.2,
                    )
                continue

            if target == "self":
                await updater.update_from_attributes(
                    owner_id, primary_pid, [attr_dict]
                )
            elif _is_valid_person_name(target):
                sec_node = await gs.get_person_by_name(owner_id, target)
                if not sec_node:
                    sec_node = await gs.upsert_person_node(
                        owner_id, target, "secondary"
                    )
                await updater.update_from_attributes(
                    owner_id, str(sec_node["person_id"]), [attr_dict]
                )

        # Step 4.5: 关系声明正则兜底
        target_text_joined = " ".join(
            t["content"] for t in target_turns if t["role"] == "user"
        )
        await updater.update_from_declaration(
            owner_id, primary_pid, target_text_joined
        )

        # Step 5: Focus 追踪
        focus_text = " ".join(
            t["content"] for t in target_turns if t["role"] == "user"
        )
        related = [n for n in name_to_pid if n != user_name]
        await focus_tracker.update(primary_pid, focus_text, related)

        logger.info(
            f"[extract] session={session_id[:8]} done: "
            f"{len(result.events)} events stored"
        )

    except Exception as e:
        logger.error(f"[extract] session={session_id[:8]} failed: {e}")
        traceback.print_exc()


# ── 主接口 ────────────────────────────────────────────────────────────

@router.post("/memory/chat", response_model=MemoryChatResponse)
async def memory_chat(
    req: MemoryChatRequest,
    gs: GraphStore = Depends(get_gs),
    redis: RedisStore = Depends(get_redis),
    llm: LLMClient = Depends(get_llm),
    pg: PgStore = Depends(get_pg),
):
    """
    统一记忆接口。

    流程：
      1. 用 user_message 立即召回记忆
      2. 将本轮对话追加到 Redis session buffer
      3. 每累积5轮用户消息，后台异步提取记忆
      4. 立即返回召回结果
    """
    session_id = req.session_id or str(uuid4())
    import time as _time_p
    _T0 = _time_p.monotonic()
    _STAGES: dict[str,float] = {}
    def _mark(label):
        _STAGES[label] = round((_time_p.monotonic()-_T0)*1000, 1)

    # 确保 PersonNode 存在
    # P0-A 修复：用 get_primary_person(owner) 而不是 get_person_by_name(owner, user_name)
    # 否则 rename_person 把 name 改成真名后，客户端默认 user_name="用户"会 miss 并重复建
    try:
        node = await gs.get_primary_person(req.owner_id)
        if not node:
            # 同 owner 确实还没有 primary，才建一个
            node = await gs.get_person_by_name(req.owner_id, req.user_name)
            if not node:
                node = await gs.upsert_person_node(
                    req.owner_id, req.user_name, "primary"
                )
        person_id = str(node["person_id"])
        _mark("person_resolve")
    except Exception as e:
        logger.error(f"[pg_error] upsert_person failed: {e}")
        return MemoryChatResponse(
            session_id=session_id,
            person_id="",
            recall=RecallData(confidence="empty", source="degraded"),
        )

    # 实时身份检测 + 召回 + 缓存读取：三路并发，不增加延迟
    import re as _re
    import json as _json
    from retrieval.graph_recall import graph_recall

    _msg = req.user_message.strip()
    # 第一人称代词变种（撒娇/方言/网络/自谦）—— 统一替换为"我"再走原判断
    # 注意：末尾\b 避免误伤"我们"里的"我"；放在 identity 触发判断前
    _FIRST_PERSON_ALIASES = ("人家", "本宝宝", "本小姐", "本少爷", "本公主", "本大爷",
                             "老子", "小的", "小女子", "在下", "鄙人",
                             "咱", "俺", "偶")
    _msg_norm = _msg
    for _alias in _FIRST_PERSON_ALIASES:
        if _alias in _msg_norm:
            # 仅句首/紧邻空格后替换，避免"我们咱"类误伤；实际小孩/方言多句首
            if _msg_norm.startswith(_alias):
                _msg_norm = "我" + _msg_norm[len(_alias):]
                break
            # 否则不替换（保守）
    # name 信号（自报姓名场景）
    _has_name_signal = bool(_re.search(r'我叫|叫我|我的?名字|我是[^\s，。！？]{1,6}$|本人|在下|大家好[，,\s]*我[^\s，。！？的是]{1,4}$', _msg_norm))
    # 其他 identity 字段信号（age/school/grade/gender 等小孩自报场景）
    _has_attr_signal = bool(_re.search(
        r'我[^，。！？]{0,4}?(岁|周岁|多大)'           # 年龄: 我X岁/我今年X岁/我多大
        r'|我(在|读|上)[^，。！？]{1,12}?(小学|中学|学校|大学|幼儿园|学院)'  # 学校
        r'|我[^，。！？]{0,3}?(读|上|是)[一二三四五六七八九1234567890]{1,2}年级'  # 年级
        r'|我是.{0,2}(男孩|女孩|男生|女生|男|女)(子|的)?[^，。！？]{0,3}$'  # 性别
        r'|我[的是]?生日',                              # 生日
        _msg_norm,
    ))
    _is_question = bool(_re.search(r'[？?]|什么|啥|谁|吗|嘛|呢$|记得|知道|猜|来着|多大|多少|几岁|几年级|哪个|哪所|哪里|哪儿|是不是|对不对', _msg))
    _need_identity_extract = (_has_name_signal or _has_attr_signal) and not _is_question and len(_msg) <= 30

    async def _realtime_identity():
        """轻量 LLM identity 提取（仅在有 name signal 时触发）"""
        if not _need_identity_extract:
            return
        try:
            from llm.skills.attribute_extractor import extract_identity_attributes
            _node_cur = await gs.get_person_node(person_id)
            _cur_identity = _node_cur["identity"] if isinstance(
                _node_cur["identity"], dict
            ) else _json.loads(_node_cur["identity"] or "{}")
            _patch = await extract_identity_attributes(_msg_norm, _cur_identity, llm)
        except Exception as _e:
            # 只覆盖 LLM 提取阶段；DB 写入失败不应被误报为"提取失败"
            logger.warning(f"[realtime] LLM identity 提取失败: {_e}")
            return

        if not isinstance(_patch, dict):
            return

        # P0-B 修复：过滤占位词，防止 LLM 把"用户/小朋友/宝宝"等默认称呼写成 name
        # 选择白名单而非 prompt 侧改动是因为：prompt 改动影响面大、回归风险高，
        # 白名单是小范围 hot-fix 妥协。根治方案见 docs/incident_liuyang_persona_bug.md
        # (正向 evidence_span 验证 + DB partial unique index)
        # TODO(hotfix-followup): 把 _PLACEHOLDER_NAMES 抽到 config/identity_blocklist.yaml
        # TODO(i18n): 当前仅覆盖简中+英;繁体/日文/拼音需配合 NFKC + 多语种词表
        # 黑名单原则（修订 2026-04）：
        # 只收录"绝对不可能作为真实人名"的纯指代词/泛称/职业/虚词。
        # 不再收录"LLM 常见示例名"——因为这些（小明/小红/张三/李四/小芳）
        # 在儿童语音机器人产品场景里是真实小孩的名字，黑名单误伤代价远大于
        # LLM 偶尔编造占位的代价。LLM 编造由上游 _need_identity_extract
        # 信号 + evidence_span 验证防御，不在此层兜底。
        _PLACEHOLDER_NAMES_NORMALIZED = {
            # 纯指代/泛称（不可能是名）
            "用户", "小朋友", "小朋友们", "朋友", "小伙伴",
            "宝宝", "宝贝", "孩子", "小孩", "同学", "同学们",
            "你", "我", "他", "她", "主人", "亲",
            # 职业/称谓（不是名）
            "先生", "女士", "老师", "客户", "来访者",
            # 自称/谦辞类指代（古今汉语）
            "本人", "鄙人", "在下", "晚辈", "前辈", "敝人", "本座", "老朽",
            "閣下", "阁下", "貴客", "贵客", "区区", "區區",
            # 真正的"无名"占位
            "路人", "匿名", "某某", "无名氏",
            # 英文纯指代/职业（预归一化为 lower）
            "user", "usr", "anonymous", "someone",
            "guest", "customer", "kid", "child", "friend", "buddy",
            # 英文 placeholder 全名模式（保留：JohnDoe/JaneDoe 是公认占位约定）
            "johndoe", "janedoe", "johnsmith", "janesmith", "marysue",
            "foobar",
        }

        # 泛化占位词判据（通用特征，不依赖词表）：
        #   R1 虚词结尾："亲爱的/好看的/聪明啊"
        #   R2 含序数/匿名词素："路人甲/无名氏/某某"
        #   R3 指代/自称词集："本人/自己/对方/那人/此人"
        #   R4 "小/老/大/阿" + 形容词性字："小可爱/小漂亮/老大"
        # 对 CAT-C 真名（刘杨/欧阳娜娜/王思聪/李明/小芳/周杰伦/张伟）零误伤，
        # 对 CAT-B 开放集合占位词（亲爱的/小可爱/本人/无名氏/路人甲）命中。
        _TRAILING_PARTICLES = ("的", "吗", "了", "呢", "啊", "哦", "吧", "嘛", "呀", "咯")
        _INDEX_MORPHEMES = set("甲乙丙丁戊己庚辛氏某")  # 排行/匿名字
        _SELFREF_TOKENS = {
            "本人", "自己", "对方", "自我", "某人", "那人", "此人", "他人",
            "自家", "那位", "这位", "某位",
        }
        _ADJ_AFTER_XIAO = set("可爱漂亮聪明美乖萌帅靓丑笨胖瘦高矮傻蠢酷")

        def _looks_like_generic_placeholder(name: str) -> bool:
            """白名单之外的通用特征判据。命中任一规则即视为占位词。"""
            if not name or len(name) < 2:
                return False
            # R1 虚词结尾
            if name.endswith(_TRAILING_PARTICLES):
                return True
            # R2 含匿名/序数字
            if any(ch in _INDEX_MORPHEMES for ch in name):
                # 但 2 字名含"己"需宽容（极少见姓），只拦 >=3 字或明显模式
                if len(name) >= 3 or name[-1] in _INDEX_MORPHEMES:
                    return True
            # R3 指代/自称词
            if name in _SELFREF_TOKENS:
                return True
            # R4 "小/老/大/阿" + 形容词性字（仅 3 字复合命中，避免误伤 2 字常见名"小美/小萌/小乖"）
            if len(name) == 3 and name[0] in "小老大阿":
                tail = name[1:]
                if any(ch in _ADJ_AFTER_XIAO for ch in tail):
                    return True
            # R5 英文占位全名模式 "X Doe/Smith/Roe"（P2: F3/F4 泛化）
            # 只对含空格或含拉丁字母的 name 触发，避免误伤中文
            _lower = name.lower().strip()
            if " " in _lower and any(c.isascii() and c.isalpha() for c in _lower):
                _tail = _lower.rsplit(" ", 1)[-1]
                if _tail in {"doe", "smith", "roe", "bloggs", "public"}:
                    return True
            return False

        def _is_placeholder_name(s) -> bool:
            """BLOCK-2 修复：strip + lower + 去全半角空格 + NFKC 归一再查表。
            防绕过：'UsEr' / '用 户' / '用　户' / '   ' 纯空白 全部拦下。
            泛化修复：白名单未命中时进一步走 _looks_like_generic_placeholder。"""
            if not isinstance(s, str):
                return False
            import unicodedata as _ud
            norm = _ud.normalize("NFKC", s).lower()
            norm = norm.replace(" ", "").replace("\u3000", "").replace("\t", "")
            if not norm:  # 纯空白视作占位
                return True
            if norm in _PLACEHOLDER_NAMES_NORMALIZED:
                return True
            # 通用判据（对原字符串而非 lower，避免中文无意义）
            return _looks_like_generic_placeholder(s.strip())

        _changed = False
        for _k, _v in _patch.items():
            if _v in (None, "", 0):
                continue
            if _k == "name":
                if _is_placeholder_name(_v):
                    logger.info(f"[realtime] 拒绝占位词作为 name: {_v!r}")
                    continue
            if _cur_identity.get(_k) != _v:
                _cur_identity[_k] = _v
                _changed = True

        if _changed:
            try:
                await gs.update_person_field(person_id, "identity", _cur_identity)
                logger.info(f"[realtime] LLM identity 写入: {_cur_identity}")
                # 关键：identity.name 是 jsonb 内的"声明姓名"，但 person_nodes.name
                # 列是检索/展示主键，二者必须同步。否则 graph_recall 的
                # build_profile_summary 仍读到旧 name="用户"，对话层永远不知道真名。
                _new_name = _cur_identity.get("name")
                if _new_name and isinstance(_new_name, str) and _new_name.strip():
                    try:
                        await gs.rename_person(person_id, _new_name.strip())
                        logger.info(
                            f"[realtime] 同步 person_nodes.name: → {_new_name.strip()!r}"
                        )
                    except Exception as _re:
                        logger.warning(f"[realtime] rename_person 失败: {_re}")
            except Exception as _e:
                # BLOCK-3 修复：写库失败单独报，避免与 LLM 提取失败混淆
                logger.warning(f"[realtime] identity 写库失败: {_e}")

    # 三路并发 → 两路 gather + identity fire-and-forget
    # 性能优化：realtime_identity 涉及一次 LLM 调用 (~600ms p50)，
    # 阻塞召回返回收益极小（identity 不影响本轮回复，下轮就生效）
    merged_events: list[dict] = []
    if _need_identity_extract:
        asyncio.create_task(_realtime_identity())
    try:
        recall_result, old_cache = await asyncio.gather(
            graph_recall(gs, req.owner_id, person_id, req.user_message,
                         redis=redis, session_id=session_id),
            redis.get_decay_cache(session_id),
        )
    except Exception as e:
        logger.error(f"[recall_error] graph_recall/cache failed: {e}")
        recall_result = None
        old_cache = []
    _mark("recall_parallel")

    # 合并新召回 + 旧缓存衰减
    if recall_result:
        merged_events = _merge_recall_events(recall_result.events, old_cache)
        # 异步写回缓存，不阻塞返回
        asyncio.create_task(redis.set_decay_cache(session_id, merged_events))
        new_count = len(recall_result.events)
        cached_count = len(old_cache)
        logger.info(
            f"[recall] session={session_id[:8]} "
            f"merged={len(merged_events)} (new={new_count}, cached={cached_count})"
        )

    # 2. 追加到 Redis session buffer
    user_count = 0
    try:
        user_count = await redis.append_session(
            session_id, req.user_message, req.assistant_message
        )
    except Exception as e:
        logger.warning(f"[redis_error] append_session failed: {e}")
        # Redis 挂了，跳过累积，不阻塞

    # 3. 每5轮触发后台提取（P0-1 Outbox: 先同步写 task 表 → fire-and-forget）
    if user_count > 0 and user_count % 5 == 0:
        try:
            buffer = await redis.get_session_buffer(session_id)
            task_id = str(uuid.uuid4())
            payload = {
                "owner_id": req.owner_id,
                "person_id": person_id,
                "session_id": session_id,
                "user_name": req.user_name,
                "buffer": buffer,
            }
            try:
                await pg.create_graph_task(
                    task_id=task_id,
                    owner_id=req.owner_id,
                    person_id=person_id,
                    session_id=session_id,
                    payload=payload,
                )
            except Exception as e:
                logger.error(f"[outbox] create_graph_task failed: {e}")
                task_id = None  # 退化为旧行为
            asyncio.create_task(
                _background_extract(
                    llm, gs, req.owner_id, person_id,
                    session_id, req.user_name, buffer,
                    task_id=task_id, pg=pg,
                )
            )
            logger.info(
                f"[trigger] session={session_id[:8]} "
                f"user_count={user_count} task_id={task_id} extracting..."
            )
        except Exception as e:
            logger.warning(f"[redis_error] get_session_buffer failed: {e}")

    # 4. 立即返回
    if recall_result:
        recall_data = RecallData(
            profile_summary=recall_result.profile_summary,
            events=[
                {
                    "event_id": str(e.get("event_id", "")),
                    "event_type": e.get("event_type", ""),
                    "summary": e.get("summary", ""),
                    "event_time": str(e.get("event_time", "")),
                    "participants": e.get("participant_names", []),
                    "weight": round(e.get("weight", 1.0), 2),
                }
                for e in merged_events[:5]
            ],
            session_context=recall_result.session_context,
            intent=recall_result.intent,
            confidence=recall_result.confidence,
            source=recall_result.source,
            latency_ms=recall_result.latency_ms,
        )
    else:
        recall_data = RecallData(confidence="empty", source="degraded")

    _mark("pre_return")
    logger.info(f"[profile] stages={_STAGES}")
    return MemoryChatResponse(
        session_id=session_id,
        person_id=person_id,
        recall=recall_data,
    )
