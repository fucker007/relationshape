# Memory System 接入文档

## 概述

Memory System 是一个**基于人物图谱的对话记忆服务**。接入后，你的 AI 对话系统可以：

- 自动记住用户的身份、喜好、行为习惯、经历
- 每轮对话立即召回相关记忆，让 AI 回复更有温度
- 多用户完全隔离，同一用户跨 session 记忆共享

---

## 快速开始

### 1. 启动服务

```bash
cd memory_system

# 构建镜像（首次需要，约 5 分钟）
bash docker/build.sh

# 启动容器
bash docker/run.sh
```

> 默认暴露 `8010` 端口，依赖全部打包在容器内（PostgreSQL · Redis · Embedding · API）。

验证服务正常：

```bash
curl http://localhost:8010/health
# {"status":"ok","pg":true,"redis":true}
```

---

### 2. 最小接入示例（Python）

```python
import httpx

MEMORY_API = "http://localhost:8010/api/v1/memory/chat"

async def chat_with_memory(owner_id: str, user_name: str, user_message: str) -> str:
    # 1. 先查记忆（传上一轮 assistant 回复，或首轮传空字符串）
    resp = await client.post(MEMORY_API, json={
        "owner_id":          owner_id,       # 系统/租户唯一标识，UUID 格式
        "user_name":         user_name,      # 用户姓名（用于图谱节点）
        "user_message":      user_message,   # 本轮用户输入
        "assistant_message": last_reply,     # 上一轮 AI 回复（首轮传 ""）
        "session_id":        session_id,     # 同一会话保持不变，跨 session 可新建
    })
    data = resp.json()

    # 2. 把记忆注入 System Prompt
    recall = data["recall"]
    memory_context = build_context(recall)   # 见下方 build_context 示例

    # 3. 调用你自己的 LLM
    reply = await your_llm.chat(system=memory_context, user=user_message)
    return reply


def build_context(recall: dict) -> str:
    parts = []
    if recall["profile_summary"]:
        parts.append(f"用户画像：{recall['profile_summary']}")
    if recall["events"]:
        events_text = "\n".join(
            f"- [{e['event_time'][:10]}] {e['summary']}" for e in recall["events"]
        )
        parts.append(f"相关记忆：\n{events_text}")
    return "\n\n".join(parts) if parts else ""
```

---

## API 参考

### `POST /api/v1/memory/chat`

**统一记忆接口**——接入只需调用这一个接口。

#### 请求体

| 字段                | 类型   | 必填 | 说明                                        |
|---------------------|--------|------|---------------------------------------------|
| `owner_id`          | string | ✅   | 系统/租户 ID，**必须是 UUID 格式**           |
| `user_name`         | string | ✅   | 用户姓名，用于图谱中的人物节点              |
| `user_message`      | string | ✅   | 本轮用户输入                                |
| `assistant_message` | string | ✅   | 本轮 AI 回复（用于记忆积累）                |
| `session_id`        | string |      | 会话 ID；不传则自动生成并在响应中返回       |

```json
{
    "owner_id":          "550e8400-e29b-41d4-a716-446655440000",
    "user_name":         "张三",
    "user_message":      "我最近在学 Python，感觉挺有意思的",
    "assistant_message": "Python 入门很友好，你有什么具体问题吗？",
    "session_id":        "sess-abc123"
}
```

#### 响应体

| 字段                      | 类型   | 说明                                            |
|---------------------------|--------|-------------------------------------------------|
| `session_id`              | string | 本次会话 ID（首次调用时自动创建）               |
| `person_id`               | string | 用户在图谱中的 UUID                             |
| `recall.profile_summary`  | string | 用户画像摘要（身份、喜好、行为习惯等）          |
| `recall.events`           | array  | 相关历史事件列表                                |
| `recall.intent`           | string | 本轮意图类型：`person` / `event` / `general`    |
| `recall.confidence`       | string | 召回置信度：`high` / `medium` / `low` / `empty` |
| `recall.latency_ms`       | number | 召回耗时（毫秒）                                |

```json
{
    "session_id": "sess-abc123",
    "person_id":  "7c380f06-027d-4ad4-8f12-415ffead2821",
    "recall": {
        "profile_summary": "张三，热爱编程，正在学习 Python，对技术有浓厚兴趣",
        "events": [
            {
                "event_type": "learning",
                "summary":    "开始学习 Python 编程",
                "event_time": "2025-04-01T12:00:00+00:00",
                "participants": ["张三"]
            }
        ],
        "intent":      "person",
        "confidence":  "high",
        "source":      "graph",
        "latency_ms":  45.3
    }
}
```

### `GET /health`

服务健康检查。

```json
{"status": "ok", "pg": true, "redis": true}
```

---

## 内部工作机制

```
用户发送消息
     │
     ▼
① graph_recall(user_message)        ← 立即执行，毫秒级
     │  · 意图识别（person / event / general）
     │  · 向量检索相关事件
     │  · 构建画像摘要
     │
     ▼
② 返回 recall 给调用方               ← API 在此返回，不阻塞 LLM 调用
     │
     ▼
③ Redis session buffer 追加          ← 异步，20 条滑动窗口
     │
     ▼
④ 每累积 5 轮 → 后台提取             ← 完全异步，不影响响应延迟
       · LLM 提取事件 + 属性
       · 写入 PostgreSQL + pgvector
       · 更新用户画像 / 关系图谱
```

---

## 在 System Prompt 中使用记忆

推荐模板：

```python
SYSTEM_TEMPLATE = """你是一个有记忆的 AI 助手。

{memory_block}

请根据以上记忆，自然地回应用户，不要生硬地重复记忆内容。"""

def inject_memory(recall: dict) -> str:
    lines = []

    if recall.get("profile_summary"):
        lines.append("## 关于这位用户")
        lines.append(recall["profile_summary"])

    events = recall.get("events", [])
    if events:
        lines.append("\n## 相关记忆")
        for e in events[:5]:  # 最多展示 5 条
            date_str = e["event_time"][:10] if e.get("event_time") else "近期"
            lines.append(f"- {date_str}：{e['summary']}")

    memory_block = "\n".join(lines) if lines else "（暂无该用户的记忆）"
    return SYSTEM_TEMPLATE.format(memory_block=memory_block)
```

---

## 多用户隔离方案

`owner_id` 决定数据隔离边界：

| 场景             | owner_id 建议                           |
|------------------|-----------------------------------------|
| SaaS 多租户      | 每个租户一个固定 UUID                   |
| 单租户多 Bot     | 每个 Bot 一个固定 UUID                  |
| 开发/测试环境    | 每次测试生成新 UUID，互不干扰           |

同一 `owner_id` 下，同一用户(`user_name`)的记忆跨 session 自动共享。

---

## 性能参考（实测）

| 场景              | QPS    | 平均延迟 | p95 延迟 |
|-------------------|--------|----------|----------|
| 10 用户并发       | ~14    | 488ms    | 2657ms   |
| 50 用户并发×100轮 | ~324   | 130ms    | 1010ms   |

> 延迟主要来自 LLM 调用（记忆提取为后台异步，不计入）。召回本身仅需 2–50ms。

---

## 环境变量配置

启动容器时通过 `-e` 注入：

| 变量                      | 默认值                       | 说明                          |
|---------------------------|------------------------------|-------------------------------|
| `MEMORY_llm_provider`     | `qwen`                       | `anthropic` 或 `qwen`         |
| `MEMORY_qwen_api_url`     | `http://localhost:9003/v1`   | Qwen 兼容 OpenAI 的接口地址   |
| `MEMORY_qwen_api_key`     | `no-api-key-required`        | Qwen API Key                  |
| `MEMORY_anthropic_api_key`| 空                           | 使用 Anthropic 时必填         |
| `LLM_MAX_CONCURRENT`      | `10`                         | LLM 最大并发请求数            |
| `EXTRACT_MAX_CONCURRENT`  | `5`                          | 后台提取最大并发任务数        |

示例（使用本地 Qwen）：

```bash
docker run -d \
    --name memory-allinone \
    --network host \
    --gpus all \
    -v memory_pg_data:/var/lib/postgresql/data \
    -v memory_redis_data:/var/lib/redis \
    -e MEMORY_llm_provider=qwen \
    -e MEMORY_qwen_api_url=http://localhost:9003/v1 \
    memory-system-allinone
```

示例（使用 Anthropic Claude）：

```bash
docker run -d \
    --name memory-allinone \
    --network host \
    --gpus all \
    -v memory_pg_data:/var/lib/postgresql/data \
    -v memory_redis_data:/var/lib/redis \
    -e MEMORY_llm_provider=anthropic \
    -e MEMORY_anthropic_api_key=sk-ant-xxxx \
    memory-system-allinone
```

---

## 完整接入示例（FastAPI + Memory）

```python
import uuid
import httpx
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
MEMORY_API = "http://localhost:8010/api/v1/memory/chat"
OWNER_ID = "550e8400-e29b-41d4-a716-446655440000"  # 你的系统固定 UUID

# 简单的 session 存储（生产环境用 Redis）
sessions: dict[str, dict] = {}

class ChatRequest(BaseModel):
    user_name: str
    message: str
    session_id: str | None = None


@app.post("/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    last_reply = sessions.get(session_id, {}).get("last_reply", "")

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. 调用记忆接口
        mem_resp = await client.post(MEMORY_API, json={
            "owner_id":          OWNER_ID,
            "user_name":         req.user_name,
            "user_message":      req.message,
            "assistant_message": last_reply,
            "session_id":        session_id,
        })
        recall = mem_resp.json()["recall"]

    # 2. 构建带记忆的 System Prompt
    memory_text = ""
    if recall["profile_summary"]:
        memory_text += f"用户画像：{recall['profile_summary']}\n"
    if recall["events"]:
        memory_text += "相关记忆：\n" + "\n".join(
            f"- {e['summary']}" for e in recall["events"]
        )

    system_prompt = f"你是有记忆的助手。\n{memory_text}" if memory_text else "你是有记忆的助手。"

    # 3. 调用你的 LLM（此处为示意）
    reply = await your_llm_call(system=system_prompt, user=req.message)

    # 4. 保存本轮回复，供下轮作为 assistant_message
    sessions[session_id] = {"last_reply": reply}

    return {"reply": reply, "session_id": session_id}
```

---

## 常见问题

**Q: `owner_id` 格式有要求吗？**  
A: 必须是标准 UUID（32–36 字符），建议用 `uuid.uuid4()` 生成后固定下来。

**Q: `session_id` 什么时候换新的？**  
A: 同一个用户的连续对话保持同一 `session_id`；用户重新开启对话时换新 ID。换 `session_id` 不影响已积累的长期记忆。

**Q: 首轮对话 `assistant_message` 传什么？**  
A: 传空字符串 `""` 即可，系统会跳过空消息的记忆提取。

**Q: 记忆提取有延迟，影响正确性吗？**  
A: 不影响。提取在后台异步执行，当前轮召回的是已入库的历史记忆。每 5 轮触发一次提取，之后的对话就能召回到。

**Q: 容器内 PostgreSQL 数据会丢失吗？**  
A: 数据挂载到 `memory_pg_data` 卷，重启容器不丢失。只有删除 volume 才会清空。

**Q: 支持水平扩展吗？**  
A: 当前 all-in-one 模式适合单机部署。需要水平扩展时，将 PostgreSQL / Redis 独立部署，多个 API 实例共享同一 DB 即可。

---

## Swagger UI

服务启动后访问 `http://localhost:8010/docs` 可查看完整 API 文档并在线调试。
