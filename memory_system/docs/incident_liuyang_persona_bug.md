# Incident Report: 刘杨 Persona 重复与污染 bug (P0)

- 报告人: DEV
- 日期: 2026-04-19
- 严重级别: P0
- 状态: hot-fix 已上线，脏数据清理 + schema 约束待执行
- 影响模块: memory_system / api/routers/memory_chat.py + storage/pg_store.py

------------------------------------------------------------

## 1. 症状（用户语言）

用户「刘杨」连续多天与 agent 对话后，profile 接口返回的姓名固定显示为「用户」而非「刘杨」。
排查发现同一个 owner_id 下出现了 6 个 role='primary' 的 person_node，相互之间 identity 信息散落且互相覆盖。
新会话中如果用户偶然说「我是用户」一类带占位词的句子，identity.name 会被实时身份提取写成「用户」，
进一步污染 primary 节点。

观察到的具体 owner: 7263aee7-a7bb-508f-9c2f-c487c6d8bf60

------------------------------------------------------------

## 2. 复现步骤

1. 客户端默认 user_name = "用户" 调用 POST /memory/chat。
2. 首次请求时后端 upsert_person_node(owner, "用户", "primary") 创建节点 N1。
3. 用户在对话里说「我叫刘杨」，realtime identity 提取 + rename_person 把 N1 的 name 改成「刘杨」。
4. 下一次请求客户端依旧传 user_name="用户"，老逻辑走 get_person_by_name(owner, "用户") → 命中失败 → 又 upsert 创建 N2。
5. 重复 3-4 N 次后，owner 下出现 N 个 primary 节点，且每个的 identity 都不完整。
6. 当某轮用户说出含占位词的句子（例如「我是用户的家人」），LLM identity_extractor 把 name 抽成「用户」写回最近一个 primary 节点。
7. profile 接口按 created_at DESC 拿到最新污染节点 → 返回 "姓名: 用户"。

------------------------------------------------------------

## 3. 根因（拆成 3 个 P0）

P0-A · 节点暴增（路由错误导致重复创建）
  - 位置: api/routers/memory_chat.py 老逻辑（修复前）
  - 描述: chat 入口按 user_name 查 person，rename 后 name 不再等于客户端传入的「用户」，每次请求都 miss → 走 upsert 新建。
  - 后果: 同 owner 下出现 6 个 primary 节点。

P0-B · identity 污染（占位词被当真名）
  - 位置: api/routers/memory_chat.py · _realtime_identity 老逻辑
  - 描述: realtime LLM identity 提取没有占位词白名单，patch.name ∈ {"用户","小朋友","你","我",...} 直接写入。
  - 后果: identity.name 被污染为「用户」。

P0-C · profile 路由错误（命中污染节点）
  - 位置: 同 P0-A 的根因，profile 端复用同一查找路径。
  - 描述: 命中最新创建/最新污染的节点，返回 "姓名: 用户"。
  - 后果: 用户看到错误身份。

------------------------------------------------------------

## 4. 影响面

- 直接受影响 owner: 7263aee7-a7bb-508f-9c2f-c487c6d8bf60（已确认 6 个重复 primary）。
- 潜在受影响范围: 任何使用默认 user_name="用户"（或其它占位词）的客户端 owner，特别是儿童陪伴端用 "小朋友/宝宝" 默认值的场景。
- 数据层面: person_nodes 多份重复 primary；events.participant_ids 指向其中某一份；relationships 以 from_person_id/to_person_id 散落到不同 person_id。

------------------------------------------------------------

## 5. 修复措施

### 5.1 hot-fix（已落地，禁止回滚 / 禁止再次修改）

文件: api/routers/memory_chat.py

- L521-535 · P0-A & P0-C 路由修复
  改用 `get_primary_person(owner_id)` 作为 chat 入口 primary 路由；只有该 owner 完全没有 primary 时，才 fallback 到 `get_person_by_name + upsert_person_node`。
  彻底消除「rename 后再次按 user_name miss → 重复创建」路径。

- L558-576 · P0-B identity 占位词白名单
  realtime `_realtime_identity` 在写入 identity 前过滤 patch:
  ```
  _PLACEHOLDER_NAMES = {
      "用户","小朋友","朋友","宝宝","孩子","小孩",
      "你","我","他","她","主人",
      "user","User","USER",
  }
  ```
  patch.name 命中白名单则丢弃并 log，其它字段照常合并。

### 5.2 待执行（本 incident 附带交付）

- B 脏数据清理: scripts/dedupe_primary_persons.py
  - dry-run 默认开，--execute 才真改
  - 按 owner_id 分组，role='primary' 多于 1 条 → 保留 created_at 最早的 keep_id，其余 drop
  - 同时把 identity.name ∈ 占位词集合的非最早 primary 也 drop
  - 重映射: events.participant_ids（UUID[]）+ relationships.from_person_id / to_person_id

- C schema UNIQUE 约束: scripts/add_primary_person_unique_index.sql
  - `CREATE UNIQUE INDEX CONCURRENTLY uq_primary_person_per_owner ON person_nodes(owner_id) WHERE role='primary';`
  - 必须在 dedupe 完跑后再上线，否则索引创建失败。

------------------------------------------------------------

## 6. 回归验证（主 agent 已跑 3 场景，全部通过）

- T1 全新 owner 自报「我叫刘杨」
  - 期望: profile 姓名=刘杨，person_nodes 仅 1 条 primary
  - 结果: ✓ 姓名=刘杨，✓ 1 个节点

- T2 故意投毒「我是用户」
  - 期望: identity.name 不被改写为「用户」，仍保持「刘杨」
  - 结果: ✓ identity={name:"刘杨", ...}，realtime log 出现「拒绝占位词作为 name: '用户'」

- T3 重启服务 + 新 session
  - 期望: 同 owner profile 仍返回「刘杨」，无新增 primary
  - 结果: ✓ 姓名=刘杨，节点数不变

------------------------------------------------------------

## 7. 未决项 / Follow-up

- [ ] 在生产环境执行 `python scripts/dedupe_primary_persons.py --owner-id 7263aee7-a7bb-508f-9c2f-c487c6d8bf60` dry-run，人工 review 输出
- [ ] dry-run 通过后 `--execute` 真清理
- [ ] 全量 dry-run（不带 --owner-id）扫一遍其它潜在受影响 owner
- [ ] 上线 scripts/add_primary_person_unique_index.sql（CONCURRENTLY，无表锁）
- [ ] 客户端默认 user_name 改为传 owner_id 对应的真实显示名或留空，不要再传「用户」
- [ ] 给 attribute_extractor 的 prompt 增加「不要把称谓/代词当作 name」的指令，作为深度防御
- [ ] 监控: 加一个定时 job，每天 SELECT owner_id, count(*) FROM person_nodes WHERE role='primary' GROUP BY 1 HAVING count(*)>1，告警

------------------------------------------------------------

## 8. 经验教训

- 任何「客户端传字段 + 服务端按该字段做查找 key」的接口，必须考虑该字段在生命周期中是否会被改写。
- LLM 抽取出的实体字段写库前必须有占位词/敏感词过滤，不能信任模型输出。
- 新的「单 owner 唯一 primary」语义必须用 schema 级 UNIQUE INDEX 兜底，不能只靠应用层逻辑。
