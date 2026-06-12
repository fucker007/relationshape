#!/bin/bash
set -e

echo "=========================================="
echo "  Memory System All-in-One 初始化"
echo "=========================================="

# ── PostgreSQL 初始化 ────────────────────────────────────────
PG_VERSION=$(ls /usr/lib/postgresql/ | head -1)
PG_BIN="/usr/lib/postgresql/$PG_VERSION/bin"
PG_DATA="/var/lib/postgresql/data"
PG_PORT="${PG_PORT:-5433}"

echo "[1/5] PostgreSQL $PG_VERSION"

if [ ! -f "$PG_DATA/PG_VERSION" ]; then
    echo "  初始化数据库..."
    chown -R postgres:postgres /var/lib/postgresql
    su - postgres -c "$PG_BIN/initdb -D $PG_DATA"

    # 配置 pg_hba.conf 允许本地连接
    echo "local all all trust" > "$PG_DATA/pg_hba.conf"
    echo "host all all 127.0.0.1/32 trust" >> "$PG_DATA/pg_hba.conf"
    echo "host all all ::1/128 trust" >> "$PG_DATA/pg_hba.conf"

    # 配置监听端口
    sed -i "s/#port = 5432/port = $PG_PORT/" "$PG_DATA/postgresql.conf"

    # 启动 PG 创建用户和数据库
    su - postgres -c "$PG_BIN/pg_ctl -D $PG_DATA -o '-p $PG_PORT' -l /tmp/pg_init.log start"
    sleep 3

    su - postgres -c "psql -p $PG_PORT -c \"CREATE USER memory WITH PASSWORD 'memory' SUPERUSER;\""
    su - postgres -c "psql -p $PG_PORT -c \"CREATE DATABASE memory OWNER memory;\""
    su - postgres -c "psql -p $PG_PORT -d memory -c \"CREATE EXTENSION IF NOT EXISTS vector;\""

    su - postgres -c "$PG_BIN/pg_ctl -D $PG_DATA stop"
    sleep 2
    echo "  ✓ 初始化完成"
else
    echo "  ✓ 数据已存在，跳过初始化"
fi

# ── 更新 supervisord 中 PG 路径和端口 ──────────────────────────
sed -i "s|PG_VERSION_PLACEHOLDER|$PG_VERSION|g" /etc/supervisor/conf.d/supervisord.conf
sed -i "s|PG_PORT_PLACEHOLDER|$PG_PORT|g" /etc/supervisor/conf.d/supervisord.conf

# ── 显示配置 ──────────────────────────────────────────────────
echo ""
echo "[2/5] 配置信息"
echo "  PG:        $MEMORY_pg_dsn"
echo "  Redis:     $MEMORY_redis_url"
echo "  Embedding: $MEMORY_embedding_service_url"
echo "  LLM:       $MEMORY_llm_provider ($MEMORY_qwen_api_url)"
echo "  LLM 并发:  $LLM_MAX_CONCURRENT"
echo "  提取并发:  $EXTRACT_MAX_CONCURRENT"

# ── 数据库迁移 ────────────────────────────────────────────────
echo ""
echo "[3/5] 等待服务启动并执行迁移..."

# 先启动 PG 和 Redis
su - postgres -c "$PG_BIN/pg_ctl -D $PG_DATA -o '-p $PG_PORT' -l /tmp/pg.log start"
redis-server --daemonize yes --port 6379
sleep 3

# 执行迁移
cd /app/memory
python -c "
import asyncio, asyncpg

async def migrate():
    pool = await asyncpg.create_pool('$MEMORY_pg_dsn', min_size=1, max_size=2)
    from storage.pg_store import GraphStore
    gs = GraphStore(pool)
    await gs.migrate()
    await pool.close()
    print('  ✓ 数据库迁移完成')

asyncio.run(migrate())
"

# 停止手动启动的服务，交给 supervisor 管理
redis-cli shutdown 2>/dev/null || true
su - postgres -c "$PG_BIN/pg_ctl -D $PG_DATA stop" 2>/dev/null || true
sleep 2

echo ""
echo "[4/5] 启动所有服务..."
echo ""
echo "  服务列表："
echo "    - PostgreSQL  :5432"
echo "    - Redis       :6379"
echo "    - Embedding   :8002 (GPU)"
echo "    - Memory API  :8009"
echo ""
echo "[5/5] supervisord 接管"
echo "=========================================="

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
