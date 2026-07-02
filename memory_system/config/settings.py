from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEMORY_", env_file=".env", extra="ignore")

    # Anthropic
    anthropic_api_key: str = ""
    extraction_model: str = "claude-sonnet-4-6"
    embedding_model: str = "BAAI/bge-m3"
    embedding_model_path: str = "/app/models/BAAI/bge-m3"
    embedding_device: str = "cuda"
    embedding_dim: int = 1024
    embedding_service_url: str = ""  # 如果设置则使用HTTP服务，否则使用本地模型

    # PostgreSQL
    pg_dsn: str = "postgresql://memory:***@localhost:5433/memory"
    pg_pool_min: int = 20
    pg_pool_max: int = 100

    # Redis
    redis_url: str = "redis://localhost:6379"
    redis_max_connections: int = 100

    # Kafka 已弃用（永久切换到 Outbox: extraction_tasks + asyncio.create_task + GraphTaskPoller）

    # Extraction pipeline
    extraction_concurrency: int = 50          # 每 Worker 进程的协程数
    extraction_min_message_len: int = 10      # 低于此长度跳过提取
    extraction_confidence_threshold: float = 0.4  # 低于此置信度丢弃
    extraction_max_retries: int = 3

    # Recall
    recall_default_limit: int = 15
    recall_max_per_type: int = 3
    recall_min_importance: float = 0.3
    recall_vector_weight: float = 0.5
    recall_importance_weight: float = 0.3
    recall_recency_weight: float = 0.2
    recall_recency_decay: float = 0.1        # 每天衰减系数

    # Dedup
    dedup_similarity_threshold: float = 0.92  # 向量余弦相似度阈值

    # Redis TTL (seconds)
    redis_profile_ttl: int = 7 * 24 * 3600      # 7 天
    redis_memories_ttl: int = 30 * 24 * 3600     # 30 天
    redis_memory_detail_ttl: int = 24 * 3600     # 24 小时
    redis_recall_cache_ttl: int = 5 * 60         # 5 分钟

    # LLM model config
    llm_model: str = "claude-3-5-sonnet-20241022"
    llm_fast_model: str = "claude-haiku-4-5-20251001"

    # Merge Worker
    merge_concurrency: int = 20
    merge_queue_maxsize: int = 500
    merge_similar_limit: int = 5
    merge_similarity_threshold: float = 0.82

    # Forgetting
    forget_score_threshold: float = 0.05
    forget_importance_protect: float = 0.8
    forget_batch_size: int = 50
    forget_judge_concurrency: int = 5
    forget_hard_delete_delay: int = 3600

    # Capacity quotas (per person, per type)
    memory_quota: dict = Field(default_factory=lambda: {
        "identity": 20_000, "personality": 15_000,
        "behavior": 30_000, "preference": 50_000,
        "aversion": 50_000, "experience": 150_000,
        "joy": 80_000, "pain": 80_000,
    })

    # Decay rates per type
    forget_lambda: dict = Field(default_factory=lambda: {
        "identity": 0.001, "personality": 0.002,
        "behavior": 0.005, "experience": 0.008,
        "preference": 0.010, "aversion": 0.010,
        "joy": 0.015, "pain": 0.015,
    })

    # Qwen 配置（记忆系统专用 LLM，与聊天系统分离）
    llm_provider: str = "qwen"  # "anthropic" 或 "qwen"
    qwen_api_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model_name: str = "qwen-plus"
    qwen_api_key: str = ""   # 通过环境变量 MEMORY_QWEN_API_KEY 注入，切勿硬编码进源码

    @property
    def effective_model(self) -> str:
        """当前 provider 对应的主模型名称"""
        return self.qwen_model_name if self.llm_provider == "qwen" else self.llm_model

    @property
    def effective_fast_model(self) -> str:
        """当前 provider 对应的快速模型名称"""
        return self.qwen_model_name if self.llm_provider == "qwen" else self.llm_fast_model


settings = Settings()
