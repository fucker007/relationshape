"""
Embedding 客户端 - 调用 Docker 服务
"""
import httpx
from typing import List
import numpy as np


class EmbeddingClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.Client(timeout=30.0)

    def encode(self, texts: List[str], convert_to_numpy: bool = True):
        """生成 embedding"""
        response = self.client.post(
            f"{self.base_url}/embed",
            json={"texts": texts}
        )
        response.raise_for_status()

        data = response.json()
        embeddings = data["embeddings"]

        if convert_to_numpy:
            return np.array(embeddings)
        return embeddings

    def health_check(self) -> bool:
        """健康检查"""
        try:
            response = self.client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except:
            return False

    def close(self):
        self.client.close()


# 异步接口
async def get_embedding(text: str) -> List[float]:
    """异步获取单个文本的 embedding"""
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:8000/embed",
            json={"texts": [text]}
        )
        response.raise_for_status()
        data = response.json()
        return data["embeddings"][0]


# 全局实例
_embedding_client = None


def get_embedding_client() -> EmbeddingClient:
    """获取全局 embedding 客户端"""
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = EmbeddingClient()
    return _embedding_client
