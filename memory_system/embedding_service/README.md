# Embedding Service

BGE-M3 embedding 服务，支持 GPU + 请求合批。

## 文件结构

```
embedding_service/
├── embed.py        # FastAPI 服务（含 BatchEncoder）
├── client.py       # Python 客户端
├── Dockerfile      # Docker 构建文件
├── test_api.py     # API 测试
└── test_local.py   # 本地模型测试
```

## 启动（GPU）

```bash
docker run -d --gpus all \
  -p 8002:8002 \
  -v /path/to/models:/app/models \
  --name embedding \
  embedding-service
```

## 优化特性

- **请求合批**：5ms 窗口内的并发请求合并为一次 GPU 推理
- **线程池**：encode 不阻塞事件循环
- **FP16**：CUDA 可用时自动启用半精度

## 性能

RTX 3090 实测：
- 串行：52ms/请求
- 并发10：282ms 总（28ms/请求）
- 并发20：478ms 总（24ms/请求）
