"""PostgreSQL 后端本地冒烟：建表 + 一段对话往返 + JSON 路径查询。

用法（指向你 memory_system 的 PG 实例）：
    export RELATIONSHAPE_PG_DSN=postgresql://memory:密码@localhost:5433/memory
    python eval/pg_smoke.py
表会自动创建（relationship_state）；也可先手动 psql -f relationshape/sql/001_relationship_state.sql。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from relationshape import CompanionEngine, EngineConfig  # noqa: E402

DSN = os.environ.get("RELATIONSHAPE_PG_DSN") or os.environ.get("MEMORY_PG_DSN")
if not DSN:
    raise SystemExit("请先 export RELATIONSHAPE_PG_DSN=postgresql://用户:密码@主机:端口/库")

cfg = EngineConfig(state_backend="postgres", state_dsn=DSN)
uid = "smoke_kid"

eng = CompanionEngine(config=cfg)
print("后端:", type(eng.store).__name__)
for t in ["我叫小满", "我超级喜欢荡秋千", "我最讨厌吃苦瓜", "我妈妈最爱打麻将", "你还记得我叫什么吗"]:
    eng.prepare_turn(uid, t)
    eng.commit(uid, t, "嗯嗯")

st = CompanionEngine(config=cfg).store.load(uid)        # 全新实例从 PG 读回
m = st.memory
print(f"读回  name={m.user_name!r}  prefs={m.preferences}  avers={m.aversions}")
print(f"轮次={st.turn_index}  阶段={st.core.stage.value}")
assert m.user_name == "小满" and "荡秋千" in m.preferences and "苦瓜" in m.aversions
assert "麻将" not in m.preferences          # 第三方不污染
print("✓ PostgreSQL 后端往返成功，精确率不变量保持")

eng.store.delete(uid)                        # 清理冒烟数据
eng.store.close()
