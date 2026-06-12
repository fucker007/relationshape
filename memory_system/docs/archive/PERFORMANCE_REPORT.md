# Memory System 性能测试报告 & 上线评估

## 1. 系统架构

```
客户端
  │
  ▼
POST /api/v1/memory/chat ──→ FastAPI (uvicorn)
  │                              │
  │  1. graph_recall()           ├── PostgreSQL + pgvector (事件/人物/关系)
  │     └── embed_text()  ──→   ├── Embedding Service (BGE-M3, GPU)
  │  2. Redis RPUSH              ├── Redis (session buffer)
  │  3. 每5轮 → 后台提取         │
  │     └── WindowedExtractor    │
  ▼                              │
返回召回结果 (立即)          后台写入 DB (异步)
```

## 2. 性能优化历程

### 2.1 优化措施

| 阶段 | 措施 | 原因 |
|------|------|------|
| 1 | Embedding Docker 挂载 GPU | 原为 CPU 运行时(runc)，未分配 GPU |
| 2 | embed_text 增加内存缓存 | HTTP 模式下缓存被跳过，每次重复计算 |
| 3 | graph_recall 内并行化 | profile_summary 与 embed_text 串行执行，改为并行 |
| 4 | Embedding 服务请求合批 | 10个并发请求逐条串行推理，改为 BatchEncoder 合并为一次 GPU 推理 |

### 2.2 性能对比（10用户并发 × 10轮 = 100请求）

| 指标 | CPU 原始 | GPU | GPU+缓存+并行 | GPU+批处理+缓存 |
|------|---------|-----|-------------|----------------|
| avg | 2668ms | 331ms | 313ms | **18ms** |
| p50 | 2721ms | 370ms | 392ms | **15ms** |
| p95 | 5291ms | 474ms | 464ms | **36ms** |
| p99 | 879ms | 545ms | 387ms | **39ms** |
| QPS | 3.1 | 16.9 | 17.7 | **45.4** |
| 总耗时 | 32.1s | 5.9s | 5.7s | **2.2s** |

### 2.3 延迟分解（优化后单请求）

```
upsert_person_node ──── 3ms
graph_recall ────────── 12ms（缓存命中时）/ 80ms（需 embedding 时）
  ├─ classify_intent ── 0ms
  ├─ build_profile ──── 1ms
  ├─ embed_text ─────── 0ms（缓存命中）/ 76ms（首次）
  └─ vector_search ──── 6ms
redis_append ────────── 1ms
build_response ──────── 0ms
```

## 3. 功能验证结果

| 功能 | 状态 | 说明 |
|------|------|------|
| 召回（graph_recall） | ✅ | 意图分类 + 向量搜索 + Profile 摘要 |
| 会话累积（Redis） | ✅ | RPUSH + LTRIM 滑动窗口（20条/10轮） |
| 自动提取（每5轮） | ✅ | 异步 create_task，不阻塞响应 |
| 事件写入 | ✅ | 代词消解 + embedding + INSERT |
| 属性更新 | ✅ | ProfileUpdater.update_from_attributes() |
| 关系更新 | ✅ | 关系声明 + sentiment 更新 |
| Focus 追踪 | ✅ | FocusTracker.update() |
| 并发安全 | ✅ | 10用户并发 100% 成功率 |

## 4. 上线前必须完成的测试

### 4.1 正确性测试

#### 4.1.1 记忆准确性测试
- [ ] **召回准确率**：准备 50 组问答对，验证召回事件是否与问题相关
- [ ] **属性提取准确率**：验证姓名/爱好/职业等属性是否正确写入 person_nodes
- [ ] **关系提取准确率**：验证 "我妈妈叫李华" → relationships 表是否正确记录
- [ ] **代词消解准确率**：验证 "他很喜欢" 是否正确消解为具体人名
- [ ] **重复记忆去重**：同一事件多次提及不应重复写入

#### 4.1.2 会话边界测试
- [ ] **新会话**：不传 session_id 时自动创建，记忆独立
- [ ] **会话恢复**：传入已有 session_id，应继续累积
- [ ] **会话过期**：Redis TTL 30分钟后，session_buffer 应清空
- [ ] **过期后新会话**：过期后重新开始，不影响已持久化的记忆

#### 4.1.3 多用户隔离测试
- [ ] **owner_id 隔离**：不同 owner_id 的用户记忆完全隔离
- [ ] **同名不同人**：两个 owner 下都有 "小明"，记忆不交叉
- [ ] **并发写入隔离**：两个用户同时触发提取，数据不串

### 4.2 压力测试

#### 4.2.1 并发扩展测试
- [ ] **50 用户并发**：验证 QPS 和延迟是否线性增长
- [ ] **100 用户并发**：验证系统是否有瓶颈（连接池/GPU 显存/Redis）
- [ ] **持续压测 30 分钟**：验证内存泄漏、连接泄漏、GPU 显存泄漏

#### 4.2.2 极端场景测试
- [ ] **超长消息**：单条消息 5000 字，验证截断/处理是否正常
- [ ] **空消息**：user_message 或 assistant_message 为空
- [ ] **特殊字符**：emoji、换行符、SQL 注入字符、XSS payload
- [ ] **快速连发**：同一用户 100ms 内发送 10 条消息
- [ ] **大 session_buffer**：连续对话 100 轮，验证 LTRIM 是否正常

#### 4.2.3 资源监控
- [ ] **PostgreSQL 连接池**：压测期间无连接泄漏（pg_stat_activity）
- [ ] **Redis 内存**：监控 INFO memory，验证无异常增长
- [ ] **GPU 显存**：nvidia-smi 监控，验证无显存泄漏
- [ ] **API 进程内存**：无 Python 内存泄漏（RSS 稳定）

### 4.3 容错测试

#### 4.3.1 依赖故障
- [ ] **PostgreSQL 宕机**：API 返回降级响应（503），不 crash
- [ ] **Redis 宕机**：召回正常（走 PG），session 累积失败但不阻塞
- [ ] **Embedding 服务宕机**：向量搜索降级为空，其他功能正常
- [ ] **依赖恢复后自动重连**：各服务恢复后 API 自动恢复

#### 4.3.2 后台任务故障
- [ ] **提取失败不影响响应**：WindowedExtractor 异常时用户无感知
- [ ] **提取失败可重试**：失败的提取不丢失数据（session_buffer 仍在 Redis）
- [ ] **并发提取互不影响**：多个 session 同时提取，一个失败不影响其他

#### 4.3.3 数据一致性
- [ ] **API 重启后**：Redis session 仍在，PG 数据完整
- [ ] **Embedding 服务重启后**：缓存清空，首次请求变慢但功能正常
- [ ] **PG 事务一致性**：事件写入中途失败，不产生脏数据

### 4.4 安全测试

- [ ] **输入校验**：owner_id 必须为合法 UUID，user_name 长度限制
- [ ] **SQL 注入**：user_message 含恶意 SQL 不影响数据库
- [ ] **认证鉴权**：API 是否需要 token/API key（当前无认证）
- [ ] **限流**：单用户 QPS 限制，防止滥用
- [ ] **敏感信息**：确认日志不打印用户消息原文（GDPR/隐私）

### 4.5 部署测试

#### 4.5.1 部署流程
- [ ] **Docker Compose 一键启动**：API + PG + Redis + Embedding 全部容器化
- [ ] **健康检查**：/health 端点覆盖所有依赖
- [ ] **优雅关闭**：SIGTERM 后等待后台提取完成再退出
- [ ] **日志格式**：结构化日志（JSON），便于 ELK 采集

#### 4.5.2 监控告警
- [ ] **Prometheus 指标**：请求延迟、QPS、错误率、提取成功率
- [ ] **关键告警**：p99 > 500ms、错误率 > 1%、PG 连接池 > 80%
- [ ] **Dashboard**：Grafana 展示实时流量和延迟

#### 4.5.3 数据备份
- [ ] **PG 定时备份**：pg_dump 每日备份
- [ ] **Redis 持久化**：RDB/AOF 配置（session 数据可丢失，但 recall cache 有价值）

## 5. 上线建议

### 5.1 必须 (P0 — 上线阻断)

1. 多用户隔离测试通过
2. 50 用户并发压测通过
3. 依赖故障容错测试通过
4. 输入校验 + SQL 注入防护
5. API 认证鉴权
6. Docker Compose 部署脚本
7. 健康检查 + 优雅关闭

### 5.2 应该 (P1 — 上线后一周内)

1. 100 用户持续压测 30 分钟
2. Prometheus + Grafana 监控
3. 结构化日志
4. PG 定时备份
5. 限流策略

### 5.3 可选 (P2 — 后续迭代)

1. 记忆准确率评估（需人工标注）
2. Embedding 缓存从内存改为 Redis（多 worker 共享）
3. API 多 worker + 负载均衡
4. 读写分离（PG 主从）

## 6. 当前系统参数

| 参数 | 值 |
|------|-----|
| API 框架 | FastAPI + uvicorn |
| 数据库 | PostgreSQL + pgvector |
| 缓存 | Redis 6.x |
| Embedding 模型 | BGE-M3 (568M params) |
| GPU | NVIDIA RTX 3090 24GB |
| Embedding 精度 | FP16 |
| 向量维度 | 1024 |
| Session 窗口 | 20 条（10轮对话） |
| 提取触发 | 每 5 轮用户消息 |
| Session TTL | 30 分钟 |
| 内存缓存大小 | 10000 条 embedding |
| 批处理窗口 | 5ms / 最大 32 条 |
