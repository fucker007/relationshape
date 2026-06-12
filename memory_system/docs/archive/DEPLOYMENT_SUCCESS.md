# 部署成功总结

## 完成的工作

### 1. Docker 配置优化
- ✅ 修改 API 端口从 8000 改为 8009
- ✅ 使用轻量级 python:3.11-slim 基础镜像
- ✅ 分离 embedding 服务为独立容器 (bge-m3)
- ✅ 使用清华源加速依赖安装
- ✅ 添加 openai 依赖支持 Qwen API

### 2. 服务架构
```
┌─────────────────────────────────────────────────────┐
│  API (port 8009)          Worker x2                 │
│  ├─ FastAPI               ├─ Kafka Consumer         │
│  ├─ 记忆召回              ├─ LLM 提取               │
│  └─ 异步提交              └─ 并发处理 (50)         │
└─────────────────────────────────────────────────────┘
         │                           │
    ┌────┴────┬──────────────────────┴─────┬──────────┐
    │         │                            │          │
┌───▼───┐ ┌──▼──┐ ┌────────┐ ┌──────────┐ ┌────────┐
│Postgres│ │Redis│ │ Kafka  │ │Zookeeper │ │Embedding│
│(5433)  │ │(6379)│ │ (9092) │ │  (2181)  │ │ (8001) │
└────────┘ └─────┘ └────────┘ └──────────┘ └────────┘
```

### 3. 文件修改

**docker-compose.yml**
- API 端口映射: `8009:8000`
- 添加独立 embedding 服务
- 环境变量配置 Qwen LLM

**Dockerfile**
- 使用清华源: `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple`
- 移除 torch/sentence-transformers 依赖

**pyproject.toml**
- 添加 `openai>=1.0.0` 依赖

**chat_api.py**
- 默认端口改为 8009
- 使用 UUID 作为 session_id

**pipeline/embedding.py**
- 改为 HTTP 客户端调用独立 embedding 服务

**config.py**
- 添加 `embedding_service_url` 配置

## 当前状态

### 运行中的服务
```bash
$ docker compose ps
NAME                        STATUS                   PORTS
memory_system-api-1         Up                       0.0.0.0:8009->8000/tcp
memory_system-embedding-1   Up                       0.0.0.0:8001->8080/tcp
memory_system-kafka-1       Up (healthy)             0.0.0.0:9092->9092/tcp
memory_system-postgres-1    Up (healthy)             0.0.0.0:5433->5432/tcp
memory_system-redis-1       Up (healthy)             0.0.0.0:6379->6379/tcp
memory_system-worker-1      Up
memory_system-worker-2      Up
memory_system-zookeeper-1   Up                       0.0.0.0:2181->2181/tcp
```

### 健康检查
```bash
$ curl http://localhost:8009/health
{"status":"ok","pg":true,"redis":true}
```

### 测试结果
- ✅ API 服务正常响应
- ✅ 用户创建成功
- ✅ 消息提交成功
- ✅ Kafka 消息队列正常
- ✅ Worker 启动并连接 Kafka
- ⚠️  Worker LLM 连接需要配置 (Qwen API 地址)

## 使用方法

### 启动服务
```bash
docker compose up -d
```

### 停止服务
```bash
docker compose down
```

### 查看日志
```bash
docker compose logs -f api
docker compose logs -f worker
```

### 测试聊天
```bash
python chat_api.py --user 小明
```

## 注意事项

1. **LLM 配置**: Worker 需要可用的 LLM API
   - 当前配置: `http://host.docker.internal:9003/v1`
   - 如果没有本地 Qwen 服务,需要修改 `docker-compose.yml` 中的环境变量:
     ```yaml
     MEMORY_llm_provider: anthropic
     MEMORY_anthropic_api_key: your-api-key
     ```

2. **端口占用**: 确保以下端口未被占用
   - 8009 (API)
   - 8001 (Embedding)
   - 5433 (PostgreSQL)
   - 6379 (Redis)
   - 9092 (Kafka)
   - 2181 (Zookeeper)

3. **镜像大小**
   - API: ~500MB (轻量级)
   - Worker: ~500MB (轻量级)
   - Embedding: ~10GB (包含模型)

## 下一步

如需启用完整的记忆提取功能,请配置可用的 LLM API:

**选项 1: 使用 Anthropic Claude**
```yaml
environment:
  MEMORY_llm_provider: anthropic
  MEMORY_anthropic_api_key: sk-ant-xxx
```

**选项 2: 使用本地 Qwen**
确保 `http://host.docker.internal:9003/v1` 可访问

**选项 3: 使用其他 OpenAI 兼容 API**
```yaml
environment:
  MEMORY_llm_provider: qwen
  MEMORY_qwen_api_url: https://your-api-endpoint/v1
  MEMORY_qwen_api_key: your-key
  MEMORY_qwen_model_name: your-model
```
