# 儿童陪伴机器人长效记忆系统（LLMS）

**Person-Centric Long-term Memory System for Child Companion AI**

实时可搜索、结构化提取、以人为中心的记忆系统。
目标：> 10K QPS，P99 写入 < 20ms，P99 召回 < 100ms。

---

## 目录

1. [系统架构](#1-系统架构)
2. [记忆类型体系](#2-记忆类型体系)
3. [数据模型](#3-数据模型)
4. [存储设计](#4-存储设计)
5. [提取流水线](#5-提取流水线)
6. [召回流水线](#6-召回流水线)
7. [置信度与冲突解决](#7-置信度与冲突解决)
8. [指代消解（EntityBuffer）](#8-指代消解entitybuffer)
9. [API 接口](#9-api-接口)
10. [CLI 调试工具](#10-cli-调试工具)
11. [快速启动](#11-快速启动)
12. [配置参数](#12-配置参数)
13. [测试与评估](#13-测试与评估)
14. [项目结构](#14-项目结构)

---

## 1. 系统架构

```
对话服务
  │
  ├─→ POST /memories/extract-and-ingest   fire-and-forget, < 5ms
  │         │
  │         └─→ Kafka: conversation.messages (64 partitions, 按 person_id 路由)
  │                    │
  │               Extraction Worker Pool (HPA, 20-70 Pods, 50 协程/Pod)
  │                    ├─ 预过滤 (规则层, < 10 字跳过)
  │                    ├─ 上下文组装 (Redis 拉取人物摘要)
  │                    ├─ Claude API 结构化提取 (~600ms)
  │                    ├─ Embedding 生成 (~100ms)
  │                    ├─ 去重检查 (Redis Lua 原子操作)
  │                    ├─ 写入 Redis (立即可搜索)
  │                    └─ Kafka: memory.persist → PostgreSQL
  │
  └─→ POST /memories/recall               三路并行搜索, < 100ms P99
            ├─ 向量搜索  (pgvector HNSW, weight=0.5)
            ├─ 重要性兜底 (Redis Sorted Set, weight=0.3)
            └─ 关键词匹配 (Redis 反向索引, weight=0.2) + 时序衰减
```

**关键设计决策：**

- **写入路径解耦**：对话服务仅入队（< 5ms），提取在后台 Worker 异步完成（~700ms）
- **Redis 热路径**：写入后立即可被召回，无需等待 PG 持久化
- **Cluster 分片**：所有 key 使用 `{person_id}` hash tag，同人数据落同一 slot
- **三路融合召回**：向量语义 + 重要性排名 + 关键词精确，互补防漏

---

## 2. 记忆类型体系

系统共 9 种记忆类型，覆盖儿童的完整人格画像：

| 类型 | 枚举值 | 描述 | 示例 |
|------|--------|------|------|
| 身份 | `identity` | 姓名、年龄、学校、年级 | "小明今年8岁，就读光明小学二年级" |
| 性格 | `personality` | 内向/外向、认真/活泼 | "小明性格认真负责，做事不罢休" |
| 行为 | `behavior` | 作息规律、固定仪式、生活习惯 | "小明每天晚上九点睡觉，睡前要喝牛奶" |
| 喜好 | `preference` | 食物、科目、游戏、宠物、英雄偶像 | "小明最喜欢吃草莓，喜欢上体育课" |
| 厌恶 | `aversion` | 禁忌食物、恐惧对象、反感行为 | "小明非常害怕吸尘器的声音" |
| 经历 | `experience` | 重要事件、社交互动 | "小明和小红去公园玩了一整天" |
| 开心 | `joy` | 喜悦来源、令人高兴的事 | "小明考了满分，开心地一直傻笑" |
| 痛苦 | `pain` | 委屈、遗憾、恐惧、负面经历 | "小明因为考试没考好而难过了很久" |
| 关系 | `relationship` | 好友、老师、家人关系 | "小明最好的朋友是萍萍，认识三年了" |

> **可变类型（Mutable）**：`preference`、`aversion`、`joy`、`pain` 会随时间更新，旧记忆得分随时间衰减。
> **稳定类型（Stable）**：`identity`、`personality`、`behavior`、`relationship` 不应用衰减。

---

## 3. 数据模型

### MemoryEntry（核心记忆条目）

```python
class MemoryEntry(BaseModel):
    memory_id:         UUID              # 唯一 ID
    person_id:         UUID              # 对应人物
    session_id:        UUID              # 来源会话
    memory_type:       MemoryType        # 9 种类型之一

    content:           str               # 第三人称自然语言描述
    structured_data:   dict              # 依类型而定（见下）

    importance_score:  float             # 0.0-1.0，重要程度
    confidence_score:  float             # 0.0-1.0，提取置信度
    emotional_valence: float             # -1.0(负面) ~ +1.0(正面)
    emotional_intensity: float           # 0.0-1.0，情绪强度

    embedding:         list[float]|None  # 1536-dim（text-embedding-3-small）
    source_message:    str               # 原始消息文本
    is_merged:         bool              # 是否经过融合
    version:           int               # 乐观锁版本号
    created_at:        datetime
    expires_at:        datetime|None
```

### 各类型 structured_data 字段

```python
# identity
{"name": str, "age": int, "age_range": str, "occupation": str,
 "location_city": str, "gender": str, "education_level": str}

# personality
{"dimension": str, "trait": str, "evidence": str, "intensity": float}

# behavior
{"pattern_name": str, "description": str, "frequency": str, "context": str}

# preference / aversion
{"category": str, "item": str, "strength": float, "reason": str,
 "is_sensitive": bool}  # aversion 额外字段

# experience
{"event_summary": str, "time_reference": str, "location": str,
 "people_involved": [str], "outcome": str, "is_significant": bool}

# joy / pain
{"trigger": str, "emotional_response": str, "intensity": float,
 "is_recurring": bool, "related_memory_ids": [str]}
```

### RecallResponse（召回响应）

```python
class RecallResponse(BaseModel):
    memories:          list[MemorySearchResult] | None
    summary:           str | None           # format=summary 时填充
    search_latency_ms: int
    sources:           dict[str, int]       # {"vector": N, "importance": N, "keyword": N}
    confidence:        Literal["high", "uncertain", "low", "empty"]  # 置信信号
    top_score:         float                # 最高得分，用于判断结果质量
```

---

## 4. 存储设计

### Redis（热路径，立即可搜索）

```
# 人物画像
{person_id}:profile                 → Hash（display_name, summary, version）

# 按类型分层的记忆索引（score = importance × 1000 + timestamp_factor）
{person_id}:memories:{type}         → Sorted Set（member=memory_id）

# 记忆详情（24h TTL，冷却后从 PG 回填）
memory:{memory_id}                  → Hash（content, structured_data, scores）

# 关键词反向索引（精确匹配用）
{person_id}:kw:{keyword}            → Set（memory_ids）

# 去重指纹（防并发重复写入）
{person_id}:fingerprints            → Set（SHA256[:24]）

# 召回缓存（5分钟 TTL）
{person_id}:recall:{ctx_hash}       → String（summary 文本）
```

> 所有 key 均以 `{person_id}` 为 hash tag，确保同人数据路由至同一 Redis Cluster slot。

**并发安全机制：**

- **去重 Lua 脚本**：`SISMEMBER + SADD` 原子操作，防止并发写入相同记忆
- **Profile CAS Lua 脚本**：乐观锁版本号控制，防止并发更新画像时数据覆盖

### PostgreSQL + pgvector（持久化 + 向量搜索）

```sql
CREATE TABLE memory_entries (
    memory_id        UUID PRIMARY KEY,
    person_id        UUID NOT NULL REFERENCES persons,
    session_id       UUID NOT NULL,
    memory_type      VARCHAR(50) NOT NULL,
    content          TEXT NOT NULL,
    structured_data  JSONB NOT NULL DEFAULT '{}',
    importance_score FLOAT NOT NULL DEFAULT 0.5,
    confidence_score FLOAT NOT NULL DEFAULT 0.5,
    emotional_valence FLOAT NOT NULL DEFAULT 0.0,
    embedding        VECTOR(1536),                   -- text-embedding-3-small
    is_merged        BOOLEAN DEFAULT FALSE,
    version          INTEGER DEFAULT 1,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- 向量 ANN 索引（HNSW，余弦距离）
CREATE INDEX idx_memory_embedding_hnsw
    ON memory_entries USING hnsw(embedding vector_cosine_ops)
    WITH (m=16, ef_construction=128);

-- 人物+类型+重要性复合索引（高权重兜底路径）
CREATE INDEX idx_memory_person_type_importance
    ON memory_entries(person_id, memory_type, importance_score DESC)
    WHERE is_merged = FALSE;

-- JSONB 全文索引（structured_data 查询）
CREATE INDEX idx_memory_structured_gin
    ON memory_entries USING gin(structured_data);
```

---

## 5. 提取流水线

### 异步解耦流程

```
对话服务 → POST /extract-and-ingest → 入队 Kafka → 立即返回 task_id（< 5ms）
                                              ↓
                              Extraction Worker（50 协程/Pod）
                                    ├─ 长度预过滤（< 10 字跳过）
                                    ├─ 拉取 Person Summary（Redis）
                                    ├─ Claude API 提取（~600ms）
                                    │     └─ 严格 JSON schema 输出
                                    ├─ Embedding 生成（~100ms）
                                    ├─ 去重检查（Redis Lua）
                                    ├─ 写入 Redis（立即可搜索）
                                    └─ 发布 memory.persist → PostgreSQL
```

### Claude API Prompt 设计

```
System:
  你是记忆提取专家。只提取用户信息，不推测，只提取明确表达的内容。
  输出严格 JSON：{"memories": [{memory_type, content, structured_data,
  importance_score, confidence_score, emotional_valence, emotional_intensity}],
  "no_memory_found": bool}

User:
  当前人物摘要：{person_summary}
  最近对话：{last_5_turns}
  待提取消息：[{role}]: {message}
```

### 去重策略

```python
fingerprint = sha256(f"{memory_type}:{normalize(content)}")[:24]
# Redis Lua 原子：SISMEMBER → 已存在则跳过；否则 SADD + 写入
```

### 重试策略

| 级别 | 触发条件 | 处理 |
|------|----------|------|
| L1 | 网络超时 | 立即重试 2 次 |
| L2 | API 限流 (429) | 指数退避，最多 3 次（间隔 2^n 秒） |
| L3 | JSON 解析失败 | 修正 prompt 重试 1 次 |
| L4 | 彻底失败 | 写入 `extraction_tasks(status=failed)` → DLQ（保留 7 天） |

### 本地规则提取（_simple_rule_extract）

用于测试和低延迟场景（不调用 Claude API），覆盖 9 种记忆类型、100+ 触发模式：

| 类型 | 关键触发词/模式示例 |
|------|---------------------|
| identity | `我叫xxx`、`我家住在`、`在xxx小学上学` |
| relationship | `最好的朋友是`、`我很喜欢xxx老师`、`死党`、`担心他/她` |
| behavior | `每天`、`雷打不动`、`养成习惯`、`睡前要`、`饭前必须` |
| personality | `性格外向/内向`、`丢三落四`、`人多的地方`、`相信明天会更好` |
| preference | `最喜欢吃`、`钟情于`、`迷上了`、`吃了三碗`、`宠物叫xxx` |
| aversion | `最讨厌吃`、`受不了xxx的味道`、`敬而远之`、`一到xxx课就头疼` |
| experience | `和xxx一起去`、`上周和`、`约好了还要去`、`帮了xxx` |
| joy | `开心`、`乐开花`、`心情美美`、`太棒了`、`最快乐` |
| pain | `难过`、`害怕`、`担心`、`焦虑`、`恐惧症`、`感觉要失败` |

> **注入覆盖率**（10万条多样化测试）：提取成功率 **100%**，类型准确率 **98.6%**

---

## 6. 召回流水线

### 三路并行搜索

```python
results_a, results_b, results_c = await asyncio.gather(
    # 路径 A：向量语义搜索（pgvector HNSW）
    pg.vector_search(embed(context), person_id, limit=60),

    # 路径 B：高重要性兜底（Redis Sorted Set）
    redis.top_memories_per_type(person_id, top_n=3),  # 每类 3 条，共 27 条

    # 路径 C：关键词精确匹配（Redis 反向索引）
    redis.keyword_search(person_id, extract_keywords(context)),
)
```

### 融合评分公式

```
final_score = 0.5 × semantic_similarity    # pgvector cosine（路径 A）
            + 0.3 × importance_score        # 0.0~1.0
            + 0.2 × recency_factor          # 1 / (1 + days_ago × 0.1)
```

**时序衰减**（仅作用于可变类型）：
- 10 天前：recency ≈ 0.5
- 30 天前：recency ≈ 0.25
- `identity`/`relationship` 不衰减（高重要性天然抗衰）

### 后处理

1. 三路结果合并去重（按 `memory_id`）
2. 按 `final_score` 降序排列
3. 每类最多 3 条（均衡性，防单类霸占）
4. 截取前 15 条

### LLM 注入摘要格式（`format=summary`）

```
## 关于 小明 的记忆

**基本信息**：8岁，光明小学二年级
**性格**：认真负责，做事不罢休
**喜好**：草莓 / 体育课 / 骑自行车 / 宠物仓鼠「球球」
**注意事项**：不喜欢香菜；害怕蟑螂
**令ta开心的事**：考了满分；第一次骑车成功
**好友**：萍萍（最好的朋友，已认识三年）
**近期状态**：上周和萍萍去了公园，很开心
```

---

## 7. 置信度与冲突解决

### P0：置信信号

防止 LLM 误用无关记忆（"幻觉注入"）：

```python
def _compute_confidence(scores: list[float]) -> Literal["high", "uncertain", "low", "empty"]:
    if not scores:          return "empty"
    top = max(scores)
    if top < 0.25:          return "low"       # 绝对阈值：分数太低
    gap = top - mean(scores[1:5] or [0])
    if gap < 0.15:          return "uncertain"  # 相对突出度：不够突出
    return "high"
```

| 置信度 | 含义 | LLM 建议处理 |
|--------|------|------------|
| `high` | 召回结果与查询高度相关 | 直接使用 |
| `uncertain` | 有结果但区分度弱 | 结合对话上下文判断 |
| `low` | 无强相关记忆 | 谨慎使用，主动澄清 |
| `empty` | 无任何召回 | 不要依赖记忆作答 |

**实测结果**：误报 `high` 率 = **5.8%**（目标 ≤ 20%）✓

### P1：记忆冲突解决

当用户改变偏好时，新记忆应压制旧记忆：

**写入时降权**：新记忆写入时检测与旧同类记忆的关键词重叠度，重叠 ≥ 35% 则将旧记忆的 `importance_score × 0.6`。

**召回时衰减**：可变类型的 `score *= 1/(1 + days_ago × 0.01)`，旧记忆得分随时间降低。

**更新信号加分**：内容含"以前/现在/不再/改成/换成"等 17 个信号词时，score × 1.15。

**实测结果**：新偏好胜出率 = **94.8%**（目标 ≥ 55%）✓

---

## 8. 指代消解（EntityBuffer）

会话级命名实体缓冲区，将代词自动替换为具体实体名，提升召回精度。

### 架构

```python
@dataclass
class EntityBuffer:
    max_size: int = 20  # 最近 20 个实体
    _entities: list[dict] = field(default_factory=list)

    def update_from_text(self, text: str, entries: list[MemoryEntry]) -> None:
        """从新写入的记忆文本中提取命名实体，加入缓冲区"""

    def resolve(self, query: str) -> str:
        """将查询中的代词替换为最匹配的实体名"""
```

### 支持的实体类型（25 种，8 大生活域）

| 域 | 实体类型 |
|----|---------|
| 人际关系 | `friend`、`classmate`、`teacher`、`family`、`lover`、`colleague`、`boss`、`doctor` |
| 宠物 | `pet` |
| 娱乐 | `game`、`game_character`、`idol`、`show`、`book`、`sport_team`、`band` |
| 生活 | `place`、`restaurant`、`toy`、`device`、`brand` |
| 学习/工作 | `school`、`company`、`project`、`club` |

### 代词映射示例

| 查询 | 实体类型 | 解析结果 |
|------|---------|---------|
| "那只猫今天吃东西了吗" | pet | "橘子今天吃东西了吗" |
| "她最近在干嘛" | friend/lover | "晓晓最近在干嘛" |
| "那款游戏有新角色吗" | game | "原神有新角色吗" |
| "我们老板最近心情怎么样" | boss | "张总最近心情怎么样" |

**实测结果**：EntityBuffer 召回率 = **95.0%**（vs 原始查询基线 82.1%，提升 +12.8%）✓

---

## 9. API 接口

### `POST /api/v1/memories/extract-and-ingest`

提交对话消息，触发异步提取（fire-and-forget，**P99 < 5ms**）。

```json
// Request
{
  "person_id": "550e8400-e29b-41d4-a716-446655440000",
  "session_id": "550e8400-e29b-41d4-a716-446655440001",
  "message": "我最喜欢吃草莓，草莓是最好吃的水果",
  "role": "user",
  "turn_index": 3,
  "context_turns": [{"role": "assistant", "content": "你喜欢什么水果？"}]
}

// Response
{"task_id": "uuid", "status": "queued"}
```

---

### `POST /api/v1/memories/ingest`

直接写入已结构化记忆（**P99 < 20ms**）。

```json
// Request
{
  "person_id": "uuid",
  "memories": [{
    "session_id": "uuid",
    "memory_type": "preference",
    "content": "小明非常喜欢吃草莓，这是ta最爱的食物",
    "importance_score": 0.8,
    "confidence_score": 0.9,
    "emotional_valence": 0.7,
    "structured_data": {"category": "food", "item": "草莓", "strength": 0.9}
  }]
}

// Response
{"accepted": 1, "deduplicated": 0, "memory_ids": ["uuid"]}
```

---

### `POST /api/v1/memories/recall`

召回最相关记忆（**P99 < 100ms**）。

```json
// Request
{
  "person_id": "uuid",
  "context": "今晚妈妈做饭，帮我决定要点什么",
  "limit": 15,
  "format": "summary",
  "types": ["preference", "aversion"],
  "min_importance": 0.3
}

// Response（summary 格式）
{
  "summary": "## 关于 小明 的记忆\n**喜好**：草莓...",
  "search_latency_ms": 45,
  "confidence": "high",
  "top_score": 0.82,
  "sources": {"vector": 12, "importance": 6, "keyword": 3}
}

// Response（raw 格式）
{
  "memories": [{
    "memory_id": "uuid",
    "memory_type": "preference",
    "content": "小明非常喜欢吃草莓...",
    "importance_score": 0.8,
    "emotional_valence": 0.7,
    "final_score": 0.82
  }],
  "confidence": "high",
  "top_score": 0.82
}
```

---

### `GET /api/v1/persons/{person_id}/profile`

获取人物完整画像（**P99 < 200ms**）。

---

### `GET /api/v1/tasks/{task_id}/status`

查询提取任务状态。

```json
{"task_id": "uuid", "status": "done", "extracted_count": 2, "completed_at": "..."}
```

状态枚举：`pending` → `processing` → `done` / `failed`

---

## 10. CLI 调试工具

`cli.py` 提供交互式本地测试环境（无需真实数据库）。

```bash
python cli.py                     # 随机选一个儿童
python cli.py --name 若曦         # 指定儿童姓名
python cli.py --index 42          # 按序号选择
python cli.py --list              # 列出所有儿童
```

### 交互命令

| 命令 | 功能 |
|------|------|
| `<任意文字>` | 直接查询召回，显示匹配记忆 + 置信度标签 |
| `:profile` | 显示当前儿童完整档案（ground truth） |
| `:memories` | 显示全部存储记忆（按类型分组，含情感值） |
| `:inject <文字>` | 从文字中提取并注入新记忆，显示提取结果 |
| `:entities` | 显示 EntityBuffer 当前缓存的命名实体 |
| `:tier2` | 运行 Tier 2 关键词变体召回测试（自动评分） |
| `:tier3` | 运行 Tier 3 语义迂回召回测试 |
| `:select <名字\|序号>` | 切换儿童，EntityBuffer 自动清空 |
| `:list [n]` | 列出前 n 个儿童 |
| `:help` | 查看帮助 |
| `:quit` | 退出 |

### 置信度显示

```
── 召回结果：小明 × "帮我想想吃什么好"  ● 高置信
  1. [preference    ] imp=0.8 val=+0.7  小明非常喜欢吃草莓...
  2. [aversion      ] imp=0.7 val=-0.6  小明非常不喜欢香菜...

── 召回结果：小明 × "她叫什么来着"  ◐ 不确定
  [代词解析] "她叫什么来着" → "萍萍叫什么来着"
```

---

## 11. 快速启动

```bash
# 1. 启动基础设施
docker-compose up -d          # Redis + PostgreSQL + Kafka

# 2. 安装依赖
pip install -e ".[dev]"

# 3. 配置环境变量
export MEMORY_ANTHROPIC_API_KEY="sk-ant-..."
export MEMORY_PG_DSN="postgresql://memory:memory@localhost:5432/memory"

# 4. 启动 API 服务
uvicorn api.main:app --reload --port 8000

# 5. 启动提取 Worker（另一终端）
python -m pipeline.worker

# 6. 生成测试数据并运行本地模拟（无需基础设施）
python tests/generate_enhanced_data.py   # 生成 5000 儿童档案
python tests/simulate_test.py            # 三层召回准确性测试
python tests/problem_test.py             # P0/P1/P2 修复验证

# 7. 交互式调试
python cli.py --name 小红
```

---

## 12. 配置参数

所有参数通过环境变量注入，前缀 `MEMORY_`（如 `MEMORY_EXTRACTION_MODEL`）。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `EXTRACTION_MODEL` | `claude-sonnet-4-6` | Claude 提取模型 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI 向量模型 |
| `RECALL_VECTOR_WEIGHT` | `0.5` | 向量语义权重 |
| `RECALL_IMPORTANCE_WEIGHT` | `0.3` | 重要性权重 |
| `RECALL_RECENCY_WEIGHT` | `0.2` | 时序新鲜度权重 |
| `RECALL_RECENCY_DECAY` | `0.1` | 每日衰减系数（10天→recency≈0.5） |
| `DEDUP_SIMILARITY_THRESHOLD` | `0.92` | 向量去重余弦阈值 |
| `EXTRACTION_CONFIDENCE_THRESHOLD` | `0.4` | 低置信度丢弃阈值 |
| `EXTRACTION_CONCURRENCY` | `50` | 每 Worker 进程协程并发数 |
| `EXTRACTION_MIN_MESSAGE_LEN` | `10` | 低于此长度跳过提取 |
| `REDIS_RECALL_CACHE_TTL` | `300` | 召回缓存 TTL（秒） |
| `REDIS_MEMORIES_TTL` | `2592000` | 记忆索引 TTL（30天） |
| `PG_POOL_MIN` / `PG_POOL_MAX` | `10` / `50` | asyncpg 连接池大小 |

---

## 13. 测试与评估

### 三层准确性框架（Tier 1/2/3）

| 层级 | 方法 | 当前结果 | 说明 |
|------|------|----------|------|
| **Tier 1**（存储完整性） | 验证事实是否写入记忆库 | **100%**（29 维度） | 只验证写入 |
| **Tier 2**（关键词变体） | 换表达但保留关键词 | **100%**（16 维度） | bigram/trigram 字符重叠 |
| **Tier 3**（语义迂回） | 完全不含原关键词 | **100%**（11 维度） | LSA 字符 n-gram 语义搜索 |

基于 5000 模拟儿童档案，共 140,197 条记忆。运行：

```bash
python tests/simulate_test.py 5000
```

### LSA 语义搜索

```python
# TF-IDF char bigram+trigram → TruncatedSVD (200维) → L2 归一化
# 评分公式
if kw_score > 0:    # Tier 2 路径（保留关键词稳定性）
    score = 0.60 * kw + 0.25 * importance + 0.135 + (sem - 0.05) * 0.15
else:               # Tier 3 路径（纯语义）
    score = 0.15 * importance + 0.045 + max(0, sem - 0.05) * 0.55
```

### 三项核心问题修复验证

运行：`python tests/problem_test.py`

| 问题 | 测试指标 | 实测结果 | 目标 |
|------|----------|----------|------|
| **P0 置信信号** | 误报 high 率 | **5.8%** | ≤ 20% ✓ |
| **P1 记忆冲突** | 新偏好胜出率 | **94.8%** | ≥ 55% ✓ |
| **P2 指代消解** | EntityBuffer 召回率 | **95.0%** | ≥ 70% ✓ |
| 噪声抗性 | Top-3 命中率 | **100%** | ≥ 85% ✓ |
| 长期稳定性 | 长期召回率 | **100%** | ≥ 85% ✓ |

### 注入覆盖率测试（10 万条）

运行：`python tests/generate_inject_data.py && python tests/inject_test.py`

| 指标 | 优化前（6条规则） | 优化后（100+模式） |
|------|-----------|-----------|
| 提取成功率 | 21.6% | **100%** |
| 类型准确率 | 14.1% | **98.6%** |

所有 20 个类别均达到 100% 提取成功率。

### EntityBuffer 实体测试（114 条）

运行：`python tests/entity_buffer_test.py`

| 测试套件 | 结果 |
|----------|------|
| 正向提取（73 条） | **100%** |
| 负向提取（10 条，应无误提取） | **100%** |
| 指代消解（26 条） | **100%** |
| 链式多实体消解 | **100%** |
| 缓冲区溢出 | **100%** |
| 歧义代词（最近优先） | **100%** |

---

## 14. 项目结构

```
memory_system/
├── api/                        # FastAPI 服务层
│   ├── main.py                 # 应用入口、lifespan 管理（Redis/PG/Kafka 初始化）
│   ├── deps.py                 # 依赖注入（get_redis, get_pg, get_producer）
│   └── routers/
│       ├── memories.py         # ingest / extract-and-ingest / recall
│       ├── persons.py          # profile / list memories
│       └── tasks.py            # 提取任务状态查询
│
├── models/
│   ├── enums.py                # MemoryType 枚举（9 类）
│   └── memory.py               # MemoryEntry, PersonProfile, RecallResponse,
│                               # structured_data schemas (IdentityData etc.)
│
├── pipeline/
│   ├── extractor.py            # Claude API 提取（Observer-only + 三层重试）
│   ├── embedding.py            # text-embedding-3-small + LRU 内存缓存
│   ├── dedup.py                # SHA256 指纹 + Redis Lua 原子去重
│   └── worker.py               # Kafka Consumer + asyncio Worker Pool（HPA 就绪）
│
├── retrieval/
│   ├── recall.py               # 三路并行搜索 + 融合评分 + 置信度计算
│   ├── summary.py              # 记忆摘要拼装（LLM system prompt 格式）
│   └── merger.py               # 记忆融合（DBSCAN，每小时批量 + 实时触发）
│
├── storage/
│   ├── redis_store.py          # 热路径：Sorted Set / 反向索引 / Lua 脚本 / 乐观锁
│   ├── pg_store.py             # 冷路径：asyncpg CRUD + pgvector HNSW 搜索
│   └── sync.py                 # Redis → PG 定期同步（背景任务）
│
├── scheduler/
│   └── jobs.py                 # APScheduler：摘要生成(10min) / 融合(1h) / 清理(24h)
│
├── tests/
│   ├── simulate_test.py        # 核心：MemoryStore + EntityBuffer + 三层评估
│   ├── problem_test.py         # P0/P1/P2 修复验证（176K 条数据，8K 场景）
│   ├── check_more_data_test.py # CHECK_MORE_DATA.md 7 维度扩展测试
│   ├── entity_buffer_test.py   # EntityBuffer 全域实体测试（114 条，100%）
│   ├── inject_test.py          # 注入成功率报告（按类型/类别）
│   ├── generate_enhanced_data.py  # 5000 儿童档案生成器
│   ├── generate_inject_data.py    # 10 万条注入覆盖测试数据生成器
│   ├── generate_problem_data.py   # P0/P1/P2 专项测试数据生成器
│   └── data/
│       ├── child_profiles.json      # 5000 儿童档案
│       └── inject_test_data.json    # 10 万条注入测试数据
│
├── cli.py                      # 交互式调试 CLI（EntityBuffer + 置信度显示）
├── config.py                   # Pydantic Settings（所有可配置参数）
├── docker-compose.yml          # 本地开发：Redis + PostgreSQL + Kafka
├── Dockerfile                  # 生产镜像
└── pyproject.toml              # 依赖声明
```

---

*最后更新：2026-03-24*
