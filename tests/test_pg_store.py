"""PostgresStateStore 测试：有 PG 时跑真库往返，无 PG 时整体跳过（CI 不挂）。

本地验证：先起库并设 RELATIONSHAPE_PG_DSN，例如
    export RELATIONSHAPE_PG_DSN=postgresql://memory@localhost:5433/memory
    pytest tests/test_pg_store.py -v
"""

import os

import pytest

from relationshape import CompanionEngine, EngineConfig
from relationshape.persistence import StateStore, build_store

DSN = os.environ.get("RELATIONSHAPE_PG_DSN", "")
_TEST_TABLE = "relationship_state_test"


def _pg_available() -> bool:
    if not DSN:
        return False
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(DSN, connect_timeout=3) as c:
            c.execute("select 1")
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _pg_available(), reason="无可用 PostgreSQL（设 RELATIONSHAPE_PG_DSN 后启用）")


@pytest.fixture
def store():
    from relationshape.pg_store import PostgresStateStore
    s = PostgresStateStore(DSN, table=_TEST_TABLE)
    s.delete("u_pg")
    s.delete("u_pg2")
    yield s
    s.delete("u_pg")
    s.delete("u_pg2")
    s.close()


# ---- 后端选择（无需 PG）----

def test_build_store_json_default():
    assert isinstance(build_store(EngineConfig()), StateStore)


def test_build_store_postgres_requires_dsn(monkeypatch):
    monkeypatch.delenv("RELATIONSHAPE_PG_DSN", raising=False)
    monkeypatch.delenv("MEMORY_PG_DSN", raising=False)
    with pytest.raises(ValueError):
        build_store(EngineConfig(state_backend="postgres", state_dsn=""))


# ---- 真库往返 ----

@pg
def test_missing_user_returns_fresh(store):
    st = store.load("u_pg")
    assert st.user_id == "u_pg"
    assert st.memory.user_name is None


@pg
def test_roundtrip_and_upsert(store):
    st = store.load("u_pg")
    st.memory.user_name = "小满"
    st.memory.preferences.append("荡秋千")
    st.turn_index = 5
    store.save(st)

    back = store.load("u_pg")
    assert back.memory.user_name == "小满"
    assert "荡秋千" in back.memory.preferences
    assert back.turn_index == 5

    back.memory.user_name = "团子"          # 覆盖写
    store.save(back)
    assert store.load("u_pg").memory.user_name == "团子"   # 同一行被 upsert，不新增


@pg
def test_engine_with_pg_backend_parity():
    """同一段对话，PG 后端与 JSON 后端落到等价状态（精确率不变量也保持）。"""
    convo = ["我叫小满", "我超级喜欢荡秋千", "我最讨厌吃苦瓜", "我妈妈最爱打麻将"]

    pg_cfg = EngineConfig(state_backend="postgres", state_dsn=DSN, state_table=_TEST_TABLE)
    epg = CompanionEngine(config=pg_cfg)
    for t in convo:
        epg.prepare_turn("u_pg2", t)
        epg.commit("u_pg2", t, "嗯")

    ejson = CompanionEngine(config=EngineConfig(state_dir="/tmp/relshape_json_test"))
    for t in convo:
        ejson.prepare_turn("u_pg2", t)
        ejson.commit("u_pg2", t, "嗯")

    fresh = CompanionEngine(config=pg_cfg).store.load("u_pg2")    # 从 PG 真读回
    mj = ejson._cache["u_pg2"].memory
    assert fresh.memory.user_name == mj.user_name == "小满"
    assert fresh.memory.preferences == mj.preferences
    assert fresh.memory.aversions == mj.aversions
    assert "麻将" not in fresh.memory.preferences        # 第三方不污染——跨 PG 仍成立
    epg.store.delete("u_pg2")
