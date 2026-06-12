# REVIEW — liuyang hotfix Round 2

Reviewer: REVIEWER subagent
Scope: memory_system/api/routers/memory_chat.py L552-625
        + scripts/add_primary_person_unique_index.sql
Verdict: APPROVED (with non-blocking follow-ups)

================================================================
1. BLOCK-2 — placeholder name 白名单绕过
================================================================
File: memory_system/api/routers/memory_chat.py:590-600

验证项                                         结论
-----------------------------------------------------------------
NFKC 归一                                      OK   (L596)
lower()                                        OK   (L596)
去半角空格 ' '                                 OK   (L597)
去全角空格 '\u3000'                            OK   (L597)
去 tab '\t'                                    OK   (L597)
纯空白 '   '/全空白 → True                     OK   (L598-599 `if not norm: return True`)
覆盖路径 (唯一调用点)                          OK   (L607 在 _k=='name' 分支唯一调用)

逐 case 复核 (基于 L590-600 的 _is_placeholder_name 实现):

  'UsEr'      → NFKC→'UsEr' → lower→'user' → 去空格→'user'
                ∈ 白名单 ✓ 拒绝
  '用 户'     → NFKC→'用 户' → lower 不变 → 去 ' '→'用户'
                ∈ 白名单 ✓ 拒绝
  '用\u3000户' → NFKC 把 \u3000 视作兼容空格保留为 ' '
                (Python: unicodedata.normalize('NFKC','\u3000')==' ')
                → lower 不变 → 去 ' '→'用户'  ✓ 拒绝
                另 L597 显式再去 \u3000 兜底，安全冗余 ✓
  '   '       → norm 空串 → L598 命中 ✓ 拒绝
  'ＵＳＥＲ'  → NFKC→'USER' → lower→'user' → ∈ 白名单 ✓ 拒绝

结论: BLOCK-2 已堵死。

注意点 (非阻塞):
- L604 `if _v in (None, "", 0)` 只过滤裸空串；纯空白 '   ' 字符串值
  在非 name 字段上不会触发 placeholder 检查，会写入 identity。
  对 name 字段已被 _is_placeholder_name 覆盖，其它字段 (如 city)
  写入纯空白属于次要数据洁癖问题，不属于本次 hotfix scope。

================================================================
2. BLOCK-3 — try/except 语义混淆
================================================================
File: memory_system/api/routers/memory_chat.py:556-620

验证项                                         结论
-----------------------------------------------------------------
LLM extract 独立 try (L556-566)                OK
DB write 独立 try (L615-620)                   OK
日志措辞反映阶段                               OK
  L565 "[realtime] LLM identity 提取失败"
  L620 "[realtime] identity 写库失败"
LLM 失败后 return,不会误进 DB 路径             OK (L566)

结论: BLOCK-3 语义已修正。

================================================================
3. 新代码副作用扫描
================================================================

a) `_patch` 为 None / 非 dict 时 (L568-569):
   直接 return，无日志。
   - 这是预期行为：extract_identity_attributes 返回 None 表示
     "LLM 认为本句没有可抽取属性"，并非错误。
   - 对比修复前行为：原来也只是 if not patch: return，
     未引入新静默路径。
   - 非阻塞建议: 可加一行 DEBUG 级日志便于排查
     "no identity patch from LLM"，但不影响生产。

b) `import unicodedata as _ud` 在函数体内 (L595):
   - Python import 有 sys.modules 缓存，二次以后是 dict 查询，
     ns 级开销，realtime 路径每请求至多调用一次（仅 name 分支）。
   - 可接受。建议后续随 _PLACEHOLDER_NAMES 一起上提到模块级。

c) gather 中 _realtime_identity() 异常隔离 (L625-634):
   - 函数内部已自捕获两段异常，外层 gather 不会因身份提取失败
     而连带把 recall_result/old_cache 打回 None。✓

d) 没有发现新增竞态、资源泄漏、阻塞 IO。

================================================================
4. BLOCK-1 — 代码 fallback + DB UNIQUE 双保险评估
================================================================
File: memory_system/api/routers/memory_chat.py:524-533
File: memory_system/scripts/add_primary_person_unique_index.sql:29-31

DDL 审查:
  CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
      uq_primary_person_per_owner
      ON person_nodes (owner_id)
      WHERE role = 'primary';

- 部分唯一索引 (partial unique) 语义正确：
  对每个 owner_id 至多一行 role='primary'。✓
- CONCURRENTLY: 不锁写，生产安全。✓
- IF NOT EXISTS: 幂等，可重复执行。✓
- 前置 dedupe 流程 (scripts/dedupe_primary_persons.py) 已在
  注释中明示 dry-run → execute → 校验三步，runbook 完整。✓
- 回滚命令已给出。✓

代码侧 fallback (L524-533) race 残留分析:
  T1: get_primary_person → None
  T2: get_primary_person → None         (并发)
  T1: get_person_by_name → None
  T2: get_person_by_name → None
  T1: upsert_person_node('primary')  → INSERT 成功
  T2: upsert_person_node('primary')  → INSERT
       └─ DDL 上线后被 uq_primary_person_per_owner 拒写
          → 抛 UniqueViolation
          → L534 except 捕获 → 返回 degraded recall

风险评估:
- DDL 上线后，重复 primary 在 DB 层硬性不可能存在。
- T2 在并发窗口内会拿到 degraded 响应（一次失败），
  下一轮请求会通过 get_primary_person 命中 T1 写入的行，
  自愈。属于可接受的瞬时降级。

接受声明:
  ★ 接受代码 + DB 双保险方案：
    代码层 fallback 仍存在 race 风险，
    DDL (uq_primary_person_per_owner) 上线后，
    重复 insert 会被 DB 拒写，业务语义不会被破坏。

上线顺序硬性要求 (PM 注意):
  1. 先跑 dedupe_primary_persons.py --execute 清理历史脏数据
  2. 校验 SELECT ... HAVING count(*)>1 返回 0 行
  3. 再 psql 执行 add_primary_person_unique_index.sql
  4. 应用代码可以先于或后于 DDL 部署，无强依赖
     (代码不引用该索引名，仅靠 DB 兜底)

================================================================
最终结论
================================================================

VERDICT: APPROVED

BLOCK-2: FIXED  (memory_chat.py:590-600)
BLOCK-3: FIXED  (memory_chat.py:556-620)
BLOCK-1: ACCEPTED via 代码 + DB 双保险
         (memory_chat.py:524-533 + add_primary_person_unique_index.sql:29-31)

----------------------------------------------------------------
非阻塞跟进项 (建议进 backlog，不阻塞本次合并)
----------------------------------------------------------------
F1. 把 _PLACEHOLDER_NAMES_NORMALIZED + _is_placeholder_name +
    unicodedata import 上提到模块级常量/工具函数，便于复用与
    单测覆盖。 (memory_chat.py:577-600)

F2. 抽到 config/identity_blocklist.yaml，运营态可热更新。
    (代码注释 L575 已 TODO 标注)

F3. 增补繁体/日文/拼音占位词 + 多语种归一。
    (代码注释 L576 已 TODO 标注)

F4. _patch is None / 非 dict 路径加 DEBUG 日志，便于线上排查
    "为什么没写 identity"。 (memory_chat.py:568-569)

F5. 非 name 字段值为纯空白时也应跳过写入 (memory_chat.py:604)，
    与 name 字段保持一致的洁癖标准。

F6. 监控告警：DDL 上线后增加对 PG 错误码 23505
    (unique_violation) on table person_nodes 的指标计数，
    用于观察并发 race 实际触发频率，必要时再回头给代码加
    advisory lock。

F7. dedupe_primary_persons.py 的 dry-run 输出建议落盘到
    reports/ 目录，留审计痕迹。
