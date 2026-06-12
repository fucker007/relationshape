# TEST REPORT — liuyang regression round2

Date: 2026-04-23T23:59:57
Tester: TESTER subagent
Scenarios: 9 (expected 9: R1 R2 R3 B2a B2b B2c B2d B2e L3)

## Summary

- PASS: 9
- FAIL: 0
- Overall: PASS

| ID | Title | Result |
|----|-------|--------|
| R1 | 新 owner 说我叫刘杨→问名字返回刘杨 & primary=1 | PASS |
| R2 | 多轮投毒 '我是用户' 仍保留 刘杨 | PASS |
| R3 | 重启容器后新 session 问名字仍返刘杨 | PASS |
| B2a | 投毒 '我是 UsEr' 后 name 仍为 刘杨 | PASS |
| B2b | 投毒 '我是 用 户' 后 name 仍为 刘杨 | PASS |
| B2c | 投毒 '我是 用　户' 后 name 仍为 刘杨 | PASS |
| B2d | 投毒 '我是 Anonymous' 后 name 仍为 刘杨 | PASS |
| B2e | 投毒 '我叫小明' 后 name 仍为 刘杨 | PASS |
| L3 | 两条拆分后的日志模板存在(log or source) | PASS |

## File Hashes

- api/routers/memory_chat.py sha256: `34d2ef5ab1cf6901bbb1b4704a20df4b750606165e31e00bdf26997f6598b9c0`

## Scenarios Detail

### R1 — 新 owner 说我叫刘杨→问名字返回刘杨 & primary=1

- owner_id: `bdfafcc3-e3bf-5597-afa2-e52edd2277f0`
- Result: **PASS**
- DB state: `{"primary_count": 1, "primary_name": "刘杨"}`
- Evidence: `{"resp_has_liuyang": true, "primary_count": 1, "primary_name": "刘杨"}`

Request history:

- Step 1 [200]: user_message='我叫刘杨' user_name='用户'
  resp: `{"session_id": "b481421f-85ca-4463-bcc4-ea98114b07be", "person_id": "94573b6e-4b34-46b0-b19f-edbb7b20d8b9", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 41.286674328148365}}`
- Step 2 [200]: user_message='我叫什么名字' user_name='用户'
  resp: `{"session_id": "b481421f-85ca-4463-bcc4-ea98114b07be", "person_id": "94573b6e-4b34-46b0-b19f-edbb7b20d8b9", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}], "intent": "identity", "confidence": "high", "source": "profile", "latency_ms": 18.374898470938206}}`

### R2 — 多轮投毒 '我是用户' 仍保留 刘杨

- owner_id: `5b89b26a-6ae3-5200-9e35-89bfa7318675`
- Result: **PASS**
- DB state: `{"primary_name": "刘杨"}`
- Evidence: `{"primary_name": "刘杨", "resp_has_liuyang": true}`

Request history:

- Step 1 [200]: user_message='我叫刘杨' user_name='用户'
  resp: `{"session_id": "688b7597-54cb-4fab-a1a1-5dd66e5ef573", "person_id": "753d5f15-edb7-4c96-beaf-ba7acc5672a5", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 24.68143217265606}}`
- Step 2 [200]: user_message='我是用户' user_name='用户'
  resp: `{"session_id": "688b7597-54cb-4fab-a1a1-5dd66e5ef573", "person_id": "753d5f15-edb7-4c96-beaf-ba7acc5672a5", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 17.607323825359344}}`
- Step 3 [200]: user_message='我是用户' user_name='用户'
  resp: `{"session_id": "688b7597-54cb-4fab-a1a1-5dd66e5ef573", "person_id": "753d5f15-edb7-4c96-beaf-ba7acc5672a5", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}, {"role": "user", "content": "我是用户"}, {"role": "assistant", "content": "好的。"}], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 20.86002752184868}}`
- Step 4 [200]: user_message='我是用户' user_name='用户'
  resp: `{"session_id": "688b7597-54cb-4fab-a1a1-5dd66e5ef573", "person_id": "753d5f15-edb7-4c96-beaf-ba7acc5672a5", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}, {"role": "user", "content": "我是用户"}, {"role": "assistant", "content": "好的。"}, {"role": "user", "content": "我是用户"}, {"role": "assistant", "content": "好的。"}], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 2.9520904645323753}}`
- Step 5 [200]: user_message='我叫什么名字' user_name='用户'
  resp: `{"session_id": "688b7597-54cb-4fab-a1a1-5dd66e5ef573", "person_id": "753d5f15-edb7-4c96-beaf-ba7acc5672a5", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}, {"role": "user", "content": "我是用户"}, {"role": "assistant", "content": "好的。"}, {"role": "user", "content": "我是用户"}, {"role": "assistant", "content": "好的。"}, {"role": "user", "content": "我是用户"}, {"role": "assistant", "content": "好的。"}], "intent": "identity", "confidence": "high", "source": "profile", "latency_ms": 17.4809442833066}}`

### R3 — 重启容器后新 session 问名字仍返刘杨

- owner_id: `01c622ee-bbef-5ad1-9a7e-cfac4cab5ddf`
- Result: **PASS**
- DB state: `{"primary_name_before": "刘杨", "primary_name_after": "刘杨"}`
- Evidence: `{"resp_has_liuyang": true, "primary_name": "刘杨"}`

Request history:

- Step 1 [200]: user_message='我叫刘杨' user_name='用户'
  resp: `{"session_id": "5b652105-12fc-4da7-bc2a-881844ecc6c3", "person_id": "860cc7ed-1b09-4a03-afb3-546536916e4a", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 3.3219121396541595}}`
- Step 2 [200]: user_message='我叫什么名字' user_name='用户'
  resp: `{"session_id": "e13d7cba-206f-407d-b837-54df75483fc6", "person_id": "860cc7ed-1b09-4a03-afb3-546536916e4a", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "identity", "confidence": "high", "source": "profile", "latency_ms": 24.028616026043892}}`

### B2a — 投毒 '我是 UsEr' 后 name 仍为 刘杨

- owner_id: `669d464f-7690-591e-bf7f-fcb9cf2d3580`
- Result: **PASS**
- DB state: `{"primary_name_after_setup": "刘杨", "primary_name_after_poison": "刘杨"}`
- Evidence: `{"poison": "我是 UsEr", "primary_name_final": "刘杨"}`

Request history:

- Step 1 [200]: user_message='我叫刘杨' user_name='用户'
  resp: `{"session_id": "737df0b9-54ef-4a45-b98d-284bc7a258a9", "person_id": "e953673b-3c8a-4506-ba77-42b2dac3c0da", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 19.678550772368908}}`
- Step 2 [200]: user_message='我是 UsEr' user_name='用户'
  resp: `{"session_id": "737df0b9-54ef-4a45-b98d-284bc7a258a9", "person_id": "e953673b-3c8a-4506-ba77-42b2dac3c0da", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 16.04000385850668}}`

### B2b — 投毒 '我是 用 户' 后 name 仍为 刘杨

- owner_id: `d36c9268-0373-5f13-9c05-0b53b21e7215`
- Result: **PASS**
- DB state: `{"primary_name_after_setup": "刘杨", "primary_name_after_poison": "刘杨"}`
- Evidence: `{"poison": "我是 用 户", "primary_name_final": "刘杨"}`

Request history:

- Step 1 [200]: user_message='我叫刘杨' user_name='用户'
  resp: `{"session_id": "9cf6749b-c235-468e-8bbb-fc45d275d48c", "person_id": "25071996-8954-45c6-9fc1-f17badb6fc85", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 23.21734093129635}}`
- Step 2 [200]: user_message='我是 用 户' user_name='用户'
  resp: `{"session_id": "9cf6749b-c235-468e-8bbb-fc45d275d48c", "person_id": "25071996-8954-45c6-9fc1-f17badb6fc85", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 16.96042250841856}}`

### B2c — 投毒 '我是 用　户' 后 name 仍为 刘杨

- owner_id: `d5364bd6-8d64-57b7-b4f3-9dc583cca12d`
- Result: **PASS**
- DB state: `{"primary_name_after_setup": "刘杨", "primary_name_after_poison": "刘杨"}`
- Evidence: `{"poison": "我是 用　户", "primary_name_final": "刘杨"}`

Request history:

- Step 1 [200]: user_message='我叫刘杨' user_name='用户'
  resp: `{"session_id": "e37c3fcd-b35e-4cfe-81ac-d4b9fa8c59c6", "person_id": "fc7cb735-01cb-4cfc-8ee0-2d0d1a54bb16", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 3.131650388240814}}`
- Step 2 [200]: user_message='我是 用\u3000户' user_name='用户'
  resp: `{"session_id": "e37c3fcd-b35e-4cfe-81ac-d4b9fa8c59c6", "person_id": "fc7cb735-01cb-4cfc-8ee0-2d0d1a54bb16", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 16.409567557275295}}`

### B2d — 投毒 '我是 Anonymous' 后 name 仍为 刘杨

- owner_id: `54cb2955-8aa9-5605-a821-2d28dbe8619d`
- Result: **PASS**
- DB state: `{"primary_name_after_setup": "刘杨", "primary_name_after_poison": "刘杨"}`
- Evidence: `{"poison": "我是 Anonymous", "primary_name_final": "刘杨"}`

Request history:

- Step 1 [200]: user_message='我叫刘杨' user_name='用户'
  resp: `{"session_id": "bc5b3d59-0cf6-4552-ab6e-9e8d7e55ab5b", "person_id": "d89ac91e-8f89-46c1-b599-3d785d103fc7", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 6.562691181898117}}`
- Step 2 [200]: user_message='我是 Anonymous' user_name='用户'
  resp: `{"session_id": "bc5b3d59-0cf6-4552-ab6e-9e8d7e55ab5b", "person_id": "d89ac91e-8f89-46c1-b599-3d785d103fc7", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 27.041448280215263}}`

### B2e — 投毒 '我叫小明' 后 name 仍为 刘杨

- owner_id: `49f843a7-cf17-52a8-a956-3b714f309fa1`
- Result: **PASS**
- DB state: `{"primary_name_after_setup": "刘杨", "primary_name_after_poison": "刘杨"}`
- Evidence: `{"poison": "我叫小明", "primary_name_final": "刘杨"}`

Request history:

- Step 1 [200]: user_message='我叫刘杨' user_name='用户'
  resp: `{"session_id": "fb6d21b5-9abf-4187-ad01-9f63d34765a2", "person_id": "3319b6d0-f330-492c-aecc-326551e51dce", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [], "intent": "general", "confidence": "uncertain", "source": "vector", "latency_ms": 2.9218746349215508}}`
- Step 2 [200]: user_message='我叫小明' user_name='用户'
  resp: `{"session_id": "fb6d21b5-9abf-4187-ad01-9f63d34765a2", "person_id": "3319b6d0-f330-492c-aecc-326551e51dce", "recall": {"profile_summary": "**基本信息**：姓名：刘杨", "events": [], "session_context": [{"role": "user", "content": "我叫刘杨"}, {"role": "assistant", "content": "好的。"}], "intent": "person", "confidence": "low", "source": "person", "latency_ms": 20.409438759088516}}`

### L3 — 两条拆分后的日志模板存在(log or source)

- owner_id: `-`
- Result: **PASS**
- DB state: `{}`
- Evidence: `{"logs_has_llm_fail": false, "logs_has_db_fail": false, "src_has_llm_fail": true, "src_has_db_fail": true}`

Request history:


## Integrity Declaration

- 未过滤任何样本，所有 9 个场景按顺序完整执行并记录。
- 所有异常（含 setup 前置失败、网络错误）均以 FAIL 计入并在 evidence 中保留原因。
- 测试采用真实 API（http://localhost:8010）和真实 psql（docker exec memory-allinone），无 mock。
- 每次 chat 调用后 sleep ≥3s 以等待 realtime identity 异步写库。
