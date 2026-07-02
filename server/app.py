"""HTTP 传输层：把 growth.GrowthEngine 翻译成 JSON API，并托管静态客户端。

刻意"薄"——这里没有任何业务逻辑（判分/养成/对战全在 growth/ 里）。它只做三件事：
1. 路由 + 收发 JSON；2. 持有一个"模拟时钟"（让客户端能快进天数，演示坚持/趋势/活力衰减）；
3. 把 client/ 下的静态文件原样发出去。

纯标准库、零依赖，和 demo/dashboard.py 同一种气质。

  python server/app.py --seed --port 8099
  # 然后浏览器打开 http://127.0.0.1:8099
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from growth import GrowthEngine  # noqa: E402

CLIENT = ROOT / "client"
DEVICE = ROOT / "device"
BASE_NOW = datetime(2026, 6, 1, 16, 0)   # 模拟时钟起点（演示用，可 reset 回到这里）

_CTYPE = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
_STATIC = {
    "/": CLIENT / "index.html", "/index.html": CLIENT / "index.html",
    "/app.js": CLIENT / "app.js", "/styles.css": CLIENT / "styles.css",
    "/device": DEVICE / "index.html", "/device/": DEVICE / "index.html",
    "/device/index.html": DEVICE / "index.html",
    "/device/app.js": DEVICE / "app.js", "/device/styles.css": DEVICE / "styles.css",
}

# 演示答案：客观题用正确答案，表达/创造给一句像样的童言（让首屏报告的"亮点"好看）
_DEMO_EFFORT = {
    "expression": "今天体育课我们玩老鹰捉小鸡，我跑得最快，特别开心！",
    "creation": "我想带恐龙飞到云朵上，搭一座棉花糖做的城堡。",
}


def _demo_answer(engine, cid: str) -> str:
    c = engine.bank.get(cid)
    if c and c.answer:
        return c.answer
    if c and c.kind.value in _DEMO_EFFORT:
        return _DEMO_EFFORT[c.kind.value]
    return "我想了很多，觉得这件事很有意思。"


def seed_demo(engine, now) -> bool:
    """无孩子时注入演示世界（唯一实现，--seed 与 /api/seed 共用）。

    小禾连续喂了 6 天（今天喂满即破壳），阿哲只玩过昨天——一老一新，正好演示对比。
    """
    if engine.store.list_ids():
        return False
    a = engine.create_child("小禾", age=8, grade=2, now=now)
    b = engine.create_child("阿哲", age=9, grade=3, now=now)

    def play(child_id, day_now):
        for ch in engine.home(child_id, now=day_now)["today"]["challenges"]:
            engine.answer(child_id, ch["cid"], _demo_answer(engine, ch["cid"]), now=day_now)

    for d in range(6, 0, -1):
        play(a.child_id, now - timedelta(days=d))
    play(b.child_id, now - timedelta(days=1))
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:   # 安静
        pass

    # ---- 基础收发 ----
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path) -> None:
        if not path.exists():
            return self._send_json({"error": f"asset missing: {path.name}",
                                    "hint": "确认 client/ 与 device/ 目录存在"}, 404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CTYPE.get(path.suffix, "application/octet-stream"))
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        return json.loads(raw or b"{}")

    def _now(self) -> datetime:
        return self.server.clock["now"]

    def _clock(self) -> dict:
        now = self._now()
        return {"now": now.isoformat(timespec="minutes"), "day": now.date().isoformat()}

    # ---- HTTP 动词 ----
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in _STATIC:
            return self._send_file(_STATIC[path])
        with self.server.lock:
            try:
                return self._route_get(path)
            except KeyError as e:
                return self._send_json({"error": "not_found", "detail": str(e)}, 404)
            except Exception as e:  # noqa: BLE001
                return self._send_json({"error": type(e).__name__, "detail": str(e)}, 500)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        with self.server.lock:
            try:
                return self._route_post(path)
            except KeyError as e:
                return self._send_json({"error": "not_found", "detail": str(e)}, 404)
            except Exception as e:  # noqa: BLE001
                return self._send_json({"error": type(e).__name__, "detail": str(e)}, 500)

    # ---- 路由 ----
    def _route_get(self, path: str):
        eng: GrowthEngine = self.server.engine
        now = self._now()
        if path == "/api/clock":
            return self._send_json(self._clock())
        if path == "/api/children":
            return self._send_json({"children": eng.list_children(now=now), **self._clock()})
        if path.startswith("/api/child/"):
            parts = path.split("/")
            cid = parts[3]
            action = parts[4] if len(parts) > 4 else "home"
            if action == "home":
                return self._send_json(eng.home(cid, now=now))
            if action == "today":
                return self._send_json(eng.today(cid, now=now))
            if action == "report":
                return self._send_json(eng.report(cid, now=now))
            if action == "album":
                return self._send_json(eng.album(cid))
            if action == "state":
                return self._send_json(eng.raw_state(cid))
        raise KeyError(path)

    def _route_post(self, path: str):
        eng: GrowthEngine = self.server.engine
        now = self._now()
        if path == "/api/clock/advance":
            days = int(self._body().get("days", 1))
            self.server.clock["now"] = now + timedelta(days=days)
            return self._send_json(self._clock())
        if path == "/api/clock/reset":
            self.server.clock["now"] = BASE_NOW
            return self._send_json(self._clock())
        if path == "/api/children":
            b = self._body()
            ch = eng.create_child(b.get("name", "小朋友"), int(b.get("age", 8)),
                                  int(b.get("grade", 2)), now=now)
            return self._send_json({"child_id": ch.child_id})
        if path == "/api/seed":
            seeded = seed_demo(eng, now)
            return self._send_json({"seeded": seeded, "children": eng.list_children(now=now)})
        if path == "/api/battle":
            b = self._body()
            return self._send_json(eng.battle(b["a"], b["b"], now=now))
        if path.startswith("/api/child/"):
            parts = path.split("/")
            cid = parts[3]
            action = parts[4] if len(parts) > 4 else ""
            if action == "answer":
                b = self._body()
                return self._send_json(eng.answer(cid, b["cid"], b.get("answer", ""), now=now))
        raise KeyError(path)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--state-dir", default="runtime/growth")
    ap.add_argument("--seed", action="store_true", help="启动时若无孩子则注入演示数据")
    args = ap.parse_args()

    engine = GrowthEngine(state_dir=args.state_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.engine = engine
    httpd.clock = {"now": BASE_NOW}
    httpd.lock = threading.Lock()

    if args.seed and seed_demo(engine, BASE_NOW):
        print("[seed] 已注入演示数据：小禾（明日破壳）+ 阿哲（新蛋）")

    print(f"成长挑战机模拟器 → http://127.0.0.1:{args.port}")
    print(f"状态目录：{args.state_dir}（每个孩子一份 JSON，可直接查看/做后台测试）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
