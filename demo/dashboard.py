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
    cfg = eng.config
    st = eng._state(user_id)
    now = datetime.now()
    m = st.memory
    name = m.user_name or user_id

    nodes: list[dict] = [{"id": "__user__", "type": "user", "label": name, "weight": 6}]
    edges: list[dict] = []
    seen: set[str] = {"__user__"}

    def add(nid: str, ntype: str, label: str, weight: float = 1.0, sub: str = "") -> str:
        if nid not in seen:
            node = {"id": nid, "type": ntype, "label": label, "weight": weight}
            if sub:
                node["sub"] = sub
            nodes.append(node)
            seen.add(nid)
        return nid

    # 反查：item → 品类（喜欢的水果是苹果 → 苹果.cat=水果）
    item_cat = {v: k for k, v in m.cat_prefs.items()}
    item_cat.update({v: k for k, v in m.cat_aversions.items()})

    # 最在乎（核心，最靠近用户、最重）
    for c in m.cared:
        nid = add(f"care:{c}", "cared", c, 3.2)
        edges.append({"source": "__user__", "target": nid, "rel": "最在乎", "kind": "cared"})

    # 喜好
    for p in m.preferences:
        nid = add(f"like:{p}", "like", p, 2.0, sub=item_cat.get(p, ""))
        edges.append({"source": "__user__", "target": nid, "rel": "喜欢", "kind": "like"})

    # 雷区
    for a in m.aversions:
        nid = add(f"dislike:{a}", "dislike", a, 2.0, sub=item_cat.get(a, ""))
        edges.append({"source": "__user__", "target": nid, "rel": "怕/讨厌", "kind": "dislike"})

    # 身边的人（过滤泛称角色词；带属性与关系）
    person_ids: dict[str, str] = {}
    for pname, info in m.people.items():
        if pname in _ROLE_WORDS:
            continue
        rel = str(info.get("relation") or "熟人")
        attr = str(info.get("attr") or "")
        nid = add(f"person:{pname}", "person", pname, 2.4 + 0.3 * info.get("mentions", 0),
                  sub=(f"{attr}的{rel}" if attr else rel))
        edges.append({"source": "__user__", "target": nid, "rel": rel, "kind": "person"})
        person_ids[pname] = nid

    # 共同梗（关系文化）
    for j in st.adaptation.inside_jokes:
        nid = add(f"joke:{j.jid}", "joke", j.label, 2.0)
        edges.append({"source": "__user__", "target": nid, "rel": "专属梗", "kind": "joke"})

    # 记忆（最显著的若干条情景记忆；与它提到的人物/喜好交叉连线，形成知识结构）
    ep_scored = []
    for ep in m.episodes:
        if ep.sensitive:
            continue
        strength, ago = _ep_strength(ep, cfg, now)
        ep_scored.append((strength, ago, ep))
    ep_scored.sort(key=lambda x: -x[0])
    for strength, ago, ep in ep_scored[:12]:
        nid = add(f"mem:{ep.mid}", "memory", ep.text, 0.8 + 2.4 * strength)
        nodes[-1]["strength"] = round(strength, 3)
        nodes[-1]["days_ago"] = ago
        edges.append({"source": "__user__", "target": nid, "kind": "memory"})
        # 交叉连线：这条记忆提到了哪位身边的人 / 哪个喜好
        for pname, pid in person_ids.items():
            if pname in ep.text:
                edges.append({"source": nid, "target": pid, "kind": "ref"})
        for p in m.preferences:
            if p in ep.text:
                edges.append({"source": nid, "target": f"like:{p}", "kind": "ref"})

    sealed = sum(1 for e in m.episodes if e.sensitive)
    return {
        "user_id": user_id, "name": name, "stage_zh": STAGE_ZH.get(st.core.stage.value, ""),
        "nodes": nodes, "edges": edges, "sealed_count": sealed,
        "counts": {
            "people": len(person_ids), "likes": len(m.preferences),
            "dislikes": len(m.aversions), "cared": len(m.cared),
            "jokes": len(st.adaptation.inside_jokes), "memories": len(ep_scored),
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
