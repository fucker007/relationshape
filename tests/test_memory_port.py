"""MemoryPort 融合层契约测试：mock memory_system 服务 + 线缆级安全断言。"""

import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from relationshape.config import EngineConfig
from relationshape.engine import CompanionEngine
from relationshape.memory_port import MemorySystemAdapter

T0 = datetime(2026, 1, 1, 9, 0)


class _MockMemorySystem:
    """实现 memory_system 两个核心端点语义的内存 mock，记录全部请求（线缆审计）。"""

    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.events: list[dict] = []
        self.profile = "小禾，5岁。喜欢：恐龙、画画。近期关注：钢琴比赛。和同桌乐乐最近闹过别扭。"

        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静默
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append((self.path, body))
                if self.path == "/api/v1/graph/recall":
                    q = body.get("query_text", "")
                    hits = [e for e in mock.events if any(t in e["summary"] for t in q) ][:6]
                    resp = {
                        "events": hits,
                        "profile_summary": mock.profile if body.get("include_profile") else None,
                    }
                elif self.path == "/api/v1/memory/chat":
                    resp = {"ok": True}
                else:
                    resp = {}
                data = json.dumps(resp, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def seed(self, summary: str, days_ago: int, importance: float = 0.8):
        self.events.append({
            "summary": summary,
            "event_time": (T0 - timedelta(days=days_ago)).isoformat(),
            "event_time_raw": f"{days_ago}天前",
            "importance": importance,
        })

    def close(self):
        self.server.shutdown()


@pytest.fixture
def mock_ms():
    m = _MockMemorySystem()
    yield m
    m.close()


def _engine(tmp_path, port=None):
    return CompanionEngine(
        config=EngineConfig(state_dir=str(tmp_path / "s")), memory_port=port,
    )


# ---------------------------------------------------------------- 适配器契约

def test_adapter_maps_remote_events_with_time_anchor(mock_ms):
    mock_ms.seed("说过下周要参加钢琴比赛，很紧张", days_ago=7)
    ad = MemorySystemAdapter(mock_ms.url)
    profile, recalls = ad.recall("u1", "钢琴比赛怎么样了", T0, want_profile=True)
    assert profile and "恐龙" in profile
    assert recalls and recalls[0].kind == "remote"
    assert recalls[0].days_ago == 7
    assert "7天前" in recalls[0].hint            # 时间锚进提示（SOTA对齐：时间是结构）


def test_adapter_fail_open_on_dead_server():
    ad = MemorySystemAdapter("http://127.0.0.1:1", timeout=0.2)   # 死端口
    profile, recalls = ad.recall("u1", "钢琴", T0)
    assert profile is None and recalls == []
    ad.observe("u1", "x", "y", "s1", T0)          # 不抛异常即通过


# ---------------------------------------------------------------- 引擎融合行为

def test_remote_memory_flows_into_directive(tmp_path, mock_ms):
    mock_ms.seed("说过要和爸爸一起做机器人翅膀", days_ago=3)
    eng = _engine(tmp_path, MemorySystemAdapter(mock_ms.url))
    d = eng.prepare_turn("u", "机器人翅膀做得怎么样啦", now=T0)
    assert any(m.kind == "remote" and "翅膀" in m.text for m in d.memories)
    assert "长期记忆" in d.to_prompt_context()


def test_profile_summary_on_session_start_only(tmp_path, mock_ms):
    eng = _engine(tmp_path, MemorySystemAdapter(mock_ms.url))
    d1 = eng.prepare_turn("u", "你好呀", now=T0)
    assert d1.profile_summary and "【TA是谁】" in d1.to_prompt_context()
    eng.commit("u", "你好呀", "（回复）", now=T0)
    d2 = eng.prepare_turn("u", "我们聊聊恐龙吧", now=T0 + timedelta(minutes=2))
    assert d2.profile_summary is None             # 非首轮不要档案，省指令长度


def test_crisis_turn_never_crosses_the_wire(tmp_path, mock_ms):
    """E4 线缆级断言：危机轮内容零外发——隐私边界在引擎。"""
    eng = _engine(tmp_path, MemorySystemAdapter(mock_ms.url))
    d = eng.prepare_turn("u", "爸爸今天打我了", now=T0)
    assert d.safety is not None
    eng.commit("u", "爸爸今天打我了", "（安全回应）", now=T0)
    crisis_payloads = [
        b for _, b in mock_ms.requests
        if "打我" in json.dumps(b, ensure_ascii=False)
    ]
    assert not crisis_payloads                    # 整个危机轮：零请求载有内容


def test_forwarding_respects_pollution_filter(tmp_path, mock_ms):
    """设备抱怨/对角色的攻击进账本不进图谱；生活话题正常外发。"""
    eng = _engine(tmp_path, MemorySystemAdapter(mock_ms.url))
    t = T0
    for text in ("怎么又卡了", "你真笨"):
        eng.prepare_turn("u", text, now=t)
        eng.commit("u", text, "（回复）", now=t)
        t += timedelta(minutes=2)
    chats = [b for p, b in mock_ms.requests if p.endswith("/memory/chat")]
    assert not chats
    eng.prepare_turn("u", "我今天画了一只翼龙", now=t)
    eng.commit("u", "我今天画了一只翼龙", "（回复）", now=t)
    chats = [b for p, b in mock_ms.requests if p.endswith("/memory/chat")]
    assert len(chats) == 1 and "翼龙" in chats[0]["user_message"]


def test_no_port_means_identical_default_behavior(tmp_path):
    eng = _engine(tmp_path, port=None)
    d = eng.prepare_turn("u", "你好呀", now=T0)
    assert d.profile_summary is None
    assert all(m.kind != "remote" for m in d.memories)
