# API 系统与 chat.py 完全对齐说明

## 概述

API 系统已完全对齐 chat.py 的 Person Graph 架构和处理流程。

## 核心架构

### Person Graph 组件
- **PersonNode**: 存储用户属性（identity, preferences, aversions, behaviors, current_focus）
- **Event**: 存储事件记录（events 表）
- **Relationship**: 存储人物关系（relationships 表）
- **Focus**: 追踪用户关注点

### 数据流
```
用户消息 → graph_recall() → LLM 回复（即时）
         ↓ 后台（每5轮触发）
         WindowedExtractor → 事件写入 → Profile更新 → Focus更新
```

## API 端点

### 1. `/api/v1/graph/recall` - 召回检索
**对齐**: `retrieval/graph_recall.py::graph_recall()`

### 2. `/api/v1/graph/extract` - 窗口提取
**对齐**: `chat.py::background_process()`

## 完整对齐的处理步骤

### Step 1: 窗口提取
- WindowedExtractor 提取事件和属性

### Step 2: 人物节点写入
- 创建 secondary 节点并建立关系

### Step 3: 事件写入
- 时间标准化
- 代词消解
- Embedding
- 关系 sentiment 更新

### Step 4: 属性更新
- ProfileUpdater.update_from_attributes()

### Step 4.5: 关系声明正则兜底
- ProfileUpdater.update_from_declaration()

### Step 5: Focus 追踪
- FocusTracker.update()

## 客户端使用

### chat_graph.py（直接调用）
```bash
python chat_graph.py --user 小明 --debug
```

支持命令：`:memories`, `:help`, `:profile`, `:recall`, `:debug`

### chat_api_v2.py（API 调用）
```bash
python chat_api_v2.py --user 小明 --api-url http://localhost:8009 --debug
```

## 关键修复点

1. ExtractedEvent: 使用 `ev.action` + 代词消解 → `resolved_text`
2. ProfileUpdater: `update_from_attributes()`, `update_from_event()`, `update_from_declaration()`
3. FocusTracker: `update(person_id, focus_text, related_names)`
4. GraphStore.insert_event: 使用字典参数
5. 后台任务: 使用 `await` 而非 `asyncio.create_task()`

## 验证

✅ 事件提取成功
✅ 属性更新成功
✅ 命令系统正常
✅ Debug 模式打印提示词
