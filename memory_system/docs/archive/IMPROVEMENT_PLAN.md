# 改进方案设计文档

基于 [CHECK_MORE_DATA.md](CHECK_MORE_DATA.md) 测试发现的三个系统局限，以下文档描述根本原因分析、候选方案及推荐实施路径。

---

## 问题 1：记忆冲突解决（Conflict Resolution）

### 根本原因

```
旧事实: "用户最喜欢的英雄是奥特曼，觉得他超厉害"   kw=0.308  score=0.520
新事实: "用户以前喜欢奥特曼，现在最喜欢蜘蛛侠了"   kw=0.231  score=0.498

查询: "我最喜欢什么英雄"
```

旧事实包含"英雄"关键词（kw=0.308），得分高于新事实（kw=0.231），导致旧答案主导召回。
系统当前不具备区分"旧状态"与"新状态"的能力。

### 重要说明

**生产层（`retrieval/recall.py`）已有时序衰减**：
```python
recency_factor = 1 / (1 + days_ago * 0.1)   # 10天前记忆 recency≈0.5
final_score = 0.5×sem + 0.3×importance + 0.2×recency
```
生产环境中，若旧事实写入超过数天，时序权重会自然降低其得分。

**问题主要存在于**：
1. 同一天内的前后矛盾（时序差异 = 0）
2. 模拟测试层（`simulate_test.py` 无时序权重）

### 候选方案

#### 方案 A：旧记忆主动降权（推荐）

写入新记忆时，检测同类型+主题相似的已有记忆，将其 `importance_score` 乘以衰减系数。

```python
# 伪代码（MemoryStore.write 扩展）
def write_with_conflict_check(new_entry, store):
    existing = [m for m in store.get_all_memories(person_id)
                if m.memory_type == new_entry.memory_type]
    for old in existing:
        if topic_overlap(old.content, new_entry.content) > 0.4:
            old.importance_score *= 0.5   # 旧记忆降权
    store.write(new_entry)
```

- **实现代价**：~30 行，不破坏现有接口
- **风险**：`topic_overlap` 误判可能降权无关记忆
- **适用范围**：`PREFERENCE / AVERSION / JOY / PAIN`，不适用于 `IDENTITY / RELATIONSHIP`

#### 方案 B：显式版本覆盖

扩展 `MemoryEntry` 添加 `supersedes: list[UUID]` 字段。写新记忆时显式声明取代哪些旧记忆，旧记忆标记 `is_merged=True`。

- **实现代价**：~50 行，需修改 API schema
- **优点**：精确、可溯源
- **缺点**：需要调用方明确知道要覆盖哪条记忆

#### 方案 C：类型感知时序衰减（模拟层补齐）

在 `simulate_test.py` 的 `recall()` 中补充时序因子，对齐生产层行为：

```python
# 当记忆有明显时序差异时加入衰减（仅对 PREFERENCE/AVERSION 类型）
if e.memory_type in (MemoryType.PREFERENCE, MemoryType.AVERSION):
    days_ago = (datetime.utcnow() - e.created_at).days
    recency = 1.0 / (1.0 + days_ago * 0.1)
    score *= recency
```

- **实现代价**：~10 行
- **作用**：让模拟测试与生产行为对齐

### 推荐路径

```
短期（修复模拟层）：方案 C，10 行，对齐生产行为
中期（生产优化）：方案 A，写入时主动降权，覆盖同天内冲突
长期（完整支持）：方案 B，显式版本链，适用于记忆融合场景
```

---

## 问题 2：召回置信信号缺失（Hallucination Signal）

### 根本原因

`recall()` 总是返回"最佳匹配"结果，即使所有记忆与查询完全无关。
调用方（LLM System Prompt 组装）无法区分：
- **情况 A**：召回了真正相关的记忆 → 可以使用
- **情况 B**：召回了重要性兜底的无关记忆 → 不应使用

```
查询: "我最喜欢的颜色是什么"
召回: "用户第一次独立做了饭，非常开心"  score=0.39  importance=0.9
→ LLM 可能把"做了饭"的喜悦情绪误解为颜色喜好
```

### 候选方案

#### 方案 A：绝对分数阈值（推荐，最简单）

```python
def recall_with_confidence(...) -> RecallResponse:
    records, sources = await recall(...)

    if not records:
        confidence = "empty"
    elif max(r["final_score"] for r in records) < 0.25:
        confidence = "low"       # 最高分都不到 0.25，说明无相关记忆
    else:
        confidence = "high"

    return RecallResponse(..., confidence=confidence)
```

- **实现代价**：< 20 行
- **缺点**：阈值 0.25 需根据实际数据校准

#### 方案 B：分数分布检测（自适应，推荐）

若所有记忆得分差异很小（方差小），说明没有真正命中的记忆：

```python
scores = [r["final_score"] for r in records]
max_s, mean_s = max(scores), sum(scores) / len(scores)

# "相对突出度"：top 分比均值高多少
relative_prominence = (max_s - mean_s) / max_s  # 0 = 所有分一样高，1 = top远超其他

if relative_prominence < 0.15:
    confidence = "uncertain"   # 没有明显胜出的记忆
elif max_s < 0.25:
    confidence = "low"
else:
    confidence = "high"
```

- **实现代价**：~30 行
- **优点**：自适应，不依赖绝对值

#### 方案 C：查询-类型一致性校验

检查查询意图与召回记忆类型是否吻合：
- 查询含"颜色/城市/号码" → 召回结果全是 `JOY/EXPERIENCE` → 类型不匹配 → `confidence=low`

- **实现代价**：~50 行（需维护查询意图→期望类型映射表）
- **优点**：语义感知，误报少

### RecallResponse 扩展

```python
class RecallResponse(BaseModel):
    memories: list[MemorySearchResult] | None = None
    summary: str | None = None
    search_latency_ms: int = 0
    sources: dict[str, int] = {}
    confidence: Literal["high", "uncertain", "low", "empty"] = "high"  # 新增
    top_score: float = 0.0                                               # 新增
```

### 推荐路径

```
立即实现：方案 A + B 组合（~30 行）
RecallResponse 加 confidence 字段
LLM prompt 组装时检查：confidence == "low/empty" → 不注入记忆，回答 "我还不了解这方面"
```

---

## 问题 3：指代消解（Coreference Resolution）

### 根本原因

```
Turn 1: "我有一只小狗叫球球"       → 存储记忆
Turn 5: "那个小家伙今天不开心"     → 查询
```

"那个小家伙"与"小狗球球"无字符 n-gram 重叠，LSA 语义也无法桥接（因为词汇完全不同）。
当前靠重要性分数兜底，若宠物记忆 importance=0.85 高于其他记忆则偶尔命中，本质是脆弱的。

### 候选方案

#### 方案 A：Session 实体缓冲区（推荐，近期可行）

在当前 session 内维护"最近提及实体"列表，查询时先做代词解析：

```python
@dataclass
class EntityBuffer:
    """Session 级实体缓冲，最近 10 轮内的实体提及"""
    entities: list[dict] = field(default_factory=list)  # [{name, type, memory_id, turn}]
    max_size: int = 20

    def update(self, text: str, memory_entries: list[MemoryEntry]):
        """从新写入的记忆中提取实体"""
        for entry in memory_entries:
            # 从内容中提取命名实体（简单正则）
            pets = re.findall(r'宠物叫(.{1,6})[，。]', entry.content)
            friends = re.findall(r'朋友(?:是|叫)(.{1,4})[，。]', entry.content)
            for name in pets:
                self.entities.append({"name": name, "type": "pet", ...})

    def resolve_pronoun(self, query: str) -> str:
        """将代词替换为实体名"""
        pronoun_patterns = ["那个小家伙", "它", "那条", "那只", "那位朋友"]
        for pattern in pronoun_patterns:
            if pattern in query:
                recent = self._most_recent(type_filter=["pet", "friend"])
                if recent:
                    return query.replace(pattern, recent["name"])
        return query
```

- **实现代价**：~80 行
- **作用范围**：单 session 内有效（重启 session 后上下文消失）
- **适用场景**：宠物名、朋友名、玩具名等具体命名实体

#### 方案 B：持久化实体索引

在 Redis 建立 `person:{id}:entities` Hash，存储该人曾提及的所有实体：

```
person:{person_id}:entities → Hash {
    "宠物": ["球球", "咪咪"],
    "朋友": ["萍萍", "小红"],
    "玩具": ["大牙"]
}
```

写入记忆时同步更新实体索引；查询时先查索引再扩充 query。

- **实现代价**：~100 行（含 Redis schema + 提取逻辑）
- **优点**：跨 session 有效，持久化

#### 方案 C：LLM 指代消解预处理

召回前，用轻量 prompt 将查询中的代词还原：

```
System: 根据对话上下文，将用户查询中的代词还原为实体名。

Context: [最近3条消息]
Query: "那个小家伙今天不开心"
Output: "小狗球球今天不开心"
```

- **实现代价**：~20 行（一次额外 LLM 调用）
- **延迟代价**：+200~400ms（可用小模型如 haiku）
- **准确率**：最高

### 推荐路径

```
短期：方案 A（session 实体缓冲区）
      覆盖 80% 的 session 内指代场景
      不需要额外存储，80 行实现

中期：方案 B（持久化实体索引）
      跨 session 的"它/那只/那个"依然有效
      建议与记忆写入流程合并，增量维护

长期：方案 C（LLM 预处理）+ 方案 B（后备）
      先查实体索引，若有高置信匹配则不调用 LLM
      只在置信度低时触发 LLM 消解，节省成本
```

---

## 综合实施优先级

| 优先级 | 问题 | 方案 | 代码改动 | 预期收益 |
|--------|------|------|---------|---------|
| **P0** | 置信信号缺失 | A+B 组合 | ~30 行 | LLM 能区分"找到"vs"不知道"；消除误导性回答 |
| **P1** | 记忆冲突（模拟层） | C（时序衰减补齐） | ~10 行 | 测试层与生产层行为对齐 |
| **P1** | 记忆冲突（生产层） | A（写入时降权） | ~30 行 | 同天内前后矛盾时正确回答 |
| **P2** | 指代消解 | A（session 缓冲区） | ~80 行 | 单 session 内"那个/它"正确关联 |
| **P3** | 指代消解（持久化） | B（实体索引） | ~100 行 | 跨 session 指代也有效 |

---

## 影响评估

### 问题 2 修复后（置信信号）对 LLM Prompt 的影响

```python
# 当前（无 confidence）
summary = build_summary(name, records)
system_prompt = f"你了解的关于{name}的信息：\n{summary}"

# 修复后（有 confidence）
if recall_response.confidence in ("low", "empty"):
    # 不注入记忆，让 LLM 诚实说"我还不了解这方面"
    system_prompt = f"你对{name}这方面还不了解，请诚实告知。"
elif recall_response.confidence == "uncertain":
    # 注入但标注不确定
    system_prompt = f"以下记忆仅供参考，可信度一般：\n{summary}"
else:
    system_prompt = f"你了解的关于{name}的信息：\n{summary}"
```

这是 **P0 优先级** 的核心原因：直接影响 AI 对话的诚实性，防止"自信地给出错误答案"。
