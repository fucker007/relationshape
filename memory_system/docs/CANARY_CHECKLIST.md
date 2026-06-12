# Memory System 灰度上线 Checklist

适用版本：分支 `fix/persona_dedupe_identity_placeholder`
覆盖 commit：4045915 → 0645162 → 8d4c93a → 006c78a → 58917b8 → 35aa6e4

---

## 0. 本次包含的改动（按风险）

| Commit  | 类别 | 描述                                      | 风险 |
|---------|------|-----------------------------------------|------|
| 4045915 | feat | 第一人称代词归一化（人家/咱/俺...→我）             | 低 |
| 0645162 | P0-1 | Outbox：extract task 落 PG 重启可恢复          | 中 |
| 8d4c93a | perf | recall p95 1232ms → 63ms                | 低 |
| 006c78a | P0/T3| 启动时 schema fail-fast guard            | 中 |
| 58917b8 | T2   | relationships.state 写入 + sentiment MAX_ABS | 低 |
| 35aa6e4 | P0/b3| 常驻 graph poller + extract 并发 5→20        | 中 |

**对外 API 契约**：零变化（仍 `POST /api/v1/memory/chat`）。

---

## 1. 上线前验证（已完成 ✅）

- [x] V2 泛化测试 46/46 PASS（`reports/tester_generalization_v2.py`）
- [x] 刘杨 regression 9/9 PASS，含容器重启 R3（`reports/tester_liuyang_regression.py`）
- [x] R1 并发压测：conc=20 → 399 rps p95=56.8ms 错误 0%
       （`reports/r1_concurrency_bench.py` + `r1_bench_results.json`）
- [x] poller 启动 reset 验证：5343 processing → 0
- [x] poller 吞吐验证：1.6 task/s（R1 前 1.0，+60%）
- [x] schema_guard 双向对照（缺列拒启动）

---

## 2. 上线参数（环境变量）

```bash
# extract 并发（从 5 提到 20）
EXTRACT_MAX_CONCURRENT=20

# graph poller 调参（不需要时全用默认）
GRAPH_POLLER_FAST_SEC=0.5     # pending 非空轮询间隔
GRAPH_POLLER_SLOW_SEC=3.0     # pending 空轮询间隔
GRAPH_POLLER_BATCH=20         # 单轮 claim 上限
GRAPH_POLLER_LEASE_SEC=300    # processing 行 lease 超时
```

---

## 3. 部署步骤（memory-allinone 单实例）

1. 备份 PG（最近 24h schema 有改动）：
   ```
   PGPASSWORD=memory pg_dump -h <host> -p 5434 -U memory memory > backup_$(date +%Y%m%d_%H%M).sql
   ```

2. 拉新镜像 / 同步代码到容器：
   ```
   docker cp <src>/api/* memory-allinone:/app/memory/api/
   docker cp <src>/storage/* memory-allinone:/app/memory/storage/
   docker restart memory-allinone
   ```

3. 等 ready（~12-15s）：
   ```
   for i in $(seq 1 30); do sleep 2; \
     curl -sf http://<host>:8010/health >/dev/null && echo ready && break; done
   ```

4. **强制确认 4 个启动信号**：
   - `[schema_guard] OK` 在 `/var/log/supervisor/api.log`
   - `[poller] startup reset processing→pending: UPDATE N` （N 应=之前残留的 processing 数）
   - `[poller] started batch=20 fast=0.5s slow=3.0s lease=300s`
   - `Application startup complete.`

   任一缺失 → **回滚**（`docker restart` 旧镜像 / `git checkout` 上一 commit 重 cp）。

---

## 4. 上线后 5 分钟健康观察（必看）

| 指标                          | 阈值                     | 查询                                               |
|------------------------------|--------------------------|----------------------------------------------------|
| API 错误率                    | < 0.5%                   | dashboard 或 access log 5xx 计数                    |
| `/api/v1/memory/chat` p95     | < 200ms                  | dashboard                                          |
| extraction_tasks pending      | 不持续单调上升             | `SELECT count(*) FROM extraction_tasks WHERE task_type='graph' AND status='pending'` |
| extraction_tasks processing   | 0 < x ≤ 20               | 同上换 status='processing'                          |
| extraction_tasks failed       | 5 分钟内增量 < pending 的 5% | 同上换 status='failed'                              |
| done 增长速率                  | ≥ 1.0 task/s             | 两次采样 `count(status='done')` 差 / 间隔秒          |
| poller alive                  | 进程内任务存在             | `grep '\[poller\]' /var/log/supervisor/api.log` 不间断 |

---

## 5. 第一小时观察

- 每 15min 看一次 P95、错误率、pending/processing/done 趋势
- 检查 `api_err.log`：CUDA busy 警告若变密集（embedding 抢卡）→ 暂停灰度，开 P2 排查
- 拉两次 SQL 看一致性：
  ```sql
  SELECT count(*) FROM events WHERE created_at > NOW()-INTERVAL '1 hour';
  SELECT count(*) FROM relationships WHERE updated_at > NOW()-INTERVAL '1 hour';
  ```
  events 应持续涨；relationships 涨幅约为 events 的 30-60%。

---

## 6. 回滚条件（任一触发即回滚）

- API 错误率 > 1% 持续 5min
- `/memory/chat` p95 > 500ms 持续 5min
- extraction_tasks failed 5min 增量 > 50
- pending 持续上涨 30min 且 done 速率 < 0.5 task/s
- 启动日志缺失 schema_guard / poller signal
- PG 连接池耗尽 / Redis OOM

回滚命令：
```
git checkout 58917b8         # 回到 b3 之前
docker cp ... && docker restart memory-allinone
```

回滚后必须验证：4 个启动信号中除 `[poller]` 两条外其他仍要在。

---

## 7. 已知限制（已评估可接受）

- **首轮无记忆**：extract 异步 ~10-30s 完成，对话层有 10 轮上下文兜底，产品上不阻塞。
- **HEAVY LLM ~9s**：吞吐上限 ~2.2 task/s/实例。流量峰值 > 此值时 pending 会短期堆积，poller 会自动消化。
- **单实例假设**：startup reset 把 processing→pending 是按"无其他活实例"假设。多实例化前必须改为 worker_id 过滤或纯靠 lease 超时。
- **CUDA 共享**：embedding + LLM 共用 GPU，偶有 CUDA busy。需独立 GPU 或拆 service 是 P2。

---

## 8. 上线后 24h 验证

- [ ] V2 46/46 在生产数据 owner 上抽样回归（5 个真实 owner）
- [ ] `extraction_tasks` failed 总量 < done 的 1%
- [ ] events / relationships 表增量符合流量预期
- [ ] 无 schema_guard 报错重启
- [ ] poller 24h 无 stop（grep `[poller] stopped`）

---

## 9. 联系人

- DEV：本次 PR 作者
- 回滚 owner：on-call 值班
- DBA：备份 / 恢复

---

最后更新：2026-04-23
