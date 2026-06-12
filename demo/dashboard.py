"""养成面板：把关系状态变成看得见的网页（纯标准库，零依赖）。

  python demo/dashboard.py --demo            # 先生成30天演示数据再启动
  python demo/dashboard.py --state-dir runtime/relationshape --port 8088

端点：
  GET  /                     面板页面
  GET  /api/users            状态目录下的用户列表
  GET  /api/state/<user>     可读的关系状态全景（含遗忘衰减后的记忆强度、晋升门槛进度）
  POST /api/preview          {"user_id","text"} → 本轮指令预览（一次性引擎，不落盘不影响真实状态）

安全原则进 UI：封存区只显示条数，内容永不出现在任何接口里。
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
from relationshape.relationship import stage_label  # noqa: E402
from relationshape.types import STAGE_ORDER, Stage  # noqa: E402

HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


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
        days = max(0.0, (now - datetime.fromisoformat(ep.created_at)).total_seconds() / 86400)
        half = cfg.memory_half_life_days * (1 + 0.8 * ep.recall_count)
        strength = ep.salience * (0.5 ** (days / half))
        out.append({"text": ep.text, "days": int(days), "strength": round(strength, 3),
                    "recalls": ep.recall_count, "vulnerable": ep.vulnerability >= 3})
    out.sort(key=lambda x: -x["strength"])
    return out[:12]


def build_state(state_dir: str, user_id: str) -> dict:
    cfg = EngineConfig(state_dir=state_dir)
    eng = CompanionEngine(config=cfg)
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
    }


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

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, HTML.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/users":
                users = sorted(p.stem for p in Path(state_dir).glob("*.json"))
                self._json(users)
            elif self.path.startswith("/api/state/"):
                uid = _decode_uid(self.path.split("/api/state/", 1)[1])
                try:
                    self._json(build_state(state_dir, uid))
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e)}, 500)
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
    ap.add_argument("--demo", action="store_true", help="先生成30天演示数据（runtime/demo_state）")
    args = ap.parse_args()

    if args.demo:
        sys.path.insert(0, str(ROOT / "demo"))
        import simulate
        print("生成 30 天演示数据…")
        simulate.fast_forward()
        args.state_dir = "runtime/demo_state"

    Path(args.state_dir).mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.state_dir))
    print(f"养成面板: http://127.0.0.1:{args.port}  （状态目录: {args.state_dir}）")
    server.serve_forever()


if __name__ == "__main__":
    main()
