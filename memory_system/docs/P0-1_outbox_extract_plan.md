# P0-1 Outbox Extract 实施计划

## 目标
消除 T1（容器重启 in-flight extract 100% 丢失）。
保持 API 契约不变（选项 X：响应不加字段）。

## 设计

### 数据流
```
POST /memory/chat
  ├─ 同步写 extraction_tasks (status=pending, payload=buffer JSON)
  ├─ fire-and-forget create_task(_do_extract(task_id=...))
  └─ 立即返回 200（对话系统零感知）

_do_extract:
  start → UPDATE status=processing
  成功 → UPDATE status=done, completed_at=NOW()
  异常 → UPDATE status=failed, error_message=..., retry_count++

启动 hook (lifespan):
  扫 status IN ('pending','processing','failed') AND retry_count<max_retries
  逐个 create_task(_do_extract) 恢复
```

### Schema 扩展
```sql
ALTER TABLE extraction_tasks
    ADD COLUMN IF NOT EXISTS owner_id UUID,
    ADD COLUMN IF NOT EXISTS task_type VARCHAR(20) DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS payload JSONB,
    ALTER COLUMN message_content DROP NOT NULL,
    ALTER COLUMN message_role DROP NOT NULL,
    ALTER COLUMN turn_index DROP NOT NULL,
    ALTER COLUMN person_id DROP NOT NULL,
    ALTER COLUMN session_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_extraction_tasks_graph_pending
    ON extraction_tasks(scheduled_at)
    WHERE task_type='graph' AND status IN ('pending','processing','failed');
```

message_content/role/turn_index 对旧 legacy worker 仍 NOT NULL 会炸，因此统一放松；
旧代码走 create_task 仍会填它们（兼容）。

### 代码改动
1. `storage/pg_store.py::PgStore.migrate()` 追加上述 ALTER
2. `storage/pg_store.py` 新增:
   - `create_graph_task(task_id, owner_id, person_id, session_id, payload: dict) -> None`
   - `get_pending_graph_tasks(limit=100) -> list[dict]`
   - `update_task_status` 复用（已有）
3. `api/routers/memory_chat.py`:
   - 触发点 L739 区：改为
     ```
     task_id = uuid.uuid4()
     payload = {"buffer": buffer, "user_name": req.user_name}
     await pg.create_graph_task(task_id, owner_id, person_id, session_id, payload)
     asyncio.create_task(_do_extract_with_task(task_id, ...))
     ```
   - `_do_extract_with_task` 包 status 迁移 + 失败上报
4. `api/main.py::lifespan`:
   - 启动后新增 `await _recover_pending_graph_tasks(app.state)`

### 回归测试
- V2 46/46 保持
- round3 21/21 保持
- T1 复现实验：5 轮 ACK 后 restart，events 最终应到 4+（从 0 变非 0）
- Task 状态：pending→processing→done 迁移可查

### 不做
- 不改 API 响应（选项 X）
- 不改对话系统
- 不动老 /memories、/tasks legacy worker 路径
