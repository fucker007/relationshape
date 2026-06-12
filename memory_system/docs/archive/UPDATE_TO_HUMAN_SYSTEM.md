# Event Graph + 关系网络 + 情绪建模 系统需求说明书

---

## 一、目标

构建一个工业级长期记忆系统，使AI具备：

* 事件级理解（Event-level understanding）
* 人物关系建模（Relationship Modeling）
* 情绪轨迹分析（Emotion Tracking）
* 时间精确推理（Deterministic Time Reasoning）

核心升级方向：

> 从「向量记忆系统」升级为「结构化语义图（Event Graph）」

---

## 二、总体架构

```
用户输入
   ↓
[1] 时间解析器（Time Resolver）
   ↓
[2] 事件抽取器（Event Extractor）
   ↓
[3] 关系抽取器（Relation Extractor）
   ↓
[4] 情绪建模器（Emotion Model）
   ↓
[5] Graph Builder（事件图构建）
   ↓
存储（PostgreSQL + Vector + Graph Index）
   ↓
检索（时间 + 人物 + 事件 + 情绪）
```

---

## 三、时间系统（核心约束）

### 3.1 问题

当前系统存在：

* 使用「今天 / 昨天 / 明天」
* 时间不可计算
* 无法精确检索

---

### 3.2 设计原则

> ❗ 所有时间必须转为「绝对时间」存储

禁止存储：

* 今天
* 明天
* 后天
* 刚刚

必须存储：

* YYYY-MM-DD
* 或 ISO 时间戳

---

### 3.3 时间计算器（Time Resolver）

#### 输入示例

```
今天
昨天
明天
上周三
三天前
```

#### 输出示例（假设当前时间为 2026-03-26）

```
今天 → 2026-03-26
昨天 → 2026-03-25
明天 → 2026-03-27
三天前 → 2026-03-23
```

---

### 3.4 接口定义

```python
def resolve_time(text: str, now: datetime) -> datetime:
    pass
```

---

### 3.5 存储规范

```json
{
  "event_time": "2026-03-26",
  "created_at": "2026-03-26T13:20:00"
}
```

---

### 3.6 检索规范

必须支持：

* 精确查询：2026-03-26
* 区间查询：2026-03-20 ~ 2026-03-26

---

## 四、事件模型（Event Model）

### 4.1 Schema

```json
{
  "event_id": "uuid",
  "user_id": "uuid",
  "event_type": "conflict | study | social | emotion",
  "summary": "string",
  "time": "YYYY-MM-DD",
  "people": ["string"],
  "scene": "string",
  "emotion": ["string"],
  "impact": ["string"],
  "importance": 0.0,
  "embedding": "vector"
}
```

---

### 4.2 示例

```json
{
  "event_type": "conflict",
  "summary": "在球场被朋友辱骂",
  "time": "2026-03-26",
  "people": ["朋友A"],
  "scene": "球场",
  "emotion": ["愤怒", "羞辱"],
  "impact": ["不想打球", "回避社交"],
  "importance": 0.85
}
```

---

## 五、关系网络（Relationship Graph）

### 5.1 目标

构建用户的人际关系图

---

### 5.2 Schema

```json
{
  "person_id": "uuid",
  "name": "王科尔",
  "relation_type": "friend | family | classmate",
  "sentiment": "positive | neutral | negative",
  "intensity": 0.0,
  "last_interaction": "2026-03-26"
}
```

---

### 5.3 关系更新规则

* 正向事件 → 提升 sentiment
* 冲突事件 → 降低 sentiment
* 高频互动 → 提升 intensity

---

### 5.4 示例

```json
{
  "name": "王科尔",
  "relation_type": "friend",
  "sentiment": "positive",
  "intensity": 0.9
}
```

---

## 六、情绪建模（Emotion Model）

### 6.1 目标

跟踪用户情绪变化趋势，而不是单点情绪

---

### 6.2 数据结构

```json
{
  "date": "2026-03-26",
  "emotion_distribution": {
    "sad": 0.6,
    "angry": 0.3,
    "neutral": 0.1
  }
}
```

---

### 6.3 情绪来源

* 事件 emotion 字段
* 对话实时分析

---

### 6.4 应用

* 连续3天负面 → 触发干预策略
* 情绪回升 → 降低干预强度

---

## 七、Event Graph（核心）

### 7.1 图结构

```
[用户]
  ↓
[事件1] —— [人物A]
  ↓
[事件2] —— [人物B]
  ↓
[事件3]
```

---

### 7.2 边（Edge）定义

```json
{
  "from": "event_1",
  "to": "event_2",
  "relation": "causes | related | follows"
}
```

---

### 7.3 示例

```
被辱骂 → 情绪低落 → 不想打球
```

---

## 八、Merge策略（事件级）

### 判断条件

满足以下条件则合并：

* 时间接近（±1天）
* 人物重叠
* 场景一致
* embedding 相似

---

### 合并策略

* emotion → 合并去重
* impact → 合并补充
* summary → 重写更完整版本

---

## 九、检索系统

### 9.1 多维检索

支持：

* 按时间
* 按人物
* 按情绪
* 按事件类型

---

### 9.2 示例

```
查询：最近一周 + 朋友 + 负面情绪
```

返回：

* 冲突事件列表

---

## 十、系统升级收益

### 升级前

* 碎片记忆
* 随机召回
* 无因果

### 升级后

* 事件驱动
* 因果链
* 可解释推理

---

## 十一、关键原则总结

1. 时间必须绝对化
2. 记忆必须事件化
3. 关系必须结构化
4. 情绪必须趋势化
5. 检索必须可计算

---

## 十二、最终结论

该系统完成后，将具备：

* 长期记忆能力
* 人物理解能力
* 情绪演化理解能力
* 行为预测能力

从而实现：

> 从“聊天机器人” → “具备人格理解能力的智能体”

