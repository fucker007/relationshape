#!/usr/bin/env python3
"""测试宿主机embedding服务"""
import httpx

url = "http://localhost:8002/v1/embeddings"
data = {"input": ["测试文本", "Python编程"], "model": "BAAI/bge-m3"}

print("测试 Embedding API:")
print(f"URL: {url}")
print(f"请求: {data}\n")

resp = httpx.post(url, json=data, timeout=30.0)
print(f"状态码: {resp.status_code}")

if resp.status_code == 200:
    result = resp.json()
    print(f"✅ 成功")
    print(f"返回数据条数: {len(result['data'])}")
    print(f"Embedding维度: {len(result['data'][0]['embedding'])}")
    print(f"第一个向量前5维: {result['data'][0]['embedding'][:5]}")
else:
    print(f"❌ 失败: {resp.text}")
