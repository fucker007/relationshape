===== REVIEWER Code Review =====
Date: 2026-04-19
Target: api/routers/memory_chat.py hot-fix (P0-A 节点暴增 / P0-B identity 污染 / P0-C profile 姓名错)
Reviewer stance: adversarial

总体结论: REQUEST_CHANGES

理由摘要:
P0-A 路由修复方向正确,但 fallback 分支在"primary 已存在但被上次改名"的情形下
仍会绕过新路由产生重复节点(见 Blocking #1)。P0-B 白名单存在大小写/空白/拼音/常
见称谓漏网(见 Blocking #2 及若干 Nit)。此外有一个可能吞错误导致脏数据继续写入
的问题(Blocking #3)。

--- 检查清单逐项结论 ---

正确性:
- get_primary_person 存在? YES
  证据: storage/pg_store.py:717 `async def get_primary_person(self, owner_id)`,
  SQL: `WHERE owner_id=$1 AND role='primary' ORDER BY created_at LIMIT 1`,
  返回 dict | None。返回 None 时 fallback 到 get_person_by_name,逻辑方向正确。
- fallback 在 primary 已存在时是否可能绕过新路由?
  部分 YES —— 见 Blocking #1。
- 白名单是否覆盖所有常见中英文占位词? NO
  证据: memory_chat.py:565-569。漏: 小写 'user'(只有 'user' 实际有,但缺
  'usr'),漏 '小朋友们' '同学' '同学们' '客户' '来访者' '小伙伴' '亲' '哥' '姐'
  '先生' '女士' '老师' '小明'(常见 LLM 占位示例名)等。
- 白名单判断是否做 strip/lower? 部分。
  证据: memory_chat.py:574 `_v.strip() in _PLACEHOLDER_NAMES`,做了 strip,
  但未 lower。 'USER' 硬编码覆盖了,但 'UsEr' / 'User ' / '用户 ' 内部空格 '用 户'
  会绕过(strip 只去首尾)。
- LLM 返回 name 为 ""、None、缺失 key 是否健壮? 基本 YES。
  证据: memory_chat.py:572 `if _v in (None, "", 0): continue`,缺失 key 时
  for 循环自然不触发;但 `_v=" "`(纯空格)strip 后为 ""不在 PLACEHOLDER set 中,
  会被当成合法非空 name 写入 —— 见 Blocking #2 Note。

欺骗检查:
- test_mode / DEBUG 绕过? NO。整段没有 env/flag 短路,已通读 L521-594。
- 异常吞掉导致脏 name 写入? YES, 存在隐患 —— 见 Blocking #3。
  证据: memory_chat.py:583-584 `except Exception as _e: logger.warning(...)`
  捕获全部异常。若 update_person_field 本身抛错(例如 JSON 序列化失败),
  日志只打 "提取失败",措辞误导(实际是"写入失败")。
- 日志误导? YES (同上,L584 措辞不准确)。另 L582 `LLM identity 写入` 是在
  调用 update_person_field 之后打印,但若 DB 抛错已被 L583 吞掉,日志链路
  就是 "写入" 后紧跟 "提取失败",很容易让排查人员误以为是 LLM 阶段的失败。
- fallback upsert_person_node 在新分支下还能被正常触发吗? 能,但窄。
  证据: memory_chat.py:528-532。只在 primary 不存在且 get_person_by_name
  也 miss 时触发(即首次创建)。非死代码,但见 Blocking #1,该路径本身是
  产生重复的风险源。

清洁性:
- 白名单硬编码? YES,未引用 config。
  证据: memory_chat.py:565 内联 set。加新词必须改代码+重部署。属于可接受的
  hot-fix 妥协,但应在注释里留 TODO 指向 config —— 未做,记为 Nit。
- 注释解释 WHY? 部分。
  证据: memory_chat.py:522-523 "P0-A 修复" 解释了为什么换路由,OK。
  L564 "P0-B 修复" 说了"防止 LLM 写占位词",但没解释为什么选白名单而不是
  prompt 侧改进或 LLM 后校验 —— 记为 Nit。
- dead import/未使用变量? 有 minor 问题。
  证据: memory_chat.py:543-545 在函数体内 `import re as _re`, `import json
  as _json`,把 import 塞进请求 handler 属于风格问题,不是 dead,但模块顶部
  已 import re / json 的可能性需 DEV 确认(本审计未翻到顶部,留给 DEV)。

架构级问题:
- 多语言挂? YES。中英文混合白名单是典型 band-aid,拼音 'xiao peng you'、
  繁体 '用戶'、日文 '使用者'、空格注入 '用 户' 都能绕过。未见 FUTURE/TODO
  注解。建议加 `# TODO(i18n): normalize to NFKC + lower + 去空白,或改为
  LLM post-validator / 白名单从 config 加载`。
- 根治方案建议: 见下文"推荐的未来改进"。

--- Blocking issues (必须修才能合并) ---

[BLOCK-1] fallback 链条在 rename 后仍可能产生重复节点
  file: api/routers/memory_chat.py:528-532
  证据:
    L525: node = await gs.get_primary_person(req.owner_id)
    L526: if not node:
    L528:     node = await gs.get_person_by_name(req.owner_id, req.user_name)
    L530:     if not node:
    L531:         node = await gs.upsert_person_node(
    L532:             req.owner_id, req.user_name, "primary"
    L531-L532:            role="primary"
  场景: 如果 owner 曾经因其他 bug 路径(比如本次修复前)已有一个 role !=
  'primary' 或 role 列被异常写坏的老节点,get_primary_person 返回 None
  走到 L531,会再造一个 primary,仍然形成 2 个 primary。且 upsert_person_node
  在 pg_store.py:676-682 的去重键是 (owner_id, name),不是 (owner_id, role),
  所以多 primary 不会被 DB 层拦截。
  另一种场景: get_primary_person 返回 None(例如 owner 第一次 chat,client
  传 user_name="用户"),L528 get_person_by_name(owner, "用户") 也 miss,L531
  新建 name="用户" role="primary"。下一轮 LLM 把 name 改成真名调用 rename_person
  (pg_store.py:708),第三轮 client 仍传 user_name="用户",get_primary_person 这次
  命中(好);但如果 get_primary_person 因任何原因失败(网络抖动),L534 的
  `except Exception` 直接 return degraded,不写库 —— 这是好的;但若只有 L525
  抛错而之后 retry 重走整个函数,仍走 L528 get_person_by_name("用户") miss →
  L531 新建 → 重复。
  要求: 把新建动作(L531)加一道 "再次确认 primary 仍不存在" 的并发/重试安全
  检查,或者在 upsert_person_node 层加 (owner_id, role='primary') 唯一约束;
  最小修法是在 L531 之前再 `get_primary_person` 一次,或把 role 参数从
  'primary' 改为 'primary' 仅在 L531 的"首次"分支下(现在就是,但缺并发保护)。

[BLOCK-2] 白名单未 lower,也未做中文空白归一,P0-B 未真正堵死
  file: api/routers/memory_chat.py:565-574
  证据:
    L568: "user", "User", "USER",
    L574: if _k == "name" and isinstance(_v, str) and _v.strip() in _PLACEHOLDER_NAMES:
  漏洞:
    1. 'uSer' / 'USer' 等大小写变体不在 set,绕过。
    2. '用 户'(全角/半角空格夹在中间)strip 只去首尾,绕过。
    3. 纯空白字符串 '   ' strip 后变 ''; L572 的 `_v in (None,"",0)` 只判
       原始 _v,没判 stripped 值,所以 _v='   ' 不会在 L572 跳过,会走到
       L574,stripped='' 不在 set,然后 L577 被当成合法 name 写入。
  要求: L574 改为 `_v.strip().lower().replace(" ","").replace("　","") in
  _PLACEHOLDER_NAMES_NORMALIZED`(且 set 成员全部预先 lower/去空白);同时
  在 L572 增加 `or (isinstance(_v,str) and not _v.strip())`。

[BLOCK-3] 异常吞噬 + 日志措辞误导,掩盖写库失败
  file: api/routers/memory_chat.py:581-584
  证据:
    L581: await gs.update_person_field(person_id, "identity", _cur_identity)
    L582: logger.info(f"[realtime] LLM identity 写入: {_cur_identity}")
    L583: except Exception as _e:
    L584: logger.warning(f"[realtime] LLM identity 提取失败: {_e}")
  问题: L581 写库失败也会被 L583 捕获,但 L584 说成"提取失败",与实际阶段
  不符。刘杨这个 bug 的根因之一就是"identity 被错误写入",如果将来写入路径
  再出故障,日志会误导定位。
  要求: 把 try 拆成两段,或把 L584 措辞改为 "[realtime] identity 处理失败
  (extract or write)";更好是分别 try extract / try write。

--- Non-blocking nits (合并后跟进) ---

[NIT-1] 白名单漏常见占位词
  file: api/routers/memory_chat.py:565-569
  缺: '小朋友们' '同学' '同学们' '客户' '来访者' '小伙伴' '宝贝' '亲' '哥'
  '姐' '先生' '女士' '老师' '张三' '李四' '小明' '小红' '匿名' '路人'
  'anonymous' 'someone' 'guest' 'customer' 'kid' 'child' 'friend' 'buddy'。
  LLM 在没拿到真名时很爱用 '小明'/'张三'/'路人' 作占位。

[NIT-2] 白名单硬编码,缺配置化 TODO
  file: api/routers/memory_chat.py:565
  建议加: # TODO(hotfix-followup): 把 _PLACEHOLDER_NAMES 抽到
  config/identity_blocklist.yaml,便于不重启补词。

[NIT-3] 注释没解释"为什么选白名单而不是 prompt 侧修复"
  file: api/routers/memory_chat.py:564
  hot-fix 用白名单是因为 prompt 侧改动影响面大、回归风险高,值得在注释里
  说明这是权衡,而非首选方案。

[NIT-4] import 放在函数体内
  file: api/routers/memory_chat.py:543-545, 557
  `import re as _re` / `import json as _json` / `from llm.skills...` 放在
  请求 handler 内,每次 chat 都走一次 import 缓存查表。改为模块顶部 import
  (确认没有循环依赖)。

[NIT-5] _cur_identity 解析假设 _node_cur 非 None
  file: api/routers/memory_chat.py:558-561
  `_node_cur["identity"]` 若 _node_cur 为 None(极端竞态: person 在本函数
  前半段刚建好,_realtime_identity 并发读时主键查不到),会 TypeError。
  被 L583 吞掉,不致命,但应加 `if not _node_cur: return`。

--- Deception check 结果 ---

- test_mode / DEBUG 短路: 未发现,clean。
- 吞异常导致脏数据落库: 见 BLOCK-3,日志措辞误导确实存在。
- dead code: upsert_person_node 的 fallback(L531)非死代码,但 BLOCK-1
  指出它是重复节点的剩余风险源。
- 没发现 DEV 用 "skipped" 一类措辞把写入错误伪装成跳过。总体诚实度 OK,
  只有 L584 一处措辞误导。

--- 推荐的未来改进(不阻塞本次)---

1. 根治 identity 姓名污染应该走"正向验证"而非"反向黑名单":
   - 要求 LLM 返回 name 时必须同时返回 evidence_span(原文中自报姓名的片段),
     后端做子串/正则验证 `我叫X|叫我X|我是X`;无 evidence 则拒。
   - 或给 name 字段加 post-validator: 长度 ≥2、非常见占位词、出现在
     user_message 里才接受。
2. PersonNode 层加 DB 约束 `UNIQUE(owner_id) WHERE role='primary'`
   (partial unique index),从存储层根治 P0-A。
3. _PLACEHOLDER_NAMES 配置化 + NFKC 归一化 + lower,统一放 util:
     def is_placeholder_name(s: str) -> bool
   memory_chat.py 和后续 batch extractor 共用。
4. rename_person 与 upsert_person_node 之间需要事务或幂等键,防止并发重复。
5. 补单测: 覆盖 (a) 已 rename 后 user_name="用户" 二次请求不造新节点;
   (b) LLM 返回 name='User'/'用户 '/'用 户'/'小明' 全部被拒。

================================
