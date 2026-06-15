"""关系可视化面板：以"用户"为入口，看每段关系的时间线动态与记忆图谱。

纯标准库、零依赖。前端（dashboard.html）不引任何外部库——力导向图谱与
信任轨迹都是手写的，和引擎本体一样的"零依赖"气质。

  python demo/dashboard.py --demo            # 先生成多用户演示世界再启动
  python demo/dashboard.py --state-dir runtime/relationshape --port 8088

端点：
  GET  /                       面板（单页应用：画廊 → 用户详情）
  GET  /api/users              所有用户的概览卡片 + 全局聚合（第一性：看得见所有人）
  GET  /api/state/<user>       某用户的关系状态全景（账本/养成/文化/适应/痕迹）
  GET  /api/timeline/<user>    某用户的关系时间线：动态事件 + 记忆在时间上的分布
  GET  /api/graph/<user>       某用户的记忆库图谱（人物/喜好/雷区/最在乎/共同梗/记忆）
  POST /api/preview            {"user_id","text"} → 本轮指令预览（干跑，不落盘）

安全原则进 UI：封存区（危机内容）只显示条数，内容永不出现在任何接口里。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from relationshape import CompanionEngine, EngineConfig  # noqa: E402
from relationshape.affect import mood_word  # noqa: E402
from relationshape.memory import _ROLE_WORDS  # noqa: E402
from relationshape.relationship import stage_label  # noqa: E402
from relationshape.types import STAGE_ORDER, Stage  # noqa: E402

HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")

STAGE_ZH = {
    "stranger": "陌生", "acquaintance": "相识", "familiar": "熟悉",
    "companion": "同伴", "confidant": "知己",
}


def _ep_strength(ep, cfg, now: datetime) -> tuple[float, int]:
    """情景记忆当前强度（遗忘曲线衰减后）与距今天数。"""
    days = max(0.0, (now - datetime.fromisoformat(ep.created_at)).total_seconds() / 86400.0)
    half = cfg.memory_half_life_days * (1 + 0.8 * ep.recall_count)
    return ep.salience * (0.5 ** (days / half)), int(days)


# ---------------------------------------------------------------------------
# 养成进度 / 记忆 / 状态全景
# ---------------------------------------------------------------------------

def _gate_progress(st, cfg) -> dict:
    idx = STAGE_ORDER.index(st.core.stage)
    if idx >= len(STAGE_ORDER) - 1:
        return {"next_stage": None, "items": []}
    nxt = STAGE_ORDER[idx + 1]
    g = cfg.stage_gates[nxt]
    days = (datetime.now() - datetime.fromisoformat(st.core.first_met)).days if st.core.first_met else 0
    led = st.ledger
    items = [
        ("认识天数", days, g.min_days),
        ("见面次数", st.core.sessions, g.min_sessions),
        ("实质对话", led.substantive_turns, g.min_substantive),
        ("信任", round(led.trust, 1), g.min_trust),
        ("自我表露", led.disclosures, g.min_disclosures),
        ("深度表露", led.deep_disclosures, g.min_deep_disclosures),
        ("共同文化", st.adaptation.culture_size(), g.min_culture),
    ]
    return {
        "next_stage": stage_label(nxt),
        "items": [
            {"name": n, "cur": c, "need": r, "pct": min(100, round(100 * c / r)) if r else 100}
            for n, c, r in items if r > 0
        ],
        "rupture_block": led.ruptures_open > 0,
    }


def _memories(st, cfg, now: datetime) -> list[dict]:
    out = []
    for ep in st.memory.episodes:
        if ep.sensitive:
            continue
        strength, days = _ep_strength(ep, cfg, now)
        out.append({"text": ep.text, "days": days, "strength": round(strength, 3),
                    "recalls": ep.recall_count, "vulnerable": ep.vulnerability >= 3})
    out.sort(key=lambda x: -x["strength"])
    return out[:12]


def build_state(eng: CompanionEngine, user_id: str) -> dict:
    cfg = eng.config
    st = eng._state(user_id)
    now = datetime.now()
    days = (now - datetime.fromisoformat(st.core.first_met)).days if st.core.first_met else 0
    sealed = sum(1 for e in st.memory.episodes if e.sensitive)
    return {
        "user_id": user_id,
        "character": eng.identity.name,
        "stage": st.core.stage.value,
        "stage_label": stage_label(st.core.stage),
        "stage_index": STAGE_ORDER.index(st.core.stage),
        "days_known": days,
        "sessions": st.core.sessions,
        "milestones": st.core.milestones_done,
        "gate": _gate_progress(st, cfg),
        "mood": {"p": st.mood.p, "a": st.mood.a, "d": st.mood.d, "word": mood_word(st.mood)},
        "ledger": {
            "trust": round(st.ledger.trust, 1), "closeness": round(st.ledger.closeness, 1),
            "substantive": st.ledger.substantive_turns, "disclosures": st.ledger.disclosures,
            "deep": st.ledger.deep_disclosures, "bids": st.ledger.bids_toward,
            "ruptures_open": st.ledger.ruptures_open, "repaired": st.ledger.ruptures_repaired,
            "promises_kept": st.ledger.promises_kept, "promises_broken": st.ledger.promises_broken,
        },
        "promises": [{"text": p.text, "status": p.status} for p in st.memory.promises][-10:],
        "culture": {
            "address": st.adaptation.address_form,
            "jokes": [j.label for j in st.adaptation.inside_jokes],
            "lessons": st.adaptation.lessons,
            "claims": st.adaptation.self_claims,
        },
        "memory": {
            "episodes": _memories(st, cfg, now),
            "preferences": st.memory.preferences,
            "aversions": st.memory.aversions,
            "people": [{"name": k, **v} for k, v in st.memory.people.items()],
            "sealed_count": sealed,
            "user_name": st.memory.user_name,
        },
        "adaptation": {
            "formality": round(st.adaptation.formality, 2),
            "energy": round(st.adaptation.energy, 2),
            "humor": {k: round(v, 2) for k, v in st.adaptation.humor_receptivity.items()},
        },
        "last_hook": st.last_hook,
        "traces": list(reversed(st.traces)),
    }


# ---------------------------------------------------------------------------
# 用户画廊：所有人的概览（第一性原理：可视化能显示所有用户的信息）
# ---------------------------------------------------------------------------

def build_user_card(eng: CompanionEngine, user_id: str) -> dict:
    cfg = eng.config
    st = eng._state(user_id)
    now = datetime.now()
    days = (now - datetime.fromisoformat(st.core.first_met)).days if st.core.first_met else 0
    last_seen_days = (
        max(0, (now - datetime.fromisoformat(st.core.last_seen)).days) if st.core.last_seen else None
    )
    sealed = sum(1 for e in st.memory.episodes if e.sensitive)
    live_eps = sum(1 for e in st.memory.episodes if not e.sensitive)
    # 信任轨迹采样（给卡片画 sparkline）：时间线事件里的信任快照
    spark = [e.get("trust", 0) for e in st.timeline if "trust" in e][-24:]
    return {
        "user_id": user_id,
        "name": st.memory.user_name or user_id,
        "character": eng.identity.name,
        "stage": st.core.stage.value,
        "stage_index": STAGE_ORDER.index(st.core.stage),
        "stage_zh": STAGE_ZH.get(st.core.stage.value, st.core.stage.value),
        "trust": round(st.ledger.trust, 1),
        "closeness": round(st.ledger.closeness, 1),
        "days_known": days,
        "sessions": st.core.sessions,
        "mood_word": mood_word(st.mood),
        "last_seen": st.core.last_seen,
        "last_seen_days": last_seen_days,
        "episodes": live_eps,
        "people": len([k for k in st.memory.people if k not in _ROLE_WORDS]),
        "preferences": len(st.memory.preferences),
        "promises_open": sum(1 for p in st.memory.promises if p.status == "open"),
        "inside_jokes": len(st.adaptation.inside_jokes),
        "sealed": sealed,
        "milestones": st.core.milestones_done,
        "trust_spark": spark,
    }


def build_users(eng: CompanionEngine, user_ids: list[str]) -> dict:
    cards = [build_user_card(eng, u) for u in user_ids]
    cards.sort(key=lambda c: (-c["stage_index"], -c["trust"]))
    dist = {s.value: 0 for s in STAGE_ORDER}
    for c in cards:
        dist[c["stage"]] = dist.get(c["stage"], 0) + 1
    total_mem = sum(c["episodes"] for c in cards)
    avg_trust = round(sum(c["trust"] for c in cards) / len(cards), 1) if cards else 0
    return {
        "users": cards,
        "character": eng.identity.name,
        "aggregate": {
            "total_users": len(cards),
            "total_memories": total_mem,
            "total_people": sum(c["people"] for c in cards),
            "total_jokes": sum(c["inside_jokes"] for c in cards),
            "total_sealed": sum(c["sealed"] for c in cards),
            "avg_trust": avg_trust,
            "stage_dist": dist,
        },
    }


# ---------------------------------------------------------------------------
# 时间线：关系动态 + 记忆在时间上的分布
# ---------------------------------------------------------------------------

def build_timeline(eng: CompanionEngine, user_id: str) -> dict:
    cfg = eng.config
    st = eng._state(user_id)
    now = datetime.now()
    days = (now - datetime.fromisoformat(st.core.first_met)).days if st.core.first_met else 0

    # 轨迹采样点：时间线事件里携带的信任/亲密/阶段快照（关系真实历史的曲线）
    series = [
        {"t": e["t"], "trust": e.get("trust", 0), "closeness": e.get("closeness", 0),
         "stage": e.get("stage", ""), "session": e.get("session", 0)}
        for e in st.timeline if "trust" in e
    ]
    # 动态事件（不含记忆点；记忆单独成层）
    events = [
        {"t": e["t"], "kind": e["kind"], "label": e.get("label", ""),
         "detail": e.get("detail", ""), "trust": e.get("trust"),
         "closeness": e.get("closeness"), "stage": e.get("stage")}
        for e in st.timeline
    ]
    # 记忆点：每条非敏感情景记忆形成的时刻 + 当前（衰减后）强度
    memories = []
    for ep in st.memory.episodes:
        if ep.sensitive:
            continue
        strength, ago = _ep_strength(ep, cfg, now)
        memories.append({
            "t": ep.created_at[:16], "text": ep.text,
            "salience": round(ep.salience, 3), "strength": round(strength, 3),
            "recalls": ep.recall_count, "vulnerable": ep.vulnerability >= 3,
            "days_ago": ago, "valence": round(ep.valence, 2),
            "actors": ep.actors, "place": ep.place,
            "last_recalled": ep.last_recalled[:16] if ep.last_recalled else "",
        })
    memories.sort(key=lambda m: m["t"])
    sealed = sum(1 for e in st.memory.episodes if e.sensitive)

    return {
        "user_id": user_id,
        "name": st.memory.user_name or user_id,
        "character": eng.identity.name,
        "stage": st.core.stage.value,
        "stage_zh": STAGE_ZH.get(st.core.stage.value, st.core.stage.value),
        "first_met": st.core.first_met,
        "last_seen": st.core.last_seen,
        "days_known": days,
        "sessions": st.core.sessions,
        "series": series,
        "events": events,
        "memories": memories,
        "sealed_count": sealed,
    }


# ---------------------------------------------------------------------------
# 记忆图谱：把记忆库做成知识图谱（用户为中心，向人物/喜好/雷区/记忆辐射）
# ---------------------------------------------------------------------------

def build_graph(eng: CompanionEngine, user_id: str) -> dict:
    """记忆库知识图谱：以人为节点，事件把人连起来——什么人和谁在什么地方发生了什么事。

    拓扑：人物（用户 + 身边的人）←参与→ 事件（情景记忆）→在→ 地点；
    两个人通过共同事件相连（"和大壮在操场看星星" → 用户·大壮 同挂在那件事上）。
    喜好/雷区/最在乎/专属梗作为用户的语义档案，轻量挂在用户旁。
    """
    cfg = eng.config
    st = eng._state(user_id)
    now = datetime.now()
    m = st.memory
    name = m.user_name or user_id

    nodes: list[dict] = [{"id": "__user__", "type": "user", "label": name, "weight": 6}]
    edges: list[dict] = []
    seen: set[str] = {"__user__"}

    def add(nid: str, ntype: str, label: str, weight: float = 1.0, **extra) -> str:
        if nid not in seen:
            nodes.append({"id": nid, "type": ntype, "label": label, "weight": weight, **extra})
            seen.add(nid)
        return nid

    # 身边的人：以人为节点（过滤泛称角色词；带属性与关系）
    person_ids: dict[str, str] = {}
    for pname, info in m.people.items():
        if pname in _ROLE_WORDS:
            continue
        rel = str(info.get("relation") or "熟人")
        attr = str(info.get("attr") or "")
        nid = add(f"person:{pname}", "person", pname, 2.4 + 0.3 * info.get("mentions", 0),
                  sub=(f"{attr}的{rel}" if attr else rel))
        person_ids[pname] = nid

    # 事件（情景记忆）：把人连起来的边。优先取"有人物/有地点"的社交事件，再按显著度补足。
    ep_scored = []
    for ep in m.episodes:
        if ep.sensitive:
            continue
        strength, ago = _ep_strength(ep, cfg, now)
        social = bool(ep.actors) or bool(ep.place)
        ep_scored.append((social, strength, ago, ep))
    ep_scored.sort(key=lambda x: (-int(x[0]), -x[1]))     # 社交事件优先，再按当前鲜明度
    place_ids: dict[str, str] = {}
    shown_events = 0
    for social, strength, ago, ep in ep_scored[:12]:
        eid = add(f"ev:{ep.mid}", "event", ep.text, 1.0 + 2.2 * strength,
                  strength=round(strength, 3), days_ago=ago, place=ep.place,
                  actors=ep.actors, valence=round(ep.valence, 2))
        shown_events += 1
        edges.append({"source": "__user__", "target": eid, "kind": "attend"})   # 用户是每件事的主角
        for actor in ep.actors:                                                 # 事件涉及的其他人
            if actor in person_ids:
                edges.append({"source": person_ids[actor], "target": eid, "kind": "attend"})
        if ep.place:                                                            # 在什么地方
            pid = place_ids.get(ep.place) or add(f"place:{ep.place}", "place", ep.place, 1.8)
            place_ids[ep.place] = pid
            edges.append({"source": eid, "target": pid, "kind": "at"})

    # 反查：item → 品类（喜欢的水果是苹果 → 苹果.cat=水果）
    item_cat = {v: k for k, v in m.cat_prefs.items()}
    item_cat.update({v: k for k, v in m.cat_aversions.items()})

    # 用户的语义档案：最在乎 / 喜好 / 雷区 / 专属梗——轻量挂在用户旁
    for c in m.cared:
        nid = add(f"care:{c}", "cared", c, 2.6)
        edges.append({"source": "__user__", "target": nid, "rel": "最在乎", "kind": "cared"})
    for p in m.preferences:
        nid = add(f"like:{p}", "like", p, 1.6, sub=item_cat.get(p, ""))
        edges.append({"source": "__user__", "target": nid, "rel": "喜欢", "kind": "like"})
    for a in m.aversions:
        nid = add(f"dislike:{a}", "dislike", a, 1.6, sub=item_cat.get(a, ""))
        edges.append({"source": "__user__", "target": nid, "rel": "怕/讨厌", "kind": "dislike"})
    for jk in st.adaptation.inside_jokes:
        nid = add(f"joke:{jk.jid}", "joke", jk.label, 1.6)
        edges.append({"source": "__user__", "target": nid, "rel": "专属梗", "kind": "joke"})

    sealed = sum(1 for e in m.episodes if e.sensitive)
    return {
        "user_id": user_id, "name": name, "stage_zh": STAGE_ZH.get(st.core.stage.value, ""),
        "nodes": nodes, "edges": edges, "sealed_count": sealed,
        "counts": {
            "people": len(person_ids), "places": len(place_ids), "events": shown_events,
            "likes": len(m.preferences), "dislikes": len(m.aversions),
            "cared": len(m.cared), "jokes": len(st.adaptation.inside_jokes),
            "memories": len(ep_scored),
        },
    }


# ---------------------------------------------------------------------------
# 指令试驾（干跑）
# ---------------------------------------------------------------------------

def preview_directive(state_dir: str, user_id: str, text: str) -> dict:
    """一次性引擎做干跑：读盘→prepare→丢弃，磁盘与真实会话不受影响。"""
    eng = CompanionEngine(config=EngineConfig(state_dir=state_dir))
    d = eng.prepare_turn(user_id, text)
    return {
        "input_type": d.frame.input_type.value,
        "emotion": d.character_emotion.label,
        "acts": [a.value for a in d.acts],
        "humor": bool(d.humor),
        "reward": d.reward.rtype.value if d.reward else None,
        "safety": bool(d.safety),
        "recalled": [{"text": m.text, "kind": m.kind, "days": m.days_ago} for m in d.memories],
        "profile": bool(d.profile_summary),
        "prompt_context": d.to_prompt_context(),
    }


def _decode_uid(raw: str) -> str:
    """URL 路径里的中文用户名：兼容 %XX 编码（浏览器）与原始 UTF-8（curl）。"""
    from urllib.parse import unquote

    uid = unquote(raw)
    try:
        return uid.encode("latin-1").decode("utf-8")   # http.server 按 latin-1 解码的原始字节
    except (UnicodeEncodeError, UnicodeDecodeError):
        return uid


def make_handler(state_dir: str):
    def _users(state_dir: str) -> list[str]:
        return sorted(p.stem for p in Path(state_dir).glob("*.json"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _route(self, prefix: str, builder) -> None:
            uid = _decode_uid(self.path.split(prefix, 1)[1])
            try:
                eng = CompanionEngine(config=EngineConfig(state_dir=state_dir))
                self._json(builder(eng, uid))
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, HTML.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/users":
                try:
                    eng = CompanionEngine(config=EngineConfig(state_dir=state_dir))
                    self._json(build_users(eng, _users(state_dir)))
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, 500)
            elif self.path.startswith("/api/state/"):
                self._route("/api/state/", build_state)
            elif self.path.startswith("/api/timeline/"):
                self._route("/api/timeline/", build_timeline)
            elif self.path.startswith("/api/graph/"):
                self._route("/api/graph/", build_graph)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/api/preview":
                return self._json({"error": "not found"}, 404)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            try:
                self._json(preview_directive(state_dir, body["user_id"], body["text"]))
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--state-dir", default="runtime/relationshape")
    ap.add_argument("--demo", action="store_true", help="先生成多用户演示世界（runtime/demo_state）")
    args = ap.parse_args()

    if args.demo:
        sys.path.insert(0, str(ROOT / "demo"))
        import simulate
        print("生成多用户演示世界…")
        simulate.seed_demo_world()
        args.state_dir = "runtime/demo_state"

    Path(args.state_dir).mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.state_dir))
    print(f"关系可视化面板: http://127.0.0.1:{args.port}  （状态目录: {args.state_dir}）")
    server.serve_forever()


if __name__ == "__main__":
    main()
