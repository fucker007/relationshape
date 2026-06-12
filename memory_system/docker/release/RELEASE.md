# memory-allinone v1.0.0 — 发布说明 & 接入指南

发布日期：2026-04-27
镜像 tag：memory-allinone:v1.0.0  (latest 同步)
压缩包 sha256：6faab581504e03f913b7caba0192b3c4df685c189860949b84f658642b13a7bb

记忆/检索一体化容器。包含 PostgreSQL、Redis、Embedding (BAAI/bge-m3 1024d)
和 Memory API，单容器即可运行。LLM 走外部（默认阿里云 dashscope，兼容
OpenAI 协议），同事自备 API Key。

## 0. 发布质量指标 (Real MSC, 10 样本)

| 指标                            | 数值      | 备注              |
|---------------------------------|-----------|-------------------|
| Events Coverage                 | 80.00%    | 达基线 (≥80%)     |
| Profile Coverage                | 70.00%    |                   |
| Combined (any layer)            | 100.00%   | 全部样本至少命中  |
| Recall confidence (high)        | 80%       | 其余 uncertain    |
| LLM Judge — Memory Usage        | 0.96      | qwen-plus         |
| LLM Judge — Dialogue Quality    | 0.88      |                   |
| LLM Judge — Semantic Match      | 0.50      |                   |
| Avg Judge Score                 | 0.78      | 100% calls 成功   |
| Semantic similarity (bge-m3)    | 0.5774    | cosine            |
| BLEU / ROUGE-L                  | 0.0065 / 0.0789 | 词面分,参考 |
| 平均耗时                        | 22s/样本  | 含 15s extract 等待 |

测试运行：results/real_msc_20260427_191605.json

---

## 1. 系统要求

- Linux 主机，Docker >= 20.10、Docker Compose v2
- 内存 >= 8 GB（embedding 模型常驻 ~3 GB）
- 磁盘 >= 30 GB（镜像解压 12 GB + 运行时数据）
- 网络：能访问 LLM endpoint（默认 dashscope.aliyuncs.com）
- 端口（host network 模式直接绑宿主，确保未被占用）：
    8010  Memory API
    5434  PostgreSQL（建议 firewall 屏蔽外网）
    6380  Redis      （同上）
    8002  Embedding 服务（仅容器内自用）

---

## 2. 文件清单

    docker-compose.release.yml      启动配置
    .env.example                    环境变量模板
    memory-allinone-v1.0.0.tar.gz   镜像（约 5–6 GB）
    memory-allinone-v1.0.0.tar.gz.sha256
    RELEASE.md                      本文档

---

## 3. 部署步骤

### 3.1 校验 + 导入镜像

    sha256sum -c memory-allinone-v1.0.0.tar.gz.sha256
    docker load -i memory-allinone-v1.0.0.tar.gz
    docker images memory-allinone

### 3.2 配置环境变量

    cp .env.example .env
    vim .env                      # 至少填 LLM_API_KEY

### 3.3 启动

    docker compose -f docker-compose.release.yml --env-file .env up -d
    docker logs -f memory-allinone     # 等 ~60s，看到 "Uvicorn running on ... 8010"

### 3.4 健康检查

    # 1) API 存活（owner_id 必须是 UUID）
    UUID=$(uuidgen)
    curl -fsS -X POST http://127.0.0.1:8010/api/v1/memory/chat \
      -H 'Content-Type: application/json' \
      -d "{
        \"owner_id\":\"$UUID\",
        \"user_name\":\"healthcheck\",
        \"session_id\":\"hc_$UUID\",
        \"user_message\":\"你好\",
        \"assistant_message\":\"你好\"
      }" | head -c 300

    # 2) PG / 服务进程
    docker exec memory-allinone supervisorctl status

---

## 4. 接入示例

### 4.1 写入 + 召回（一次 chat 请求同时完成）

每次对话提交一对 user/assistant 消息。系统会同步返回召回结果，
异步触发抽取（每累积 5 轮 user_message 触发一次落库）。

    curl -X POST http://127.0.0.1:8010/api/v1/memory/chat \
      -H 'Content-Type: application/json' \
      -d '{
        "owner_id": "550e8400-e29b-41d4-a716-446655440000",
        "user_name": "小明",
        "session_id": "sess_a",
        "user_message": "我叫小明，今年7岁，在阳光小学上一年级",
        "assistant_message": "你好小明！很高兴认识你。"
      }'

返回字段（节选）：
    {
      "session_id": "sess_a",
      "person_id":  "...",                 # 内部 person 主键（UUID）
      "recall": {
        "profile_summary": "...",          # 当前 owner 的人物画像摘要
        "events": [...],                   # 召回的历史事件
        "session_context": [...],          # 当前 session 最近 N 轮
        "intent": "general",
        "confidence": "uncertain|high|empty",
        "source": "vector|graph|degraded",
        "latency_ms": 670.5
      }
    }

请求字段说明：

| 字段              | 类型   | 必填 | 说明                              |
|-------------------|--------|------|-----------------------------------|
| owner_id          | UUID   | ✓    | 对话归属（用户/角色），必须 UUID  |
| user_name         | string | ✓    | 当前用户的姓名（用于身份识别）    |
| session_id        | string | ✓    | 会话 ID（同 session 共享上下文）  |
| user_message      | string | ✓    | 用户本轮输入                      |
| assistant_message | string | ✓    | 助手本轮回复                      |

> 抽取触发条件：`user_count % 5 == 0`（每 5 轮触发 1 次 outbox task）。
> 单轮调用不会写入 events/relationships，但 person_nodes 会同步落库。

### 4.2 Python 客户端（推荐）

仓库提供 `memory_system_client`，调用方式与生产保持一致。

---

## 5. 数据持久化与备份

- PG 数据   : volume `pg_data`     → `/var/lib/postgresql/data`
- Redis 数据 : volume `redis_data` → `/var/lib/redis`

备份：

    docker exec memory-allinone pg_dump -U memory memory \
      > backup_$(date +%F).sql

升级镜像：

    docker compose -f docker-compose.release.yml down
    # 加载新镜像 tag，调整 compose 中 image: 行
    docker compose -f docker-compose.release.yml --env-file .env up -d
    # volume 不会被删除，数据保留

---

## 6. 配置说明（.env 全量）

| 变量            | 默认                                                       | 说明                  |
|-----------------|-----------------------------------------------------------|-----------------------|
| LLM_PROVIDER    | qwen                                                      | 仅 qwen（OpenAI 兼容）|
| LLM_API_URL     | https://dashscope.aliyuncs.com/compatible-mode/v1         | 端点                  |
| LLM_MODEL       | qwen-plus                                                 | 模型名                |
| LLM_API_KEY     | (必填)                                                    | API Key               |
| MEMORY_LANG     | zh                                                        | zh / en               |

---

## 7. 质量基线（v1.0.0）

中文 16 样本回归，这个是自己制作的测试集合（test_zh_dataset/data/zh_memory_test.json）：

    通过率              : 15/16  (93.75%)
    Events Coverage 平均 : 88.4%
    Judge Score   平均   : 0.91
    Combined Score 平均  : 0.89

英文msc数据集回归 
   通过率 95%左右，

唯一未通过：`zh_11_kid_name_evolution`（"曾用名"召回缺失），属边角 case。

---

## 8. 已知限制

- **首轮无记忆**：抽取走异步 outbox（10~30s 落库），首次提到的事实
  在同一 turn 内召回不到。生产侧应缓存最近 N 轮对话历史叠加 prompt。
- **曾用名 / alias 历史**：`person_nodes` 无 alias 历史列，名字演化场景
  只保留最新名（zh_11 失分原因）。
- **Kafka 已弃用**：抽取走 PG outbox（`extraction_tasks` 表）+ asyncio
  调度，无 Kafka/ZK 依赖。
- **MEMORY_LANG 切换需重启**：抽取 prompt 在启动时载入。
- **embedding 维度固定** 1024（bge-m3），切模型需重建 events 向量列。

---

## 9. 故障排查

| 现象                          | 排查                                                |
|------------------------------|---------------------------------------------------|
| 启动后 chat 返回 500          | `docker logs memory-allinone` 看 LLM endpoint / key|
| events 始终为空               | 等 30s 后再查（异步抽取）；查 `extraction_tasks.status`|
| 8010 端口被占                 | `ss -tlnp \| grep 8010`，停掉占用进程或改宿主 iptables|
| PG 连不上                     | 容器内 `psql $MEMORY_pg_dsn -c '\l'` 验证          |
| 升级后数据丢失                | 检查 volume 是否被 `docker volume rm` 误删          |

容器内常用命令：

    docker exec -it memory-allinone bash
    psql "$MEMORY_pg_dsn" -c "SELECT count(*) FROM events;"
    redis-cli -p 6379 DBSIZE
    cat /proc/<pid>/cmdline | tr '\0' ' '   # 容器内无 ps

---

## 10. 联系

问题反馈 → 项目仓库 issue / 内部 IM。
