# ARCH_iter1: 第一轮改进方案

**作者**: DEV (工程架构师)
**目标版本**: iter1
**基于数据**: real_msc_20260412_164946_with_judge.json (50 samples)

---

## 当前指标基线

| 指标 | 当前值 | 目标 |
|------|--------|------|
| LLM Judge Memory Usage | 0.20 | 0.45+ |
| Memory Coverage | 0.80 | 0.85+ |
| Semantic Similarity | 0.548 | 0.60+ |
| LLM Judge 综合 | 0.437 | 0.55+ |
| 拒绝回复 ("I don't have...") | 存在 | 0 |

---

## 一、根因分析 (Code-Level)

### 根因 1: memory_usage 全部为 0.20 — 召回的记忆根本没被用上

**现象**: 50个样本中，`memory_usage` 几乎全部为 1/5 = 0.20。
即便 `has_memory=true`，LLM Judge 也打最低分。

**具体证据** (从 JSON 结果提取):
- sample_0: has_memory=true, memory_usage=0.2, reason="ignores recalled memory"
- sample_2: has_memory=true, memory_usage=0.2, reason="hallucinates 'Randy Roth'"
- sample_3: has_memory=true, memory_usage=0.2, reason="hallucinates bio-themed slumber party, soundproof dorm room"
- sample_4: has_memory=true, memory_usage=0.2, reason="hallucinates 'visiting Auschwitz'"
- sample_29: has_memory=true, memory_usage=0.2, reason="hallucinates hotel's patio"
- sample_30: has_memory=true, memory_usage=0.2, reason="hallucinates 'homecoming win with Lynn in her tux'"

**根本原因在 `qa_module.py` 的两个代码缺陷**:

#### 缺陷 A: `_build_context` 只提取 profile + event summary，丢弃原始语句

```python
# 当前代码 (qa_module.py L50-70)
def _build_context(self, memories: List[Dict[str, Any]]) -> str:
    context_parts = []

    # Add profile if available
    for memory in memories:
        if 'profile_summary' in memory and memory['profile_summary']:
            context_parts.append(f"Profile: {memory['profile_summary']}")

    # Add events
    for memory in memories:
        if 'events' in memory:
            for event in memory['events']:
                summary = event.get('summary', '')   # <--- 只取 summary
                if summary:
                    context_parts.append(f"Event: {summary}")

    if not context_parts:
        return "No relevant memories found."

    return "\n".join(context_parts)
```

问题: `event` 对象还有 `verbatim`（原始对话语句）字段，但被忽略了。
`summary` 是经过 LLM 提炼过的摘要，原始细节已丢失。

#### 缺陷 B: `_generate_dialogue` 中 prompt 设计不驱动记忆使用

```python
# 当前代码 (qa_module.py L76-92)
if has_memory:
    prompt = f"""You are a friendly conversational assistant. Generate a natural, engaging response to continue the conversation.

User's Previous Information:
{context}

Current User Message:
{user_message}

Instructions:
- Generate a natural, engaging response that shows you remember the user
- Use the previous information to personalize your response when relevant  # <--- "when relevant" = LLM可以选择不用
- Show interest and ask follow-up questions when appropriate
- Keep the response SHORT and conversational (15-25 words maximum)  # <--- 字数限制太严，无法引用记忆细节
- DO NOT just repeat what the user said
- Be enthusiastic and supportive

Response:"""
```

三个关键问题:
1. "when relevant" 给了 LLM 不使用记忆的借口
2. 15-25 words 极限让引用记忆的空间几乎为零
3. 没有明确要求"必须引用一个具体事实"

---

### 根因 2: 幻觉严重 — LLM 在记忆不足时自己编造细节

**现象**: 多个 has_memory=true 的样本，生成回复中出现了完全不在记忆上下文中的细节：
- "Randy Roth" (sample_2) — 编造书名
- "visiting Auschwitz" (sample_4) — 编造旅游目的地  
- "bio-themed slumber party, soundproof dorm room" (sample_3)
- "homecoming win with Lynn in her tux" (sample_30)
- "Trooper's corn-dog legacy" (sample_17)
- "User_3", "User_20", "User_25", "User_32" — 直接用占位符名字作为真实人名

**根本原因**: 当 context 中出现的记忆信息不具体或不相关时，LLM 没有被约束"只用上下文中存在的事实"。
Prompt 说 "Use the previous information" 但没说 "ONLY information from context"，
也没说 "if no specific facts apply, just respond naturally WITHOUT inventing facts"。

---

### 根因 3: _build_context 传入的是整个 recall_data dict 列表，结构不匹配

```python
# test_real_msc.py L83-85
generated_response = await self.qa_module.generate_dialogue_response(
    [recall_data], user_message     # <--- 传的是 [recall_data] 整个字典包在列表里
)
```

```python
# qa_module.py L61-65
for memory in memories:       # memory = recall_data 整个字典
    if 'events' in memory:
        for event in memory['events']:
            summary = event.get('summary', '')   # event 是什么格式？
```

`recall_data` 结构从 `memory_client.py` 看:
```python
# memory_client.py L80
return data.get("recall", {})
```

而 `test_real_msc.py` 中构造 memory_context 的逻辑是:
```python
# test_real_msc.py L74-80
if recall_data.get('profile_summary'):
    memory_context_parts.append(f"Profile: {recall_data['profile_summary']}")
if recall_data.get('events'):
    for ev in (recall_data['events'] or [])[:3]:
        memory_context_parts.append(f"Event: {ev}")   # <--- ev 是原始 event 对象
```

这说明 events 是字典列表，但传给 judge 的 `memory_context` 中 `Event: {ev}` 会打印整个字典对象的 repr，不是 human-readable 字符串。

---

### 根因 4: Memory Coverage 卡在 80% — 达标但未超越

当前 50 样本中有 10 个 `has_memory=false`（samples: 1, 14, 15, 22, 26, 33, 34, 40, 45, 49 等）。

从 test_real_msc.py L71:
```python
has_memory = bool(recall_data.get('profile_summary') or recall_data.get('events'))
```

Memory Coverage = 有 profile_summary 或 events 的样本比例。
这些 false 案例意味着记忆系统对这些对话没有提取到任何内容，或提取的内容在 20s 后还没存入。

**注意**: PM 说召回管道不动，但 test_real_msc.py 中的 `await asyncio.sleep(20)` 等待时间
决定了能用的记忆量。这是 test 脚本的等待时间，不涉及召回管道本身。

---

### 根因 5: LLM Judge memory_context 传入格式损坏

```python
# test_real_msc.py L75-80
memory_context_parts = []
if recall_data.get('profile_summary'):
    memory_context_parts.append(f"Profile: {recall_data['profile_summary']}")
if recall_data.get('events'):
    for ev in (recall_data['events'] or [])[:3]:
        memory_context_parts.append(f"Event: {ev}")   # ev 是 dict，打印出来是 {'summary': '...', 'verbatim': '...'}
memory_context = "\n".join(memory_context_parts)
```

当 judge 收到 `Event: {'summary': 'xxx', 'event_time': '...'}` 这样的字符串时，
judge LLM 难以解析，导致 memory_usage 评分不准确（倾向于打低分）。

---

## 二、改进方案（按优先级）

---

### P0: 修复 qa_module.py — 强制记忆使用 + 反幻觉 prompt

**文件**: `/home/zihai/workspace/Agent_server_design/memory_system/test_msc_dataset/qa_module.py`

**目标指标**: LLM Judge Memory Usage: 0.20 → 0.45+

#### 改动 1: 扩展 `_build_context` — 提取 verbatim + 更清晰的格式

**BEFORE**:
```python
def _build_context(self, memories: List[Dict[str, Any]]) -> str:
    """Build context string from memories"""
    context_parts = []

    # Add profile if available
    for memory in memories:
        if 'profile_summary' in memory and memory['profile_summary']:
            context_parts.append(f"Profile: {memory['profile_summary']}")

    # Add events
    for memory in memories:
        if 'events' in memory:
            for event in memory['events']:
                summary = event.get('summary', '')
                if summary:
                    context_parts.append(f"Event: {summary}")

    if not context_parts:
        return "No relevant memories found."

    return "\n".join(context_parts)
```

**AFTER**:
```python
def _build_context(self, memories: List[Dict[str, Any]]) -> str:
    """Build context string from memories - includes verbatim quotes for grounding"""
    context_parts = []

    # Add profile if available
    for memory in memories:
        if 'profile_summary' in memory and memory['profile_summary']:
            context_parts.append(f"[USER PROFILE] {memory['profile_summary']}")

    # Add events - prefer verbatim over summary for factual grounding
    event_count = 0
    for memory in memories:
        if 'events' in memory and memory['events']:
            for event in memory['events']:
                if event_count >= 5:  # cap at 5 events to avoid bloat
                    break
                # Use verbatim (original utterance) if available, fall back to summary
                verbatim = event.get('verbatim', '')
                summary = event.get('summary', '')
                content = verbatim if verbatim else summary
                if content:
                    context_parts.append(f"[KNOWN FACT] {content}")
                    event_count += 1

    if not context_parts:
        return ""  # Return empty string, not "No relevant memories found."

    return "\n".join(context_parts)
```

**预期效果**: LLM 能看到真实的原始语句（verbatim），不再只看摘要，幻觉概率降低。
**风险**: 若 verbatim 字段为空，仍回退到 summary，无损失。

---

#### 改动 2: 重写 `_generate_dialogue` prompt — 强制记忆引用 + 禁止幻觉

**BEFORE**:
```python
if has_memory:
    prompt = f"""You are a friendly conversational assistant. Generate a natural, engaging response to continue the conversation.

User's Previous Information:
{context}

Current User Message:
{user_message}

Instructions:
- Generate a natural, engaging response that shows you remember the user
- Use the previous information to personalize your response when relevant
- Show interest and ask follow-up questions when appropriate
- Keep the response SHORT and conversational (15-25 words maximum)
- DO NOT just repeat what the user said
- Be enthusiastic and supportive

Response:"""
else:
    prompt = f"""You are a friendly conversational assistant. Generate a natural, engaging response to continue the conversation.

Current User Message:
{user_message}

Instructions:
- Generate a natural, engaging response even though you don't have previous information about the user
- Respond naturally to what the user just said
- Show interest and ask follow-up questions when appropriate
- Keep the response SHORT and conversational (15-25 words maximum)
- DO NOT say "I don't have enough information"
- Be enthusiastic and supportive

Response:"""
```

**AFTER**:
```python
if has_memory:
    prompt = f"""You are a warm, attentive friend who remembers details from past conversations.

What you know about this person:
{context}

Their message just now:
{user_message}

Rules (follow ALL of them):
1. You MUST reference at least one specific fact from "What you know" above in your reply.
2. ONLY use facts that appear in "What you know" — do NOT invent or assume any other facts.
3. If a known fact is relevant, weave it naturally into your response.
4. Keep it conversational and warm (20-35 words).
5. Ask one follow-up question that connects to what you know about them.
6. Never use placeholder names like "User_X" — refer to them naturally (e.g. "you").

Reply:"""
else:
    prompt = f"""You are a warm, attentive conversational partner.

Their message:
{user_message}

Rules:
1. Respond naturally and warmly to what they said.
2. Do NOT say "I don't have enough information" or similar refusals.
3. Do NOT invent personal details about this person.
4. Ask one genuine follow-up question based only on what they just said.
5. Keep it conversational (20-35 words).

Reply:"""
```

**预期效果**:
- "MUST reference at least one specific fact" → memory_usage 从 0.20 提升到 0.45+
- "ONLY use facts that appear in..." → 消灭幻觉 (hallucination)
- 明确禁止 "User_X" 占位符 → 消灭奇怪输出
- 明确禁止拒绝回复 → 消灭 "I don't have enough information"

**风险**:
- 若 context 为空字符串（has_memory=true 但 context 实际是空的），rule 1 无法执行。
  缓解: 将 _build_context 返回空时，强制切换到 else 分支（见改动 3）。

---

#### 改动 3: 修复 has_memory 判断逻辑 — 以实际 context 为准

**BEFORE**:
```python
async def generate_dialogue_response(self, memories: List[Dict[str, Any]], user_message: str) -> str:
    # Build context from memories
    context = self._build_context(memories)

    # Check if we have meaningful memories
    has_memory = bool(context and context != "No relevant memories found.")

    # Generate response using LLM
    response = await self._generate_dialogue(context, user_message, has_memory)
```

**AFTER**:
```python
async def generate_dialogue_response(self, memories: List[Dict[str, Any]], user_message: str) -> str:
    # Build context from memories
    context = self._build_context(memories)

    # has_memory is determined by whether context actually has content
    # (not just "No relevant memories found." sentinel)
    has_memory = bool(context and context.strip())

    # Generate response using LLM
    response = await self._generate_dialogue(context, user_message, has_memory)
```

**预期效果**: 与改动 1 配合，当 _build_context 返回空字符串时自动切到 no-memory 分支，避免 rule 1 冲突。
**风险**: 低，逻辑更清晰。

---

#### 改动 4: 提高 max_tokens — 给记忆引用留够空间

**BEFORE**:
```python
json={
    "model": self.model,
    "messages": [
        {"role": "user", "content": prompt}
    ],
    "temperature": 0.7,
    "max_tokens": 100      # <--- 太少，引用记忆后可能截断
}
```

**AFTER**:
```python
json={
    "model": self.model,
    "messages": [
        {"role": "user", "content": prompt}
    ],
    "temperature": 0.5,    # 降低温度：减少幻觉，保持自然
    "max_tokens": 150      # 足够引用记忆 + 提问
}
```

**预期效果**: 避免记忆引用被截断；降低 temperature 减少随机幻觉。
**风险**: 回复略长，但 prompt 中 20-35 words 约束会控制输出长度。

---

### P1: 修复 test_real_msc.py — memory_context 传给 judge 的格式

**文件**: `/home/zihai/workspace/Agent_server_design/memory_system/test_msc_dataset/test_real_msc.py`

**目标指标**: 让 LLM Judge 能正确解读记忆上下文 → memory_usage 评分更准确

#### 改动: 修复 memory_context 构造 — 从 dict 提取可读字符串

**BEFORE**:
```python
# test_real_msc.py L74-80
memory_context_parts = []
if recall_data.get('profile_summary'):
    memory_context_parts.append(f"Profile: {recall_data['profile_summary']}")
if recall_data.get('events'):
    for ev in (recall_data['events'] or [])[:3]:
        memory_context_parts.append(f"Event: {ev}")   # ev 是 dict！打印出来是 repr
memory_context = "\n".join(memory_context_parts)
```

**AFTER**:
```python
# test_real_msc.py L74-80
memory_context_parts = []
if recall_data.get('profile_summary'):
    memory_context_parts.append(f"Profile: {recall_data['profile_summary']}")
if recall_data.get('events'):
    for ev in (recall_data['events'] or [])[:3]:
        if isinstance(ev, dict):
            # Prefer verbatim (original utterance), fall back to summary
            text = ev.get('verbatim') or ev.get('summary') or str(ev)
        else:
            text = str(ev)
        if text:
            memory_context_parts.append(f"Event: {text}")
memory_context = "\n".join(memory_context_parts)
```

**预期效果**: Judge LLM 收到人类可读的记忆文本，能正确判断生成回复是否使用了记忆。
**风险**: 低，只是格式修复。

---

### P2: 提升 Memory Coverage — 增加等待时间或并发提取确认

**文件**: `/home/zihai/workspace/Agent_server_design/memory_system/test_msc_dataset/test_real_msc.py`

**目标指标**: Memory Coverage: 80% → 85%+

#### 改动: 将等待时间从 20s 提升到 30s，并增加轮询机制

**BEFORE**:
```python
# test_real_msc.py L59-60
# Wait for async background extraction to complete
await asyncio.sleep(20)
```

**AFTER**:
```python
# test_real_msc.py L59-69 (替换)
# Wait for async background extraction with polling
# 先等基础时间，然后轮询直到有记忆或超时
WAIT_BASE = 20
WAIT_MAX = 45
WAIT_POLL_INTERVAL = 5

await asyncio.sleep(WAIT_BASE)

# Poll up to (WAIT_MAX - WAIT_BASE) / WAIT_POLL_INTERVAL extra times
for _ in range((WAIT_MAX - WAIT_BASE) // WAIT_POLL_INTERVAL):
    # Quick probe: does recall return anything yet?
    probe = await self.memory_client.recall_memories(
        device_id=device_id,
        user_name=user_name,
        query=user_message,
        session_id=session_id
    )
    if probe.get('profile_summary') or probe.get('events'):
        break  # Memory ready, proceed
    await asyncio.sleep(WAIT_POLL_INTERVAL)
```

**预期效果**: 对提取速度慢的样本额外等待，Coverage 从 80% 升至 85%+。
**风险**: 每个 miss 样本多花 5-25s，总测试时间可能增加 10-15%。可接受。

---

### P3: 改进 Semantic Similarity — 调整 system role prompt 使语义更贴近 ground truth

**文件**: `/home/zihai/workspace/Agent_server_design/memory_system/test_msc_dataset/qa_module.py`

**目标指标**: Semantic Similarity: 0.548 → 0.60+

**背景分析**: Ground truth 是对话数据集中真实人类的回复，风格特点：
- 简短直接（平均 21 words）
- 有情绪共鸣（"That sounds great"、"I agree"、"Wow"）
- 没有 emoji
- 没有 "🌟", "😄", "😊" 等表情符号
- 通常先回应对方的内容，再分享自己的想法

当前生成的回复：用了大量 emoji，语气过分亢奋，语义方向常偏离。

#### 改动: 在 prompt 中增加 "match the conversational register" 指令

在 P0 的 AFTER prompt 基础上，在两个分支都加上:

```
- Match the casual, warm register of a real person chatting (no emojis, no "🌟" or "😄")
- If they share news, react to it first before asking questions
```

完整 has_memory prompt 新增 rules 6-7:
```
6. Never use placeholder names like "User_X" — refer to them naturally (e.g. "you").
7. Write like a real person chatting (no emojis, no excessive exclamations).
8. If they share news or feelings, first acknowledge/react, then optionally ask a question.
```

**预期效果**: 输出风格更贴近人类对话数据集，semantic similarity 余弦距离提升。
**风险**: 若 judge 偏好有活力的回复，dialogue_quality 可能略降。需观察。

---

## 三、不在本轮范围内的事项

根据 PM 决策，以下模块本轮不动：

1. **召回管道** (`retrieval/recall.py`)
   - 三路并行召回逻辑（vector/Redis/keyword）
   - 融合评分权重 (0.7 × sim + 0.2 × importance + 0.1 × recency)
   - 自适应截断阈值 (top_score × 0.55)
   - UsageGate LLM 决策

2. **存储架构**
   - PostgreSQL / pgvector 索引
   - Redis 存储结构
   - memory_entries 表 schema

3. **嵌入模型**
   - bge-m3 模型不变
   - embed_text 函数不变
   - 向量维度不变

4. **decider.py** (`retrieval/decider.py`)
   - IntentClassifier 规则（仅支持中文，MSC 是英文对话，但不动）
   - TypeGate 映射
   - RelevanceJudge 评分公式

5. **msc_evaluator.py** — 评估框架不动（保持评估公正性）

6. **llm_judge.py** — Judge prompt 和评分逻辑不动（保持基准一致）

---

## 四、实现顺序

### 第一步（最先实现，影响最大）: P0 全部改动

文件: `qa_module.py`

按顺序:
1. `_build_context` 改动 (verbatim + 清晰格式 + 空字符串返回)
2. `generate_dialogue_response` 中 `has_memory` 判断改动
3. `_generate_dialogue` has_memory=true 分支 prompt 重写（MUST reference, ONLY use facts）
4. `_generate_dialogue` has_memory=false 分支 prompt 改动（禁止拒绝）
5. max_tokens 100 → 150, temperature 0.7 → 0.5

**预计效果**: Memory Usage 0.20 → 0.40+，幻觉大幅减少，拒绝回复归零。

### 第二步: P1 — test_real_msc.py 的 memory_context 格式修复

文件: `test_real_msc.py`

仅修改 `memory_context` 构造的 4 行代码。

**预计效果**: Judge 评分更准确，memory_usage 数值更真实反映 P0 的改动效果。

### 第三步: P2 — 轮询等待提升 Coverage

文件: `test_real_msc.py`

将 sleep(20) 替换为带轮询的等待逻辑。

**预计效果**: Coverage 从 80% 升至 83-87%。

### 第四步: P3 — Semantic Similarity 微调

文件: `qa_module.py`

在 P0 的 prompt 上补充 style 相关 rules。

**预计效果**: Semantic Similarity 从 0.548 → 0.59-0.62。

---

## 五、风险汇总

| 改动 | 风险 | 缓解措施 |
|------|------|----------|
| P0 prompt 强制引用记忆 | 若记忆内容不相关，回复可能显得牵强 | rule: "ONLY if relevant" 保留灵活性 |
| P0 禁止 emoji | dialogue_quality judge 分可能略降 | 接受：memory_usage 提升抵消此损失 |
| P0 temperature 降低 | 回复多样性降低 | 0.5 仍有足够随机性 |
| P2 轮询等待 | 总测试时间增加 | 设置上限 45s，可接受 |
| P1 memory_context 修复 | 无负面风险 | 纯修复 |

---

## 六、预期新指标（估算）

| 指标 | 当前 | 预期 iter1 |
|------|------|-----------|
| LLM Judge Memory Usage | 0.20 | 0.42-0.50 |
| Memory Coverage | 0.80 | 0.83-0.87 |
| Semantic Similarity | 0.548 | 0.58-0.62 |
| LLM Judge 综合 | 0.437 | 0.52-0.58 |
| 拒绝回复数量 | >0 | 0 |

---

*文档生成时间: 2026-04-12*
*基于测试文件: real_msc_20260412_164946_with_judge.json*
