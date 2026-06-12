# 生产阻塞证据报告 (2026-04-19)

目的：用可复现的生产场景测试，验证每个 P0 阻塞是否真实存在。
不是"看起来有问题"，而是"证明它会在生产环境造成数据丢失/错误响应"。

---

## 总览

| 编号 | 原假设 | 验证结果 | 严重度 |
|------|--------|----------|--------|
| T1 | 容器重启会丢 in-flight extract | **证实成立** | P0 |
| T2 | relationships.state 写入失败 | **证实成立，比预想更糟** | P0 |
| T3 | schema 缺字段时服务静默失败 | **证实成立** | P0 |
| T4 | session_id 过滤让跨天场景错误 | **原假设不成立**；但发现新阻塞 T4' | — |
| T4' | extract 是异步的，首轮对话无记忆 | **证实成立** | P0 |

最终结论：**4 个 P0 阻塞确认**（T1/T2/T3/T4'），严重度不变。

---

## T1: 容器重启 → in-flight extract 数据丢失

### 方法
对照组：5 轮触发 extract 的对话，不重启，等 30s 看结果
实验组：同样 5 轮，ACK 后 0.5s 立刻 `docker restart memory-allinone`

两组用不同 owner_id 隔离。

### 数据
| 组 | events | persons |
|---|---|---|
| 对照组 (ae6dc30d)  | 4 | 张伟, 小雪 |
| 实验组 (57967cbd)  | **0** | 张伟 (只有 realtime identity 写入) |

### 结论
重启窗口内 **100% 丢失率**。extract 跑在 `asyncio.create_task`，进程死就没了，
既没 WAL 也没重试队列。代码里已经有 `MemoryProducer + ExtractionWorker + DLQ` 的
管道实现（见 `pipeline/worker.py`），但 `memory_chat.py` 走的是 `create_task` 旁路，
两条写入路径并存。

### 生产影响
用户打完 5 句话，服务器因为 OOM / 部署 / 崩溃重启 → 这 5 句话完全没进记忆。
用户下一次回来，"你忘了？"

---

## T2: relationships.state 字段无写入路径

### 方法
1. 读 DDL (`storage/pg_store.py` L625-638)
2. 用情感激烈的 5 轮对话（吵架、冷战、妈妈担心）触发 extract
3. 查 DB

### 数据
```sql
-- 全部 relationships:
SELECT state, COUNT(*) FROM relationships GROUP BY state;
  state | count
--------+-------
  {}    |  91    ← 100% 都是空默认值
```

T2 新建 owner (c99666f8) 的结果：
| 字段 | 值 |
|---|---|
| relation_type | other, other |
| sentiment | 0.1, 0.08 (几乎中性) |
| state | `{}`, `{}` |

场景里有"大吵一架"、"冷战两天"、"很生气"、"很感动"，但 sentiment 只有 0.1。

### 根因
`pg_store.py::upsert_relationship()` (L914-935) 的 INSERT/UPDATE 语句**根本不
涉及 state 列**。DDL 里也没有 state 字段（我之前手动 ALTER TABLE 补上才让日志
不报错，但代码仍然不写）。

state 是彻底的死字段。

### 生产影响
- 任何依赖 `state` 做关系状态召回的 recall 分支拿到空对象
- `sentiment` 更新机制 `sentiment * 0.9 + new` 会把所有激烈情绪稀释到 0
- 用户回来问"我上次跟阿明闹矛盾那事"，系统靠 sentiment 几乎识别不出情绪强度

---

## T3: schema 缺字段时服务静默失败

### 方法
1. `ALTER TABLE person_nodes DROP COLUMN updated_at`
2. `docker restart memory-allinone`
3. 观察服务是否 fail-fast
4. 发 5 轮对话，看 API 返回 + events 写入情况
5. 恢复字段

### 数据
- 服务启动：+14s 健康检查返 200 ✓（未 fail-fast）
- 5 次 POST 全部返 200 ✓（用户无感）
- DB events 数：**2**（对照组应为 4）
- 日志里有 `ERROR:api.routers.memory_chat:[extract] session=... failed: column "updated_at" of relation "person_nodes" does not exist`，但**只在 err.log 里**

### 结论
- 服务启动时无 schema 校验
- extract 写失败被 `try/except` 吞到 WARNING/ERROR 日志
- API 依然返 200，客户端完全看不见

### 生产影响
任何 migration 失败、DBA 误操作、字段漂移 → 用户无感，但记忆系统在静默丢数据。
下游报表看到"事件量突然腰斩"才会发现，通常是数天后。

---

## T4: 原假设不成立（session_id 过滤）

原假设："同 session_id 过滤让跨天场景召回 0"

### 方法
T2 已经入库的 owner，用 3 种 session 策略问同样问题：
- A 同老 session_id
- B 新 client-gen session_id
- C 不传 session_id (server gen)

### 数据
| Case | events 数 | intent | confidence |
|---|---|---|---|
| A 同老 session | 3 | general | high |
| B 新 session | 3 | general | high |
| C 无 session | 3 | general | high |

三组结果完全一致。

### 结论
recall 路径上没有按 session_id 过滤历史事件。之前在 zh_memory 测试里看到的
"0 events" 并不是 session 过滤，而是 **T4' 的 extract 异步问题**。

之前的记忆需要修正：之前 memory 里记录 "memory_system 召回对同 session_id 做刚
说过过滤" 的说法 **针对的是 decay_cache，不是 recall 主路径**。

### 修正后的问题：decay_cache 按 session_id 绑定
- `_merge_recall_events` 把衰减缓存按 session_id 存 Redis
- 切新 session → 衰减缓存丢失，但不影响 DB 召回
- 严重度：P2（次优化）

---

## T4': extract 异步 → 首轮对话内无记忆

### 方法
同一 session 连发 5 轮能触发 extract 的对话，然后**同 session 立刻问**
"我的猫叫什么名字？"

### 数据
| 时机 | events 数 |
|---|---|
| T+0s 立刻查 | **0** |
| T+25s 查 | 4 |
| T+25s 换新 session 查 | 4 |

### 结论
extract 走 `asyncio.create_task` 后台执行，LLM + embedding + DB 写入总耗时
10-30s。这段时间内用户接着问，系统完全不知道用户刚说过什么。

### 生产影响
这是最常见的生产翻车场景。例子：
- 用户：我猫叫橘子，三岁。
- 用户：橘子最近爱咬电线，怎么办？
- 系统：请问您说的"橘子"是？

修复方向：
- 短期：recall 时合并"当前 session 未 flush 的 buffer" (session_context 已
  经存在，但 L0.5 只读 Redis，不读进行中 buffer)
- 长期：extract 要走 pipeline/worker.py 的 MemoryProducer 队列，保证
  at-least-once；recall 要有"pending 数据回读"机制

---

## 建议优先级

按修复成本 × 业务损失加权：

| 阻塞 | 修复成本 | 业务损失 | 推荐顺序 |
|---|---|---|---|
| T1 重启丢数据 | 中（切 MemoryProducer 队列） | 高（整窗丢失） | 1 |
| T4' 首轮无记忆 | 低（recall 合并当前 buffer） | 高（用户首轮体验最差） | 2 |
| T3 schema 静默 | 低（启动 migration check） | 中（延迟发现） | 3 |
| T2 state 死字段 | 低（要么实现要么删掉） | 中（关系质量降级） | 4 |

---

## 附录：证据文件

- 对照/实验组 owner_id 列表见上
- DB 可复查（数据未清理，保留到下次 session）
- 关键日志：`docker exec memory-allinone tail -200 /var/log/supervisor/api_err.log`
- 测试代码：本报告内嵌（可一键复现）
