#!/bin/bash
set -e

IMAGE_NAME="memory-system-allinone"
CONTAINER_NAME="memory-allinone"

# 停止旧容器
docker stop $CONTAINER_NAME 2>/dev/null && docker rm $CONTAINER_NAME 2>/dev/null || true

echo "启动 Memory System All-in-One..."

docker run -d \
    --name $CONTAINER_NAME \
    --network host \
    --gpus all \
    -v memory_pg_data:/var/lib/postgresql/data \
    -v memory_redis_data:/var/lib/redis \
    -e MEMORY_llm_provider="${MEMORY_llm_provider:-qwen}" \
    -e MEMORY_qwen_api_url="${MEMORY_qwen_api_url:-https://dashscope.aliyuncs.com/compatible-mode/v1}" \
    -e MEMORY_qwen_model_name="${MEMORY_qwen_model_name:-qwen-plus}" \
    -e MEMORY_qwen_api_key="${MEMORY_qwen_api_key:-sk-95f76087084846d2b6a677000d10f309}" \
    -e MEMORY_pg_dsn="${MEMORY_pg_dsn:-postgresql://memory:memory@localhost:5434/memory}" \
    -e MEMORY_redis_url="${MEMORY_redis_url:-redis://localhost:6379}" \
    -e MEMORY_embedding_service_url="${MEMORY_embedding_service_url:-}" \
    -e LLM_MAX_CONCURRENT="${LLM_MAX_CONCURRENT:-10}" \
    -e EXTRACT_MAX_CONCURRENT="${EXTRACT_MAX_CONCURRENT:-5}" \
    -e MEMORY_pg_pool_min="${MEMORY_pg_pool_min:-20}" \
    -e MEMORY_pg_pool_max="${MEMORY_pg_pool_max:-100}" \
    $IMAGE_NAME

echo ""
echo "等待服务启动..."
sleep 15

# 健康检查
for i in $(seq 1 10); do
    if curl -s http://localhost:8009/health | grep -q '"status"'; then
        echo ""
        curl -s http://localhost:8009/health | python3 -m json.tool
        echo ""
        echo "✓ Memory System 已就绪"
        echo ""
        echo "  API:       http://localhost:8009/api/v1/memory/chat"
        echo "  Embedding: http://localhost:8002/health"
        echo "  健康检查:  http://localhost:8009/health"
        exit 0
    fi
    echo "  等待中... ($i/10)"
    sleep 3
done

echo "WARNING: 服务可能未完全启动，查看日志："
echo "  docker logs $CONTAINER_NAME"
