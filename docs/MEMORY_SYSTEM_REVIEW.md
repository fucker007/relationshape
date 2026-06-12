# memory_system 架构评审与结合方案

（探索代理精读报告的归档版，详细数据流/字段/端点清单见原始分析；本文档聚焦决策。）

## 它是什么

生产级**人物图谱记忆服务**：FastAPI + PostgreSQL/pgvector + Redis + BGE-M3。
- **九类记忆**围绕人物组织：identity/personality/behavior/preference/aversion/experience/joy/pain/relationship
- **人格设计的体现**：person_nodes 五个 JSONB 档案位（身份/性格/偏好/厌恶/行为）+ current_focus 近期关注 + 人物关系图（relation_type + sentiment 情绪记账）+ PersonalityData 带证据与强度
- **三层检索**：人物档案摘要（Redis，~0ms）→ 意图路由精确查询（<5ms）→ 向量语义（<10ms），外加**衰减缓存**（旧召回×0.6/轮衰减，高相关命中可复活）
- **每 5 轮异步 LLM 抽取**（Outbox + Poller 防掉线），定时任务做摘要刷新/记忆融合/遗忘扫描（差异化衰减系数 + LLM ForgetJudge）
- **自带 LoCoMo-Plus 评测**：当前基线 memory_usage=0.20 / coverage=0.80 / 综合 0.437

## 与 relationshape 的能力互补（关键行）

| | memory_system | relationshape |
| --- | --- | --- |
| 规模/检索 | **千万级、向量+图谱** | 进程内 JSON、字符二元组 |
| 承诺生命周期 | ✗ | **✓ open→due→kept/missed** |
| 危机封存 | 仅 is_sensitive 浅标记 | **✓ 永不召回的封存区** |
| 脆弱度分级 | ✗ | **✓ 秘密级不做开场白** |
| 信任/阶段/裂痕账本 | ✗ | **✓** |
| 角色自述账本（人设防崩） | ✗ | **✓** |
| 幽默雷区 | aversions（可对接！） | aversion_tags |

## 分工决策（建议）

**relationshape = 关系决策层（毫秒级、回合内）；memory_system = 用户记忆层（海量、跨会话）。**
原始需求文档第 16 节的边界在两侧都实现了，现在补桥即可：

```
prepare_turn ──query──▶ /graph/recall（档案摘要+事件） ──▶ 注入【TA是谁】【记忆】
commit      ──turn───▶ /memory/chat（喂抽取，回带下一轮预热召回）
承诺/信任/阶段/自述账本/敏感封存 —— 永久留在引擎侧（关系资产，非记忆数据）
```

## 四个立即可做的结合点

1. **MemoryPort 适配器**：引擎加 `MemoryPort` 协议（recall/observe 两方法），默认实现=内置 MemoryBank（零依赖兜底），HTTP 适配器=memory_system。商品与资产分离的老原则。
2. **profile_summary → 开场指令**：Layer-1 的 ~200 token 人物摘要在会话首轮注入【TA是谁】——这是 MemoryBank 永远建不出来的用户全景；current_focus 直接做开机惦记钩子。
3. **aversions(is_sensitive) → 幽默雷区**：他们的厌恶档案直通我们 `aversion_tags`，雷区从"本会话学到的"升级为"长期档案"。
4. **他们的 memory_usage=0.20 痛点，正是我们提示词层的专长**：记忆召回了但模型不用——我们的【记忆】渲染（"自然带一句，不硬塞"）+ 织体"落到具体"约束就是为这个问题设计的，iter1 的 P0（usage 0.20→0.45）可以直接换用我们的指令格式做实验。

## memory_system 要补的三个口子（按优先级）

1. **敏感封存端点**（P0，儿童产品红线）：危机内容引擎侧已不转发，但需要 `POST /events/{id}/seal` 处理既存数据；事件加 vulnerability 分级，防止 profile_summary/current_focus 把"怕输"当问候语料浮上来（我们本地修过同样的 bug）。
2. **钩子防污染在引擎侧解决**：commit 只转发用户生活类轮次（沿用 episode_types 过滤），设备抱怨/对角色的攻击不进图谱——不用改服务。
3. **承诺不上图谱**：保持引擎侧（数据小、延迟敏感、是关系资产不是事实记忆）。

## 延迟预算（实时语音可行性）

召回路径：引擎 1.3ms + HTTP 同机 ~5ms + Layer1 Redis ~0ms + 向量 ~10ms ≈ **20-50ms**，可入 prepare 关键路径；抽取全异步不占路径；commit 返回的衰减缓存召回可预热下一轮 opener。

## Phase-1 已落地（引擎侧，本仓库）

- `relationshape/memory_port.py`：MemoryPort 协议 + MemorySystemAdapter
  （fail-open 回退、时间锚进召回提示、危机轮零外发、防污染过滤）
- 引擎接线：远端召回并入【记忆】（可多带2条）、档案摘要会话首轮注入【TA是谁】
- 7 项契约测试（mock 服务）+ E1 指令级差分仪器 `eval/fusion_ab.py`
  （首读数：窗口外事实进指令 A1 0/6 vs A2 6/6）
- 默认零漂移：不配 port 时 197 项测试与 54 轮指令快照逐字不变

## Phase-2 待办（memory_system 服务侧规格）

1. `/api/v1/graph/recall` 支持 owner_id-only 解析 primary person
   （memory_chat 的 P0-A 逻辑复用），并接受 `include_profile` 开关
2. 事实有效区间：preferences/aversions/identity 加 `valid_from` /
   `invalidated_by`，更新即失效旧值（LoCoMo update 类对齐）
3. `POST /events/{id}/seal` 封存端点 + 事件 vulnerability 分级
   （秘密不浮上 profile_summary/current_focus）
4. 事件因果边：抽取 prompt 加 caused_by/leads_to（multi-hop 对齐）
5. 会话关闭时 flush 抽取（修每5轮触发的尾部丢失）
