#!/bin/bash
set -e

# ── Memory System All-in-One Docker ──────────────────────────
# 基于 embedding 镜像构建，包含：
#   PostgreSQL + pgvector
#   Redis
#   BGE-M3 Embedding (GPU)
#   Memory API (FastAPI)

IMAGE_NAME="memory-system-allinone"
CONTAINER_NAME="memory-allinone"

echo "=========================================="
echo "  构建 Memory System All-in-One"
echo "=========================================="

cd "$(dirname "$0")/.."

# 构建镜像
docker build \
    -f docker/Dockerfile.memory \
    -t $IMAGE_NAME \
    .

echo ""
echo "✓ 镜像构建完成: $IMAGE_NAME"
echo ""
echo "运行方式："
echo ""
echo "  docker run -d \\"
echo "    --name $CONTAINER_NAME \\"
echo "    --network host \\"
echo "    --gpus all \\"
echo "    -v memory_pg_data:/var/lib/postgresql/data \\"
echo "    -v memory_redis_data:/var/lib/redis \\"
echo "    -e MEMORY_qwen_api_url=http://localhost:9003/v1 \\"
echo "    $IMAGE_NAME"
echo ""
echo "端口："
echo "  8009 — Memory API"
echo "  8002 — Embedding Service"
echo "  5432 — PostgreSQL"
echo "  6379 — Redis"
