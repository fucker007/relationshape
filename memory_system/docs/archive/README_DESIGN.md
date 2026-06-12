# Memory System — LLM Skill 架构设计文档

**版本**: v1.1
**日期**: 2026-03-25
**修订**: 修复 spec review 发现的 C1/C2/M9 等 16 处问题
**阶段**: Phase 4 — LLM 写入合并 + Memory Gate + 遗忘机制

---

## 目录

1. [背景与问题](#1-背景与问题)
2. [整体架构](#2-整体架构)
3. [LLM 统一接口](#3-llm-统一接口)
4. [四个 Skill 模块](#4-四个-skill-模块)
5. [写入侧：异步 Merge Worker](#5-写入侧异步-merge-worker)
6. [遗忘机制](#6-遗忘机制)
7. [召回侧改造](#7-召回侧改造)
8. [容量与存储规划](#8-容量与存储规划)
9. [测试方案](#9-测试方案)
10. [文件结构变更](#10-文件结构变更)
11. [配置项汇总](#11-配置项汇总)

---

## 1. 背景与问题

### 1.1 现有系统（Phase 0–3）能力

- Phase 0：基础记忆存取（写入/召回），100% 写入成功率
- Phase 1：规则打分决策层（IntentClassifier + TypeGate），Precision 100%
- Phase 2：事件中心建模（who/when/where/what），完整率 100%
- Phase 3：意义层（情绪、信念、偏好变化），准确率 88.4%

### 1.2 核心缺陷：记忆爆炸

现有系统是**只增不减**的：

```
"喜欢草莓" 说了 100 次 → 存了 100 条语义重复的记忆（措辞不同导致指纹不同）
"今天很开心" 说了 50 次  → 50 条 JOY 记忆堆积
```

根本原因：
1. **写入侧**：只有 SHA256 精确指纹去重，相似但不完全相同的内容无限叠加
2. **没有遗忘**：PostgreSQL 无 TTL 强制执行，记忆永久堆积
3. **召回侧**：Phase 1 规则打分能缩窄候选，但无法做语义级精判（词不达意仍会注入）

### 1.3 解决方案：方案 B — 全 LLM Skill 架构

核心思路来自 mem0 的**写入时合并**策略：

> 每次写入前，查询相似记忆 → LLM 判断 ADD / UPDATE / DELETE / NONE → 同类信息只保留一条最新最完整的

本设计在此基础上增加：
- **统一 LLM 接口层**：OpenAI 风格，底层调 Claude
- **四个职责单一的 Skill 模块**：提取门控、使用门控、合并判断、遗忘判断
- **异步有界 Merge Worker**：不阻塞用户响应，防止资源堆积
- **双轨遗忘机制**：时间衰减 + 容量上限联合触发

---

## 2. 整体架构

```
用户发消息
    │
    ▼
┌─────────────────────────────────────────┐
│              召回侧（同步）               │
│                                         │
│  三路并行搜索（向量/类型/关键词）          │
│       ↓                                 │
│  融合评分 → Top 15                       │
│       ↓                                 │
│  Phase 1 TypeGate + 规则打分 → Top 5    │
│       ↓                                 │
│  UsageGate Skill（LLM，Haiku）           │
│       ↓                                 │
│  注入 System Prompt → LLM 生成回答       │
└─────────────────────────────────────────┘
    │
    ▼（fire-and-forget，异步）
┌─────────────────────────────────────────┐
│              写入侧（异步）               │
│                                         │
│  ExtractionGate Skill（LLM，Haiku）      │
│       ↓ should_extract=True             │
│  Claude 提取记忆（现有逻辑）              │
│       ↓                                 │
│  MergeTaskQueue（有界队列，上限 500）     │
│       ↓（后台 MergeWorker，信号量 20）   │
│  向量查询相似记忆 Top 5                   │
│       ↓                                 │
│  MergeJudge Skill（LLM，Sonnet）         │
│       ↓                                 │
│  执行 ADD / UPDATE / DELETE / NONE      │
│       ↓                                 │
│  释放信号量 + 失效召回缓存                │
└─────────────────────────────────────────┘
    │
    ▼（定时任务，每天 03:00）
┌─────────────────────────────────────────┐
│              遗忘侧（定时）               │
│                                         │
│  衰减扫描：forget_score < 0.05           │
│       +                                 │
│  容量检查：每人每类型超出上限              │
│       ↓（合并候选列表）                   │
│  硬保护过滤（身份/高重要性）               │
│       ↓                                 │
│  ForgetJudge Skill（LLM，Haiku）         │
│       ↓                                 │
│  软删除 → 1小时后物理删除                 │
└─────────────────────────────────────────┘
```

---

## 3. LLM 统一接口

### 3.1 设计目标

- 对外暴露 **OpenAI 风格接口**（`messages=[...]` 列表，`model` 参数，结构化返回）
- 底层调用 `anthropic.AsyncAnthropic`，调用方无需感知
- 统一处理重试、日志、JSON 强制解析

### 3.2 接口定义

```python
# llm/client.py

class LLMMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str

@dataclass
class LLMResponse:
    content: str           # 纯文本回答
    model: str             # 实际使用的模型
    input_tokens: int
    output_tokens: int

class LLMClient:
    async def chat(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,       # 默认 settings.llm_model
        temperature: float = 0.0,       # 判断类任务默认 0（确定性）
        max_tokens: int = 512,
        response_format: Literal["text", "json"] = "text",
    ) -> LLMResponse: ...

    async def chat_json(
        self,
        messages: list[LLMMessage],
        **kwargs,
    ) -> dict: ...                      # 直接返回 dict，自动解析 JSON
```

### 3.3 实现要点

| 特性 | 实现方式 |
|---|---|
| 底层调用 | `anthropic.AsyncAnthropic` |
| 重试策略 | `tenacity`：429 / 连接错误，指数退避，最多 3 次，最长等待 30s |
| JSON 强制 | `response_format="json"` 时，system prompt 末尾追加强制 JSON 指令 |
| 日志 | 每次调用记录 model / tokens / latency，便于成本追踪 |
| 单例 | `get_llm_client()` 返回全局单例，避免重复建连 |

### 3.4 现有代码迁移

`pipeline/extractor.py` 中的 `anthropic.AsyncAnthropic` 直接调用，后续统一替换为 `LLMClient`，接口行为不变。

---

## 4. 四个 Skill 模块

每个 Skill 是独立模块：**固定 prompt 模板 + 明确输入/输出接口 + 通过 LLMClient 调用**。

### 4.1 Skill 1 — ExtractionGate（提取门控）

**调用时机**：消息进入 Kafka 队列后、Claude 提取前
**使用模型**：`settings.llm_fast_model`（即 `claude-haiku-4-5-20251001`，速度优先）
**作用**：过滤无记忆价值的消息，节省下游 API 成本

```python
# llm/skills/extraction_gate.py

@dataclass
class ExtractionGateInput:
    message: str              # 用户消息原文
    role: str                 # user / assistant
    recent_context: str = "" # 最近 3 轮对话摘要（可选）

@dataclass
class ExtractionGateOutput:
    should_extract: bool
    reason: str               # 简短理由（用于日志）
    confidence: float         # 0.0–1.0
```

**ExtractionGate 准确率目标**：通过率（recall）≥ 90%（不漏掉有效记忆），误触发率（false positive）≤ 30%（允许部分无效消息通过，由下游过滤）。

**Prompt 判断标准**：

| 内容类型 | 决策 |
|---|---|
| 包含个人事实（身份/喜好/经历/情绪） | should_extract = True |
| 纯闲聊、礼貌用语（"好的"、"嗯嗯"）| should_extract = False |
| 纯问句、无陈述内容 | should_extract = False |
| 指令操作（"帮我查一下..."）| should_extract = False |

---

### 4.2 Skill 2 — UsageGate（使用门控）⭐ 最核心

**调用时机**：Phase 1 规则打分输出 Top 5 后、注入回答上下文前
**使用模型**：`settings.llm_fast_model`（即 `claude-haiku-4-5-20251001`，延迟敏感，在用户等待响应的路径上）
**作用**：语义精判，过滤掉"词不达意"的候选记忆

```python
# llm/skills/usage_gate.py

@dataclass
class UsageGateInput:
    query: str                        # 用户当前说的话
    recalled_memories: list[dict]     # Top 5 候选，含 content/type/importance/score

@dataclass
class UsageGateOutput:
    selected: list[str]      # 选中的 memory_id 列表，最多 3 条（超出则取前 3）
    reason: str              # 选择 / 拒绝的理由（用于调试）
```

> **注**：`selected` 硬性上限为 3 条——prompt 中明确指示 LLM 最多选 3 条，`recall_with_gate()` 中也做截断兜底：`gate_result.selected[:3]`。

**Prompt 三条判断标准**（用户确认）：

1. **确实是用户提到过的事物**，不是泛泛相关
2. **与当前问句高度匹配**，能让回答更准确、更贴近用户
3. **不词不达意**——语义相关但用上去会显得牵强的，也不选

**与 Phase 1 的分工**：

| 组件 | 职责 | 边界 |
|---|---|---|
| Phase 1 IntentClassifier | 识别查询意图 | 规则+关键词，无语义理解 |
| Phase 1 TypeGate | 硬过滤不相关类型 | 结构化约束，速度快 |
| Phase 1 规则打分 | char bigram 相似度排序 | 词汇重叠，无法判断语义等价 |
| **UsageGate（新增）** | 语义精判：用这条记忆合适吗 | LLM 理解，但有延迟 |

两者**串联**，不替代：规则层先过滤大多数无关记忆，LLM 只判断剩余 Top 5。

---

### 4.3 Skill 3 — MergeJudge（写入合并判断）

**调用时机**：异步写入前，向量查询到相似记忆后
**使用模型**：`settings.llm_model`（即 `claude-sonnet-4-6`，准确率优先，在异步后台路径上）
**作用**：决定新记忆如何处理，防止同类内容无限叠加

```python
# llm/skills/merge_judge.py

@dataclass
class MergeJudgeInput:
    new_memory: dict              # 刚提取的记忆 {type, content, importance}
    similar_existing: list[dict]  # 向量相似记忆，最多 5 条

@dataclass
class MergeJudgeOutput:
    action: Literal["ADD", "UPDATE", "DELETE", "NONE"]
    target_id: str | None         # UPDATE / DELETE 时的目标 memory_id
    merged_content: str | None    # UPDATE 时的合并后内容
    reason: str
```

**四种操作定义**：

| 操作 | 触发条件 | 示例 |
|---|---|---|
| **ADD** | 全新信息，无相似记忆 | "喜欢打篮球"，库中无运动偏好记录 |
| **UPDATE** | 找到相似记忆，新信息更完整/更新 | 旧"喜欢吃辣" → 新"喜欢川菜，特别爱麻辣" |
| **DELETE** | 新信息与旧信息矛盾 | 旧"喜欢吃辣" → 新"最近不想吃辣了" |
| **NONE** | 完全重复，已知信息 | 旧"8岁" → 新"我今年8岁" |

---

### 4.4 Skill 4 — ForgetJudge（遗忘决策）

**调用时机**：定时遗忘任务，候选删除列表确认前
**使用模型**：`settings.llm_fast_model`（即 `claude-haiku-4-5-20251001`，批量处理，成本敏感）
**作用**：在衰减/容量机制筛选出的候选中，最终确认哪些值得删除

```python
# llm/skills/forget_judge.py

@dataclass
class ForgetJudgeInput:
    candidates: list[dict]   # 候选删除记忆，含 content/importance/days_old/forget_score
                             # 每批最多 50 条（由调用方负责分批）

@dataclass
class ForgetJudgeOutput:
    delete_ids: list[str]    # 确认删除
    keep_ids: list[str]      # 保留（LLM 认为仍有价值）
    reason: str
```

**批量处理与并发**：

- 每批最多 50 条，由 `forget_scan_job()` 负责将候选列表切分后逐批提交
- 批次间并发上限：`forget_judge_concurrency: int = 5`（防止单次遗忘任务产生过多并发 LLM 调用）
- 单批失败：记录日志，该批候选保留，下次定时任务重试

**判断标准**（`forget_score` 阈值统一为 `< 0.05`，与扫描阶段一致）：

- `forget_score < 0.05` 且内容普通 → 删除
- 虽然分数低，但内容是关键身份信息（姓名/年龄/关键经历）→ 保留（硬保护已在上游过滤，此处为二次兜底）
- 与其他高分记忆高度重复 → 删除

---

## 5. 写入侧：异步 Merge Worker

### 5.1 MergeTask 数据结构

```python
# pipeline/merge_worker.py

@dataclass
class MergeTask:
    person_id: str
    memory_entry: dict        # 提取出的完整记忆 {type, content, importance, confidence, ...}
    session_id: str
    turn_index: int
    created_at: datetime = field(default_factory=datetime.utcnow)
    priority: int = 0         # 保留字段，当前不使用，默认 0
```

### 5.2 完整写入流程

```
Kafka 消息到达
    ↓
ExtractionGate.check()          ← should_extract ?
    ↓ True
Claude 提取记忆（现有逻辑不变）
    ↓
MergeTaskQueue.enqueue()        ← 丢进有界队列，立即返回（< 1ms）
    ↓（后台异步）
MergeWorker（有界信号量）
    ├─ 向量查询相似记忆 Top 5
    ├─ similarity < threshold → 直接 ADD，跳过 LLM
    ├─ similarity >= threshold → MergeJudge.decide()
    ├─ 执行写入 / 更新 / 删除
    ├─ 更新关键词索引
    ├─ 失效召回缓存
    └─ 释放信号量 slot（async with 自动保证）
```

### 5.3 并发控制参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `merge_concurrency` | 20 | 同时进行的 LLM 合并判断数上限 |
| `merge_queue_maxsize` | 500 | 任务队列上限，满了按策略降级 |
| `merge_similar_limit` | 5 | 查询相似记忆的数量 |
| `merge_similarity_threshold` | 0.82 | 低于此相似度直接 ADD，不送 LLM |

MergeWorker 初始化：`self._semaphore = asyncio.Semaphore(settings.merge_concurrency)`

### 5.4 队列满时降级策略

```
队列未满（< 500）：正常入队 → 异步合并
队列已满（= 500）：
    ├─ 新记忆（ADD）：跳过合并，直接写入（宁可重复也不丢失）
    └─ 疑似重复：完全丢弃
    └─ 记录告警日志
```

### 5.5 资源释放保证

```python
async def _process(self, task: MergeTask):
    async with self._semaphore:      # ← 自动获取，异常也能自动释放
        try:
            await self._do_merge(task)
        finally:
            self._queue.task_done()  # ← 标记完成，释放队列槽
    # 局部变量（similar_memories, llm_response）离开作用域自动 GC
    # 不持有任何任务级别的长引用
```

- 全程 `asyncio` 协程，**不使用线程池**（避免线程泄漏）
- `async with semaphore` 确保即使 LLM 调用抛异常，slot 也一定归还

---

## 6. 遗忘机制

### 6.1 衰减公式

```
forget_score = importance × e^(-λ × days)

其中：
  importance = 写入时的重要性评分（0.0–1.0）
  days       = 距该记忆「上次被访问（召回使用）」的天数；
               若从未被访问过，则使用「距创建时间」的天数。
               每次召回命中时，更新 last_accessed_at 字段（重置衰减时钟）。
  λ          = 衰减速率（按类型配置）
```

**各类型衰减速率 λ**（以下存活天数均假设 `importance = 1.0`）：

| 类型 | λ | 存活至 forget_score < 0.05 |
|---|---|---|
| `identity` | 0.001 | ~2900 天（~8年） |
| `personality` | 0.002 | ~1450 天（~4年） |
| `behavior` | 0.005 | ~580 天（~1.5年） |
| `experience` | 0.008 | ~360 天（~1年） |
| `preference` / `aversion` | 0.010 | ~290 天（~9.5个月） |
| `joy` / `pain` | 0.015 | ~195 天（~6.5个月） |

**示例**（importance=0.6, λ=0.010）：

| 天数 | forget_score |
|---|---|
| 0 | 0.600 |
| 30 | 0.444 |
| 100 | 0.221 |
| 200 | 0.082 |
| 290 | 0.050 → 进入候选 |

### 6.2 双轨触发逻辑

```
定时任务（每天 03:00 低峰期）
    │
    ├─ 轨道 1：衰减扫描
    │     ├─ 计算所有记忆的 forget_score
    │     └─ forget_score < 0.05 → 候选删除列表
    │
    └─ 轨道 2：容量检查
          ├─ 统计每人每类型条数
          └─ 超出上限 → 取最低 forget_score 的超量部分 → 候选删除列表

两轨合并去重 → 硬保护过滤 → ForgetJudge → 执行删除
```

### 6.3 每人每类型容量上限

```python
MEMORY_QUOTA: dict[str, int] = {
    "identity":    20_000,
    "personality": 15_000,
    "behavior":    30_000,
    "preference":  50_000,
    "aversion":    50_000,
    "experience":  150_000,
    "joy":         80_000,
    "pain":        80_000,
}
# 单人最大总量：~475,000 条（极限情况，正常使用远低于此）
```

### 6.4 硬保护规则（在 ForgetJudge 之前执行）

在候选列表进入 LLM 判断前，先强制过滤：

- `identity` 类中含"姓名/年龄/性别"关键字 → **强制保留**
- `importance >= 0.8` → **强制保留**
- 剩余候选才送给 ForgetJudge

### 6.5 删除执行策略

```python
async def execute_forget(memory_ids: list[str], _task_registry: set):
    await pg_store.soft_delete(memory_ids)                    # 立即标记 is_deleted=True
    await redis_store.remove_from_sorted_sets(memory_ids)     # 从召回索引移除（立即生效）

    # C1 修复：注册 Task 到 registry，防止 GC 提前回收导致静默丢失
    task = asyncio.create_task(
        pg_store.hard_delete_later(memory_ids, delay=settings.forget_hard_delete_delay)
    )
    _task_registry.add(task)
    task.add_done_callback(_task_registry.discard)  # 完成后自动清除引用
```

> `_task_registry` 是 `forget_scan_job()` 持有的 `set[asyncio.Task]`，`add_done_callback` 确保 Task 完成后自动从注册表移除，无内存泄漏。`storage/pg_store.py` 需新增 `hard_delete_later(memory_ids, delay)` 方法（见 Section 10 修改文件清单）。

---

## 7. 召回侧改造

### 7.1 完整召回流程

```
用户发消息
    ↓
【三路并行搜索】（现有，不变）
    ├─ 向量搜索 → Top 20
    ├─ 类型+重要性（Redis Sorted Set）→ Top 24
    └─ 关键词索引 → 匹配集合
    ↓
【融合评分 + 去重】（现有，不变）→ Top 15
    ↓
【Phase 1 TypeGate + 规则打分】（现有，不变）→ Top 5
    ↓
【UsageGate LLM 判断】（新增）
    ├─ 输入：用户消息 + Top 5 候选
    ├─ 判断：哪些确实相关
    └─ 输出：selected_ids（0–3 条）
    ↓
selected 非空 → 注入 System Prompt → 生成回答
selected 为空 → 直接回答（不注入任何记忆）
```

### 7.2 延迟分析

| 阶段 | 延迟 |
|---|---|
| 三路搜索 + 融合 | ~30ms |
| Phase 1 规则打分 | ~2ms |
| **UsageGate LLM 调用（Haiku）** | ~150–200ms |
| LLM 生成回答（Sonnet） | ~800ms |
| **总计** | **~1,000–1,030ms** |

> UsageGate 使用 **`claude-haiku`** 而非 `claude-sonnet`，将判断延迟控制在 200ms 以内。

### 7.3 注入格式

UsageGate 选中后，以结构化摘要形式注入 System Prompt：

```
## 关于用户的相关记忆

- [偏好] 用户之前喜欢吃辣，最近表示不太想吃了（重要性: 0.75，3天前）
- [身份] 用户是小学生，8岁
```

注入规则：
- 最多注入 **3 条**
- 每条不超过 **80 字**（截取 summary 字段）
- 格式统一：`[类型] 内容（重要性, 时间）`

### 7.4 GateResult 定义

```python
@dataclass
class GateResult:
    selected: list[MemoryEntry]   # UsageGate 选中的记忆（最多 3 条）
    used_gate: bool               # 是否真正调用了 UsageGate（False 表示降级/跳过）
    reason: str = ""              # UsageGate 返回的理由（调试用）
    confidence: str = ""          # 上游 recall_with_confidence() 返回的置信度
```

### 7.5 代码改动范围

现有 `retrieval/recall.py` 新增一个函数，原有函数**不修改**：

```python
async def recall_with_gate(
    query: str,
    person_id: str,
    *,
    use_usage_gate: bool = True,
) -> tuple[list[MemoryEntry], GateResult]:
    entries, confidence = await recall_with_confidence(query, person_id)

    # I5 修复：置信度为 empty/low 时，跳过 UsageGate 直接返回空
    if confidence in ("empty", "low") or not entries:
        return [], GateResult(selected=[], used_gate=False, confidence=confidence)

    if not use_usage_gate:
        return entries, GateResult(selected=entries, used_gate=False, confidence=confidence)

    gate_result = await usage_gate.decide(query=query, candidates=entries[:5])
    gate_result.selected = gate_result.selected[:3]   # 截断兜底，最多 3 条
    gate_result.confidence = confidence
    return gate_result.selected, gate_result
```

---

## 8. 容量与存储规划

### 8.1 单条记忆存储大小

| 组成 | 大小 |
|---|---|
| 内容 + 结构化数据 + 元数据 | ~700 bytes |
| Embedding 向量（1536维 × 4字节） | ~6,144 bytes |
| PostgreSQL 行开销 | ~100 bytes |
| HNSW 向量索引 | ~9,300 bytes |
| **合计** | **≈ 17 KB / 条** |

### 8.2 每用户每天净增量（玩 1 小时）

```
60 条用户消息
× 40% 通过 ExtractionGate          = 24 条进入提取
× 每条平均提取 2 条记忆             = 48 条原始记忆
× 50% MergeJudge 判断为 ADD        = 24 条写入
- 衰减遗忘日均清除                  ≈  4 条
─────────────────────────────────────────
净增约 20 条/天/人
```

### 8.3 稳态分析

偏好类 λ=0.010 时，记忆平均存活 ≈ **8 个月（249天）**。

用户活跃超过 8 个月后进入稳态：

```
稳态存量  = 20 条/天 × 249 天 = ~5,000 条/人
稳态占用  = 5,000 × 17KB    = ~85 MB/人
```

### 8.4 500GB 容量规划

| 阶段 | 每用户占用 | 支持用户数 |
|---|---|---|
| 成长期（< 8 个月） | 增长中，峰值 ~85MB | ~6,000 人 |
| 稳态（> 8 个月） | ~85 MB | **~6,000 人** |

Redis 热缓存（30天TTL）：~0.6KB/条 × 5,000 条 = **~3MB/人**
→ 6,000 人约 **18GB RAM**，需纳入内存规划。

### 8.5 扩展参考

| 磁盘 | 稳态用户数 |
|---|---|
| 500 GB | ~6,000 人 |
| 2 TB | ~24,000 人 |
| 10 TB | ~120,000 人 |

---

## 9. 测试方案

### 9.1 测试文件结构

```
tests/
├── unit/
│   ├── test_llm_client.py          # LLM 接口封装
│   ├── test_extraction_gate.py     # Skill 1 准确率
│   ├── test_usage_gate.py          # Skill 2 准确率
│   ├── test_merge_judge.py         # Skill 3 准确率 ⭐ 最重要
│   └── test_forget_judge.py        # Skill 4 准确率
├── integration/
│   ├── test_merge_worker.py        # 并发控制 + 资源释放
│   └── test_forget_scan.py         # 衰减公式 + 双轨触发
└── e2e/
    └── test_recall_with_gate.py    # 召回全链路（含 UsageGate）
```

### 9.2 test_llm_client.py

```python
async def test_chat_returns_openai_style_response()
async def test_chat_json_returns_dict()
async def test_retry_on_rate_limit()          # Mock 429 → 自动重试
async def test_json_forced_in_system_prompt() # 验证 system prompt 末尾追加了 JSON 指令
async def test_singleton_returns_same_instance()
```

### 9.3 test_merge_judge.py ⭐ 核心

使用**真实 LLM**，验证合并判断准确率 ≥ 85%。

| 场景 | 预期操作 | 示例 |
|---|---|---|
| 全新信息，无相似记忆 | ADD | "喜欢打篮球"，库中无运动偏好 |
| 信息更完整 | UPDATE | 旧"喜欢吃辣" → 新"喜欢川菜，特别爱麻辣口味" |
| 信息矛盾 | DELETE | 旧"喜欢吃辣" → 新"最近不想吃辣了" |
| 完全重复 | NONE | 旧"8岁" → 新"我今年8岁" |
| 同类不同对象 | ADD | 旧"喜欢草莓" → 新"喜欢西瓜" |
| 强度变化 | UPDATE | 旧"有点喜欢画画" → 新"非常热爱画画" |

```python
assert merge_action_accuracy >= 0.85    # ADD/UPDATE/DELETE/NONE 正确率
assert merge_content_quality >= 0.80    # UPDATE 合并后内容语义正确率
```

### 9.4 test_usage_gate.py

| 场景 | 预期结果 |
|---|---|
| 查询"不想吃辣"，候选含"喜欢吃川菜" | selected（相关） |
| 查询"作业好难"，候选含"喜欢草莓" | rejected（无关） |
| 查询"有点累"，候选含"曾经跑步累过" | rejected（太泛） |

```python
assert usage_gate_precision >= 0.90    # 选中的确实相关
assert usage_gate_recall    >= 0.85    # 相关的没有漏选
```

### 9.5 test_merge_worker.py（并发 + 资源释放）

```python
async def test_semaphore_bounds_concurrency():
    # 同时提交 100 个合并任务
    # 验证：峰值活跃任务数 <= merge_concurrency (20)
    active_peak = await measure_peak_concurrency(tasks=100)
    assert active_peak <= settings.merge_concurrency

async def test_queue_full_drops_gracefully():
    # 塞入 600 个任务（上限 500）
    # 验证：不抛异常，超出部分按策略丢弃
    results = await enqueue_batch(600)
    assert results.dropped == 100
    assert results.errors == 0

async def test_resources_released_after_merge():
    # 执行 50 个合并任务后
    # 验证：semaphore 全部释放，活跃任务数回到 0
    await run_merge_tasks(50)
    assert worker.active_count == 0
    assert worker._semaphore._value == settings.merge_concurrency
```

### 9.6 test_forget_scan.py

```python
def test_decay_formula_precision():
    # importance=0.6, λ=0.01, days=100 → 0.221
    score = compute_forget_score(0.6, 0.01, 100)
    assert abs(score - 0.221) < 0.001

def test_identity_hard_protection():
    # identity 类含"姓名"关键字 → 不进候选列表
    candidates = build_candidates(include_identity_name=True)
    filtered = apply_hard_protection(candidates)
    assert not any(
        m["type"] == "identity" and "姓名" in m["content"]
        for m in filtered
    )

async def test_capacity_overflow_triggers_forget():
    # 写入超过上限条数，验证触发容量检查
    ...
```

### 9.7 test_recall_with_gate.py（端到端）

```python
async def test_full_recall_pipeline():
    person_id = await create_test_person()
    await ingest_test_memories(person_id, memories=FIXTURE_MEMORIES)
    result = await recall_with_gate("我最近不太想吃辣了", person_id)

    assert len(result.selected) <= 3
    assert all(m.type in ["preference", "aversion"] for m in result.selected)
    assert result.used_gate is True

async def test_gate_returns_empty_when_irrelevant():
    result = await recall_with_gate("今天天气真好", person_id)
    assert result.selected == []
```

### 9.8 测试指标汇总

| 测试模块 | 指标 | 目标 |
|---|---|---|
| MergeJudge 操作准确率 | ADD/UPDATE/DELETE/NONE 判断正确 | ≥ 85% |
| MergeJudge 内容质量 | UPDATE 合并后内容语义正确 | ≥ 80% |
| UsageGate 精准率 | 选中的记忆确实相关 | ≥ 90% |
| UsageGate 召回率 | 相关记忆没有漏选 | ≥ 85% |
| 并发控制 | 活跃任务数 ≤ merge_concurrency | 100% |
| 资源释放 | 任务完成后 semaphore 全部归还 | 100% |
| 衰减公式精度 | 数值误差 | < 0.001 |
| 硬保护覆盖 | 身份关键信息不进候选 | 100% |

---

## 10. 文件结构变更

### 新增文件

```
memory_system/
└── llm/
    ├── __init__.py
    ├── client.py                    # LLMClient + get_llm_client()
    └── skills/
        ├── __init__.py
        ├── extraction_gate.py       # Skill 1
        ├── usage_gate.py            # Skill 2
        ├── merge_judge.py           # Skill 3
        └── forget_judge.py          # Skill 4
```

### 修改文件

| 文件 | 改动内容 |
|---|---|
| `pipeline/worker.py` | 写入前插入 ExtractionGate；写入改为丢进 MergeTaskQueue |
| `pipeline/extractor.py` | 将 `anthropic.AsyncAnthropic` 替换为 `LLMClient` |
| `retrieval/recall.py` | 新增 `recall_with_gate()` + `GateResult`，原函数不变 |
| `scheduler/jobs.py` | 新增 `forget_scan_job()`，注册到 APScheduler |
| `storage/pg_store.py` | 新增 `soft_delete()`、`hard_delete_later()` 方法 |
| `models/enums.py` | 新增 `EXPERIENCE = "experience"` 枚举值（现有枚举中缺失） |
| `models/memory.py` | 新增 `last_accessed_at` 字段（用于衰减时钟重置） |
| `config.py` | 新增 merge/forget/llm 相关配置项 |

### 不变文件

Phase 0–3 的所有测试文件和模型文件**完全不变**，新模块通过新增函数集成，不修改现有逻辑。

---

## 11. 配置项汇总

新增至 `config.py`：

```python
# LLM 模型配置（统一使用 snapshot-pinned 名称）
llm_model: str = "claude-sonnet-4-6-20250514"      # 主模型（提取/合并，准确率优先）
llm_fast_model: str = "claude-haiku-4-5-20251001"  # 快速模型（门控判断，延迟优先）

# Merge Worker
merge_concurrency: int = 20
merge_queue_maxsize: int = 500
merge_similar_limit: int = 5
merge_similarity_threshold: float = 0.82

# 遗忘机制
forget_score_threshold: float = 0.05
forget_importance_protect: float = 0.8
forget_batch_size: int = 50
forget_judge_concurrency: int = 5          # ForgetJudge 批次并发上限
forget_hard_delete_delay: int = 3600       # 软删除后物理删除延迟（秒）

# 容量上限
memory_quota: dict = {
    "identity": 20_000, "personality": 15_000,
    "behavior": 30_000, "preference": 50_000,
    "aversion": 50_000, "experience": 150_000,
    "joy": 80_000, "pain": 80_000,
}

# 衰减速率
forget_lambda: dict = {
    "identity": 0.001, "personality": 0.002,
    "behavior": 0.005, "experience": 0.008,
    "preference": 0.010, "aversion": 0.010,
    "joy": 0.015, "pain": 0.015,
}
```

---

## 附录：参考来源

- **mem0 合并策略**：`mem0/memory/main.py` — `DEFAULT_UPDATE_MEMORY_PROMPT`（ADD/UPDATE/DELETE/NONE 四操作模式）
- **现有 Phase 1 决策层**：`retrieval/decider.py` — IntentClassifier + TypeGate（与 UsageGate 串联）
- **现有提取流水线**：`pipeline/worker.py` — ExtractionWorker（Merge Worker 基于此扩展）
