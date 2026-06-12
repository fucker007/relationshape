# Person Graph 记忆系统 — 实施计划

## Context

从「扁平 MemoryEntry」升级为「Person Graph + Event + Profile」三层架构。

**核心改进**：人物节点化、事件边化、属性可更新、近期关注追踪、分层摘要。

**用户决策**：
- MemoryEntry 完全替换 → Event + PersonNode 取代
- 因果边：规则+LLM 混合产生
- 情绪干预：对话内策略调整 + 外部 webhook
- 存储：PG 邻接表（不加 Neo4j）

---

## 一、数据模型（3 张新表 + 1 张扩展表）

### 1.1 person_nodes（人物节点，替代 persons 表）

```sql
CREATE TABLE person_nodes (
    person_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,            -- 主用户 ID（对话的主角）
    name            VARCHAR(100) NOT NULL,
    role            VARCHAR(20) NOT NULL,      -- 'primary' | 'secondary'
    -- 属性层（JSONB，可更新覆盖）
    identity        JSONB DEFAULT '{}',        -- {age, birthday, school, grade, gender}
    personality     JSONB DEFAULT '[]',        -- [{trait, evidence, intensity}]
    preferences     JSONB DEFAULT '[]',        -- [{item, category, strength, since_event_id}]
    aversions       JSONB DEFAULT '[]',        -- [{item, category, strength, since_event_id}]
    behaviors       JSONB DEFAULT '[]',        -- [{pattern, frequency, context}]
    -- 近期关注层
    current_focus   JSONB DEFAULT '[]',        -- [{topic, frequency, sentiment, first_seen, last_seen, weight}]
    -- 元信息
    total_events    INTEGER DEFAULT 0,
    last_active_at  TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_person_owner ON person_nodes(owner_id);
CREATE INDEX idx_person_name ON person_nodes(owner_id, name);
```

### 1.2 events（事件，替代 memory_entries）

```sql
CREATE TABLE events (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,             -- 主用户（谁的对话）
    session_id      UUID NOT NULL,
    -- 事件核心
    event_time      TIMESTAMPTZ NOT NULL,      -- 绝对时间（强制）
    event_time_raw  VARCHAR(50),               -- 原始时间表达（"昨天"）
    event_type      VARCHAR(30) NOT NULL,      -- conflict|social|achievement|emotional|change|daily
    summary         TEXT NOT NULL,             -- "和小华在球场打篮球"
    -- 参与者（FK 到 person_nodes）
    participant_ids UUID[] NOT NULL DEFAULT '{}',
    participant_names TEXT[] NOT NULL DEFAULT '{}',  -- 冗余存名字，查询方便
    -- 场景
    scene           VARCHAR(100),
    -- 情绪（每个参与者可能不同）
    emotions        JSONB DEFAULT '{}',        -- {person_id: ["开心"], ...}
    emotion_summary VARCHAR(50),               -- 主情绪
    -- 意义层（复用 Phase 3 MeaningLayer）
    importance      FLOAT NOT NULL DEFAULT 0.5,
    belief_impact   VARCHAR(50),               -- trust_decrease / self_confidence_up / ...
    impact          TEXT[] DEFAULT '{}',        -- ["不想打球", "回避社交"]
    -- 因果边
    caused_by       UUID REFERENCES events(event_id),
    -- 向量
    embedding       VECTOR(1024),              -- BGE-M3
    -- 溯源
    source_message  TEXT DEFAULT '',
    extracted_by    VARCHAR(100) DEFAULT '',
    -- 生命周期
    is_deleted      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_event_owner_time ON events(owner_id, event_time DESC) WHERE NOT is_deleted;
CREATE INDEX idx_event_participants ON events USING GIN(participant_ids) WHERE NOT is_deleted;
CREATE INDEX idx_event_type ON events(owner_id, event_type) WHERE NOT is_deleted;
CREATE INDEX idx_event_embedding ON events USING hnsw(embedding vector_cosine_ops) WITH (m=16, ef_construction=128);
```

### 1.3 relationships（关系状态，由事件自动驱动更新）

```sql
CREATE TABLE relationships (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,
    from_person_id  UUID NOT NULL REFERENCES person_nodes(person_id),
    to_person_id    UUID NOT NULL REFERENCES person_nodes(person_id),
    relation_type   VARCHAR(30),               -- friend|family|classmate|teacher|...
    sentiment       FLOAT DEFAULT 0.0,         -- -1.0 ~ +1.0
    intensity       FLOAT DEFAULT 0.0,         -- 0.0 ~ 1.0 (互动频率)
    last_event_id   UUID REFERENCES events(event_id),
    last_event_time TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(from_person_id, to_person_id)
);
CREATE INDEX idx_rel_owner ON relationships(owner_id);
```

### 1.4 daily_emotions（日级情绪聚合）

```sql
CREATE TABLE daily_emotions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,
    person_id       UUID NOT NULL REFERENCES person_nodes(person_id),
    date            DATE NOT NULL,
    emotion_distribution JSONB NOT NULL,        -- {"sad": 0.6, "angry": 0.3, "neutral": 0.1}
    dominant_emotion VARCHAR(30),
    event_count     INTEGER DEFAULT 0,
    UNIQUE(person_id, date)
);
```

---

## 二、模块复用映射

### 直接复用（不改代码）

| 模块 | 文件 | 新角色 |
|------|------|--------|
| LLMClient | `llm/client.py` | 通用 LLM 调用 |
| MergeJudge | `llm/skills/merge_judge.py` | 事件合并决策 |
| ExtractionGate | `llm/skills/extraction_gate.py` | "要不要提取"门控 |
| UsageGate | `llm/skills/usage_gate.py` | 召回后筛选 |
| ForgetJudge | `llm/skills/forget_judge.py` | 事件衰减判断 |
| BGE-M3 Embedding | `pipeline/embedding.py` | 事件 embedding |
| MergeWorker | `pipeline/merge_worker.py` | 异步合并队列 |
| 衰减+硬保护 | `scheduler/jobs.py` | 事件遗忘 |
| EntityBuffer | `tests/simulate_test.py` 中的 EntityBuffer | 代词→实体消解 |

### 改造升级（核心逻辑保留，扩展接口）

| 模块 | 文件 | 改什么 |
|------|------|--------|
| 时间标准化 | `pipeline/event_extractor.py` `_normalize_time()` | 拒绝"最近"兜底，强制绝对时间或标记 approximate |
| 多人提取 | `pipeline/event_extractor.py` `_extract_participants()` | 返回 person_id（关联 person_nodes），不只返回名字 |
| 地点提取 | `pipeline/event_extractor.py` `_extract_place()` | 直接复用 |
| 情绪规则 | `pipeline/meaning_extractor.py` 132 条规则 | 直接复用，输出写入 Event.emotions |
| 否定检测 | `pipeline/meaning_extractor.py` `_has_negation()` | 直接复用 |
| 信念影响 | `pipeline/meaning_extractor.py` `_BELIEF_RULES` | 输出写入 Event.belief_impact |
| 重要性修正 | `pipeline/meaning_extractor.py` `_IMPORTANCE_MODIFIERS` | 输出写入 Event.importance |
| 偏好变化 | `pipeline/meaning_extractor.py` `extract_preference_change()` | 输出驱动 PersonNode.preferences 更新 |
| EventModel | `models/event.py` | 扩展为新 Event schema（加 event_type/scene/impact/embedding/caused_by） |
| MeaningLayer | `models/meaning.py` | 保留，每个 Event 附带 |
| PreferenceTimeline | `models/meaning.py` | 改为 Profile 属性更新机制 |
| 三路召回 | `retrieval/recall.py` | 加时间范围、人物过滤、事件类型过滤维度 |
| IntentClassifier | `retrieval/decider.py` | 加 "event_query"/"person_query" 意图 |
| PgStore | `storage/pg_store.py` | 加新表 CRUD，现有方法保留 |
| RedisStore | `storage/redis_store.py` | 加事件缓存/关注度缓存 key pattern |

### 新增模块

| 模块 | 文件 | 功能 |
|------|------|------|
| PersonNode 模型 | `models/person_graph.py` | PersonNode / Relationship / DailyEmotion 数据模型 |
| Event 新模型 | `models/event.py`（重写） | 完整 Event schema |
| Profile 更新器 | `pipeline/profile_updater.py` | Event → PersonNode 属性自动更新 |
| 关注度追踪 | `pipeline/focus_tracker.py` | 对话主题提取 → current_focus |
| 关系提取器 | `pipeline/relation_extractor.py` | 从事件提取/更新人物关系 |
| 多维检索 | `retrieval/graph_recall.py` | 按人物+时间+事件类型检索 |
| 摘要生成 | `scheduler/summary_jobs.py` | 事件→阶段摘要→关系状态 定时聚合 |
| 情绪干预 | `scheduler/emotion_alert.py` | 连续负面→对话策略+webhook |
| Graph PgStore | `storage/pg_store.py`（扩展） | 新表的 CRUD 方法 |

---

## 三、处理流水线

### 3.1 消息写入流程

```
用户说话
  ↓
[ExtractionGate] 要不要提取？（复用 llm/skills/extraction_gate.py）
  ↓ YES
[EntityBuffer] 代词消解："他"→"小华"（复用）
  ↓
[LLM 提取] 提取结构化信息（一次调用，同时返回事件+属性）
  ↓
  ├── 是事件？（有时间+动作+人物，至少2项）
  │   ↓
  │   [event_extractor] 时间标准化→绝对时间（复用 _normalize_time）
  │   [event_extractor] 参与者提取（复用 _extract_participants）→ 关联 person_nodes
  │   [event_extractor] 地点提取（复用 _extract_place）
  │   [meaning_extractor] 情绪提取（复用 132 条规则）
  │   [meaning_extractor] 重要性修正（复用 _IMPORTANCE_MODIFIERS）
  │   [meaning_extractor] 信念影响（复用 _BELIEF_RULES）
  │   [embedding] BGE-M3 编码（复用）
  │   ↓
  │   [MergeJudge] 事件合并决策（复用 llm/skills/merge_judge.py）
  │   ↓
  │   写入 events 表（PG） + Redis 缓存
  │   ↓
  │   [profile_updater] Event 驱动 Profile 更新：
  │     ├── 偏好变化 → 覆盖 PersonNode.preferences（复用 extract_preference_change）
  │     ├── 关系变化 → 更新 relationships.sentiment
  │     └── 情绪聚合 → 更新 daily_emotions
  │
  └── 不是事件？是属性声明？（"我叫小明" / "我喜欢吃辣"）
      ↓
      直接更新 PersonNode 对应字段（identity/preferences/...）
      ↓
      [focus_tracker] 更新 current_focus（从本轮对话提取主题）
```

### 3.2 消息召回流程

```
用户说话
  ↓
[IntentClassifier] 意图分类（复用+扩展）
  ├── identity_query   → PersonNode.identity 直查
  ├── preference_query → PersonNode.preferences 直查
  ├── person_query     → 查 PersonNode + 关系 + 相关事件
  ├── event_query      → events 表多维检索
  └── general          → 分层摘要注入
  ↓
日常对话 → Layer 1: PersonNode 属性 + current_focus + 关系状态（~200 tokens，Redis 缓存）
提到具体人/事 → Layer 3: events 表精确查询（PG 索引，<5ms）
  ↓
[UsageGate] LLM 筛选（复用）
  ↓
注入 LLM 上下文
```

### 3.3 LLM 上下文注入格式（Layer 1）

```
## 关于小明

**基本信息**：10岁，阳光小学四年级，生日1月15号
**性格**：有自信心，目标感强
**喜好**：吃辣、打篮球
**近期关注**：考试压力（焦虑，持续3天）← current_focus

**社交圈**：
  小华（好朋友 ↑0.8）最近：昨天一起打篮球 | 她喜欢：画画
  妈妈（家人 ↑0.9）最近：前天一起看医生
  王老师（班主任 →0.5）最近：上周被批评作业
```

---

## 四、实施阶段（10 步）

### Phase A: 数据模型 + PG 表
1. `models/person_graph.py` — PersonNode / Relationship / DailyEmotion Pydantic 模型
2. `models/event.py` — 重写 Event 模型（扩展 event_type/scene/impact/caused_by/embedding）
3. `storage/pg_store.py` — 新增 4 张表 DDL + CRUD 方法

### Phase B: 提取流水线
4. `pipeline/event_extractor.py` — 扩展：强制绝对时间 + participant_ids 关联 + scene 提取
5. `pipeline/profile_updater.py` — 新增：Event → PersonNode 属性更新
6. `pipeline/relation_extractor.py` — 新增：事件 → 关系提取/更新
7. `pipeline/focus_tracker.py` — 新增：对话主题 → current_focus

### Phase C: 检索
8. `retrieval/graph_recall.py` — 新增：多维检索（时间+人物+事件类型+向量）
9. `retrieval/recall.py` — 扩展：集成 graph_recall，保留评分/去重逻辑

### Phase D: 调度 + 干预
10. `scheduler/summary_jobs.py` — 阶段摘要聚合
11. `scheduler/emotion_alert.py` — 连续负面干预触发

### Phase E: chat.py 集成
12. `chat.py` — 接入新流水线，替换 MemoryEntry 逻辑

---

## 五、关键复用清单（带文件路径和行号）

| 已测功能 | 源文件 | 核心函数 | 测试结果 | 新系统用法 |
|---------|--------|---------|---------|-----------|
| 时间标准化 | `pipeline/event_extractor.py:63` | `_normalize_time()` | 98.8% | Event.event_time 生成 |
| 多人识别 | `pipeline/event_extractor.py:212` | `_extract_participants()` | 100% | Event.participant_ids |
| 地点提取 | `pipeline/event_extractor.py:176` | `_extract_place()` | — | Event.scene |
| 情绪规则 | `pipeline/meaning_extractor.py:56` | 132 条 `_EMOTION_RULES` | 88.4% | Event.emotions |
| 否定检测 | `pipeline/meaning_extractor.py:33` | `_has_negation()` | 88.4% | 情绪提取前处理 |
| 信念规则 | `pipeline/meaning_extractor.py:139` | `_BELIEF_RULES` | — | Event.belief_impact |
| 重要性修正 | `pipeline/meaning_extractor.py:177` | `_IMPORTANCE_MODIFIERS` | — | Event.importance |
| 偏好变化 | `pipeline/meaning_extractor.py:344` | `extract_preference_change()` | 93.8% | Profile.preferences 更新 |
| MergeJudge | `llm/skills/merge_judge.py` | `MergeJudge.decide()` | 85% | 事件合并 |
| 代词消解 | `tests/simulate_test.py` EntityBuffer | `resolve()` | 87.6% | 事件提取前预处理 |
| 衰减公式 | `scheduler/jobs.py` | `compute_forget_score()` | 100% | 事件遗忘 |
| BGE-M3 | `pipeline/embedding.py` | `embed_text/embed_batch` | 2771条/s | Event.embedding |
| 三路召回 | `retrieval/recall.py:128` | `recall()` | — | 扩展后继续用 |
| 意图分类 | `retrieval/decider.py:62` | `IntentClassifier` | 100% | 扩展后继续用 |

---

## 六、验证方案

### 单元测试
- PersonNode CRUD：创建/更新/查询 person_nodes
- Event 写入+查询：写入事件，按时间/人物/类型检索
- Profile 更新：Event 触发 preference/relationship 更新
- Focus 追踪：多轮对话后 current_focus 正确聚合

### 集成测试
```bash
# 完整对话流
python chat.py --user 测试用户 --pg-port 5433
> 我叫小明，今年10岁          # → PersonNode.identity 更新
> 我喜欢吃辣                   # → PersonNode.preferences 更新
> 今天和小华在球场打篮球        # → Event 写入 + 小华 PersonNode 创建 + Relationship 创建
> 小华说她喜欢画画             # → 小华.preferences 更新
> 打球的时候和小华吵架了        # → Event(conflict) + Relationship.sentiment 下降
> :memories                    # → 验证 PersonNode 属性 + Events + Relationships
```

### 性能测试
- 单用户 10K 事件后：召回 <100ms
- 关系查询：<5ms
- Profile 读取：<1ms（Redis 缓存）
