#!/usr/bin/env python3
"""测试embedding功能"""
import asyncio
import sys
sys.path.insert(0, '/home/zihai/workspace/Agent_server_design/memory_system')

from pipeline.embedding import embed_text
from config import settings

async def main():
    print(f"Embedding服务URL: {settings.embedding_service_url}")
    print(f"Embedding模型: {settings.embedding_model}")

    test_text = "我喜欢Python编程"
    print(f"\n测试文本: {test_text}")
    print("调用embedding...")

    vec = await embed_text(test_text)

    if vec:
        print(f"✅ 成功")
        print(f"维度: {len(vec)}")
        print(f"前5维: {vec[:5]}")
    else:
        print(f"❌ 失败")

if __name__ == "__main__":
    asyncio.run(main())
