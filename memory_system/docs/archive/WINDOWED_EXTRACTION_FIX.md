# 窗口提取修复说明

## 问题
chat_api.py 的记忆提取被 ExtractionGate 拦截，导致窗口提取（每5轮）无法正常工作。

## 解决方案
添加 `bypass_gate` 参数，允许窗口提取跳过 ExtractionGate 检查。

## 修改的文件

### 1. models/memory.py
- 在 `ExtractAndIngestRequest` 添加 `bypass_gate: bool = False` 字段

### 2. pipeline/worker.py
- `build_extraction_message()` 添加 `bypass_gate` 参数
- `MemoryProducer.send_extraction_task()` 添加 `bypass_gate` 参数
- `ExtractionWorker._handle_message()` 检查 `bypass_gate`，如果为 True 则跳过 ExtractionGate

### 3. api/routers/memories.py
- `extract_and_ingest()` 传递 `req.bypass_gate` 到 producer

### 4. chat_api.py
- `MemoryAPIClient.ingest_message()` 添加 `bypass_gate` 参数
- `background_extract()` 调用时设置 `bypass_gate=True`

### 5. retrieval/recall.py
- 修复：从 events 表改为 memory_entries 表
- 添加调试日志

### 6. api/routers/memories.py (recall endpoint)
- 修复：返回格式从 events 改为 memory_entries 结构

## 测试结果

### 窗口提取测试
```bash
python chat_api.py --user 李明 --debug
```

输入5条消息后，成功触发窗口提取：
```
  [触发] 窗口提取（第 5 轮用户消息）
  [提取] 已提交窗口提取 (2条消息, task_id: 8e8cdc15)
```

Worker 日志显示成功提取：
```
INFO:__main__:extracted 2 memories for person=285b2c88-7296-4529-8aaa-39a157a10f09
```

### 数据库验证
```bash
python /tmp/check_db.py
```

确认记忆已存储：
- identity: 该用户在上海工作
- preference: 该用户喜欢打篮球

### 向量搜索测试
```bash
python /tmp/test_vector_search.py
```

直接调用 pg.vector_search() 正常工作，返回正确结果。

## 待解决问题

### API 召回返回空结果
虽然直接调用 recall() 函数正常工作，但通过 API 调用返回 0 结果。

**原因**: API 服务器运行的是旧代码（Apr02 启动），需要重启加载新代码。

**解决方法**:
```bash
sudo ./restart_api_sudo.sh
```

API 服务器以 root 权限运行，需要 sudo 重启。

## 验证步骤

1. 重启 API 服务器（需要 sudo）:
   ```bash
   sudo ./restart_api_sudo.sh
   ```

2. 测试召回功能:
   ```bash
   python chat_api.py --user 李明 --debug
   ```
   
   输入命令:
   ```
   :recall 工作
   :recall 篮球
   ```

3. 测试完整对话流程:
   - 输入5条消息触发窗口提取
   - 等待10秒让 worker 处理
   - 使用 :recall 测试召回
   - 继续对话，验证记忆被使用

## 工作原理

### 窗口提取流程
1. 用户每发送一条消息，添加到 session_buffer
2. 每5条用户消息触发一次窗口提取
3. 取最后10轮对话作为窗口
4. 前5轮作为上下文，后5轮作为提取目标
5. 合并5条用户消息，设置 bypass_gate=True
6. 发送到 Kafka，worker 跳过 ExtractionGate 直接提取

### ExtractionGate 绕过
- 单条消息: 经过 ExtractionGate 过滤（可能被拦截）
- 窗口提取: bypass_gate=True，跳过过滤（保证提取）

这样既保留了单条消息的过滤机制，又确保窗口提取不会被误拦截。
