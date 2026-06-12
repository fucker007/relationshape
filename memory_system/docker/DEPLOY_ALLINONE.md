# All-in-One 部署指南

## 一键部署

```bash
# 1. 构建镜像
docker compose -f docker-compose.allinone.yml build

# 2. 启动服务
docker compose -f docker-compose.allinone.yml up -d

# 3. 查看日志
docker compose -f docker-compose.allinone.yml logs -f

# 4. 测试
python chat_api.py --user 测试用户
```

## 架构说明

**单容器包含:**
- PostgreSQL (内置,端口 5432)
- Redis (内置,端口 6379)
- API 服务 (端口 8009)
- Worker 服务

**外部依赖 (宿主机):**
- LLM API: http://127.0.0.1:9003/v1
- Embedding 服务: http://127.0.0.1:8002

## 环境变量配置

修改 `docker-compose.allinone.yml` 中的环境变量:

```yaml
environment:
  # 使用 Anthropic Claude
  MEMORY_llm_provider: anthropic
  MEMORY_anthropic_api_key: sk-ant-xxx
  
  # 或使用其他 OpenAI 兼容 API
  MEMORY_llm_provider: qwen
  MEMORY_qwen_api_url: https://your-api/v1
  MEMORY_qwen_api_key: your-key
```

## 数据持久化

PostgreSQL 数据存储在 Docker volume `pg_data` 中,容器重启数据不丢失。

## 停止服务

```bash
docker compose -f docker-compose.allinone.yml down
```

## 完全清理 (包括数据)

```bash
docker compose -f docker-compose.allinone.yml down -v
```

## 优势

- ✅ 一个容器,易于部署和迁移
- ✅ 使用 host 网络,可访问宿主机服务
- ✅ 数据持久化
- ✅ 自动重启
- ✅ 统一日志管理 (supervisor)
