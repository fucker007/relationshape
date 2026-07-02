"""
FastAPI 应用入口。

lifespan 管理所有共享资源的创建和销毁。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from api.routers import memories, persons, tasks
from config import settings
from pipeline.merge_worker import get_merge_worker
from storage.pg_store import PgStore, create_pool
from storage.redis_store import RedisStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- 启动 ---
    logger.info("initializing storage...")

    pool = await create_pool()
    pg = PgStore(pool)
    await pg.migrate()
    app.state.pg = pg

    from storage.pg_store import GraphStore
    gs = GraphStore(pool)
    await gs.migrate()
    app.state.gs = gs

    # T3 fail-fast: schema 校验，缺字段直接抛错让容器重启暴露故障
    from api.schema_guard import verify_schema
    await verify_schema(pool)

    redis = await RedisStore.create()
    app.state.redis = redis

    # Kafka 路径已永久弃用：消息提取走 Outbox（PG extraction_tasks + asyncio.create_task
    # + GraphTaskPoller），不再启动 producer/consumer/DLQ。

    merge_worker = get_merge_worker()
    await merge_worker.start()
    app.state.merge_worker = merge_worker

    from llm.client import LLMClient
    llm = LLMClient()
    app.state.llm = llm

    # Warmup：预热 embedding 连接 + DB 连接池
    try:
        from pipeline.embedding import embed_text
        await embed_text("warmup")
        logger.info("embedding warmup done")
    except Exception as e:
        logger.warning(f"embedding warmup failed: {e}")

    logger.info("memory service ready")

    # P0-1 Outbox + A1 常驻 poller：
    #   - poller 启动后会立即领取所有 pending（含崩溃残留），不需要单独 resume
    #   - SKIP LOCKED 保证多实例安全；单实例部署也安全
    try:
        from api.graph_task_poller import GraphTaskPoller, set_poller
        poller = GraphTaskPoller(pg, gs, llm)
        await poller.start()
        set_poller(poller)
        app.state.graph_poller = poller
    except Exception as e:
        logger.warning(f"[poller] start failed: {e}")

    yield

    # --- 关闭 ---
    try:
        p = getattr(app.state, "graph_poller", None)
        if p is not None:
            await p.stop()
    except Exception as e:
        logger.warning(f"[poller] stop failed: {e}")
    await merge_worker.drain()
    await merge_worker.stop()
    await redis.close()
    await pool.close()
    logger.info("memory service shutdown")


def require_api_key(request: Request) -> None:
    """接口鉴权：未配置 MEMORY_API_KEYS 时不启用（不破坏现有部署）；
    配置后所有业务接口要求 X-API-Key 头，健康检查/文档/指标放行。"""
    keys = {k.strip() for k in (settings.api_keys or "").split(",") if k.strip()}
    if not keys:
        return
    path = request.url.path
    if path in ("/", "/health", "/healthz", "/ready", "/openapi.json") or \
            path.startswith(("/docs", "/redoc", "/metrics")):
        return
    if request.headers.get("X-API-Key", "") not in keys:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


_cors_origins = [o.strip() for o in (settings.cors_origins or "*").split(",") if o.strip()] or ["*"]

app = FastAPI(
    title="Person Memory System",
    version="1.0.0",
    description="Real-time person-centric memory system for chat AI",
    lifespan=lifespan,
    dependencies=[Depends(require_api_key)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Routers
app.include_router(memories.router, prefix="/api/v1")
app.include_router(persons.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")

from api.routers import graph_memories
app.include_router(graph_memories.router, prefix="/api/v1")

from api.routers import memory_chat
app.include_router(memory_chat.router, prefix="/api/v1")

from api.routers import dashboard
app.include_router(dashboard.router)


@app.get("/health")
async def health() -> dict:
    pg_ok = await app.state.pg.ping()
    redis_ok = await app.state.redis.ping()
    return {
        "status": "ok" if pg_ok and redis_ok else "degraded",
        "pg": pg_ok,
        "redis": redis_ok,
    }
