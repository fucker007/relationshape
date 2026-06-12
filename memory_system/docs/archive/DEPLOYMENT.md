# 记忆系统 - 一键部署指南

## 快速启动

### 1. 启动所有服务

```bash
./start.sh
```

这将自动启动：
- PostgreSQL (端口 5433)
- Redis (端口 6379)
- Kafka + Zookeeper (端口 9092)
- Memory API (端口 8000)
- Extraction Workers (2 个实例)

### 2. 验证服务

```bash
# 检查健康状态
curl http://localhost:8000/health

# 查看 API 文档
open http://localhost:8000/docs
```

### 3. 停止服务

```bash
docker-compose down
```

### 4. 清理数据（重置）

```bash
docker-compose down -v  # 删除所有数据卷
```

---

## 配置说明

### 使用本地 Qwen 模型（默认）

确保 Qwen 服务运行在 `http://localhost:9003`

```bash
# 检查 Qwen 服务
curl http://localhost:9003/v1/models
```

### 切换到 Anthropic Claude

修改 `docker-compose.yml`:

```yaml
api:
  environment:
    MEMORY_llm_provider: anthropic
    MEMORY_anthropic_api_key: sk-ant-xxx
```

---

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| API | 8000 | HTTP API |
| PostgreSQL | 5433 | 数据库 |
| Redis | 6379 | 缓存 |
| Kafka | 9092 | 消息队列 |
| Zookeeper | 2181 | Kafka 协调 |

---

## 常用命令

```bash
# 查看日志
docker-compose logs -f api
docker-compose logs -f worker

# 重启服务
docker-compose restart api

# 扩展 Worker
docker-compose up -d --scale worker=4

# 进入容器
docker-compose exec api bash
docker-compose exec postgres psql -U memory

# 查看资源使用
docker stats
```

---

## 测试 API

```bash
# 创建人物
curl -X POST http://localhost:8000/api/v1/persons \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "user_123",
    "display_name": "小明"
  }'

# 提交消息
curl -X POST http://localhost:8000/api/v1/memories/extract-and-ingest \
  -H "Content-Type: application/json" \
  -d '{
    "person_id": "YOUR_PERSON_ID",
    "session_id": "session_001",
    "message": "我今年10岁，喜欢打篮球",
    "role": "user",
    "turn_index": 1,
    "context_turns": []
  }'

# 召回记忆
curl -X POST http://localhost:8000/api/v1/memories/recall \
  -H "Content-Type: application/json" \
  -d '{
    "person_id": "YOUR_PERSON_ID",
    "context": "你喜欢什么运动？",
    "format": "summary",
    "limit": 15
  }'
```

---

## 故障排查

### 1. Kafka 连接失败

```bash
# 检查 Kafka 状态
docker-compose logs kafka

# 重启 Kafka
docker-compose restart kafka zookeeper
```

### 2. PostgreSQL 连接失败

```bash
# 检查数据库
docker-compose exec postgres psql -U memory -c "SELECT 1"

# 查看日志
docker-compose logs postgres
```

### 3. Worker 无法提取记忆

```bash
# 检查 Worker 日志
docker-compose logs worker

# 检查 Qwen 服务
curl http://localhost:9003/v1/models
```

### 4. 端口冲突

如果端口被占用，修改 `docker-compose.yml` 中的端口映射：

```yaml
ports:
  - "8001:8000"  # 改为 8001
```

---

## 性能调优

### 扩展 Worker 数量

```bash
# 启动 4 个 Worker
docker-compose up -d --scale worker=4
```

### 调整并发数

修改 `docker-compose.yml`:

```yaml
worker:
  environment:
    MEMORY_extraction_concurrency: "100"  # 每个 Worker 的协程数
```

### 增加 Redis 内存

```yaml
redis:
  command: redis-server --maxmemory 4gb --maxmemory-policy allkeys-lru
```

---

## 生产部署建议

1. **使用外部数据库**: 不要使用 Docker 内的 PostgreSQL
2. **使用 Redis Cluster**: 提高缓存可用性
3. **使用 Kafka Cluster**: 3 个 broker 节点
4. **水平扩展 Worker**: 根据负载动态扩展到 10-50 个
5. **添加监控**: Prometheus + Grafana
6. **配置日志**: 使用 ELK 或 Loki
7. **启用 HTTPS**: 使用 Nginx 反向代理

---

## 监控

访问 Prometheus metrics:

```bash
curl http://localhost:8000/metrics
```

关键指标：
- `memory_extraction_duration_seconds`: 提取耗时
- `memory_recall_duration_seconds`: 召回耗时
- `memory_ingest_total`: 写入总数
- `memory_dedup_total`: 去重总数
