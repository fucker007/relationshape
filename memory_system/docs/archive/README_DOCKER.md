# 记忆系统 - 快速开始

## 一键启动

```bash
./start.sh
```

等待 30 秒后，访问：
- **API 文档**: http://localhost:8009/docs
- **健康检查**: http://localhost:8009/health

## 测试示例

```python
import httpx
import asyncio

async def test():
    client = httpx.AsyncClient(base_url="http://localhost:8009")
    
    # 1. 创建人物
    resp = await client.post("/api/v1/persons", json={
        "external_id": "user_123",
        "display_name": "小明"
    })
    person = resp.json()
    person_id = person["person_id"]
    print(f"创建人物: {person_id}")
    
    # 2. 提交消息
    resp = await client.post("/api/v1/memories/extract-and-ingest", json={
        "person_id": person_id,
        "session_id": "session_001",
        "message": "我今年10岁，在阳光小学上学，最喜欢打篮球",
        "role": "user",
        "turn_index": 1,
        "context_turns": []
    })
    print(f"提交消息: {resp.json()['task_id']}")
    
    # 等待处理
    await asyncio.sleep(3)
    
    # 3. 召回记忆
    resp = await client.post("/api/v1/memories/recall", json={
        "person_id": person_id,
        "context": "你喜欢什么运动？",
        "format": "summary"
    })
    result = resp.json()
    print(f"\n召回结果:\n{result['summary']}")
    
    await client.aclose()

asyncio.run(test())
```

## 停止服务

```bash
docker-compose down
```

详细文档见 [DEPLOYMENT.md](DEPLOYMENT.md) 和 [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
