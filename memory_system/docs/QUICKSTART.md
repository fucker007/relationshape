# 记忆系统多角色协作开发流程 — 快速启动手册

---

## 角色一览

| 角色 | 身份 | 核心关注点 | Prompt 文件 |
|------|------|----------|------------|
| PM | 人类认知学专家 + 产品经理 | 用户感受到"被记住了吗" | docs/roles/PM_PROMPT.md |
| DEV | 工程架构师 + 开发者 | 召回速度/质量，存储提取机制 | docs/roles/DEV_PROMPT.md |
| TESTER | 测试工程师 | MSC 数据集公正评测 | docs/roles/TESTER_PROMPT.md |
| REVIEWER | 顶尖审核员 | 代码无欺骗性，测试无造假 | docs/roles/REVIEWER_PROMPT.md |

---

## 启动流程

```bash
cd /home/zihai/workspace/Agent_server_design/memory_system

# 查看完整流程
python orchestrate.py --run-all

# 查看当前状态
python orchestrate.py --status

# 分阶段查看指引
python orchestrate.py --phase 0   # PM目标 + DEV架构
python orchestrate.py --phase 1   # DEV实现 + REVIEWER代码审核
python orchestrate.py --phase 2   # TESTER测试 + REVIEWER测试审核
python orchestrate.py --phase 3   # 联合诊断
```

---

## 流程图

```
[用户定义总目标]
       |
   Phase 0
   PM: 制定产品目标 + 验收标准
   DEV: 设计技术方案
   REVIEWER: 审核架构 → APPROVED?
       |
   Phase 1
   DEV: 实现代码
   REVIEWER: 审核代码 → APPROVED?
       |
   Phase 2
   TESTER: MSC 数据集评测
   REVIEWER: 审核测试 → TEST_VALID?
       |
   Phase 3
   DEV + PM: 联合诊断
       |
   ┌── 达标? ──► 完成，向用户汇报
   |
   └── 未达标? ──► 返回 Phase 1 (下一轮迭代)
   |
   └── 分歧/无法解决? ──► 升级给用户
```

---

## 当前系统状态

测试基线 (2026-04-11, 50样本):
- BLEU:            0.0078  (目标: 0.15+)
- ROUGE-L:         0.0966  (目标: 0.25+)
- Memory Coverage: 68%     (目标: 85%+)
- 召回延迟:        未测量

核心问题:
1. QA 模块无记忆时直接拒绝回复
2. 有记忆时只重复，不融合
3. BLEU 指标可能不适合开放式对话评测

---

## 升级给用户的条件

满足以下任一条件，必须停下来向用户汇报：

- 架构设计被 REVIEWER 否决且 DEV 无法解决
- PM 认为需要根本重定义产品目标
- 连续 3 轮迭代指标无改善
- DEV 和 PM 联合诊断后存在根本性分歧
- REVIEWER 发现欺骗性代码或虚假测试，当事人拒不承认

---

## 文件结构

```
memory_system/
├── docs/
│   ├── MULTI_ROLE_WORKFLOW.md    ← 完整设计文档
│   ├── QUICKSTART.md             ← 本文件
│   ├── roles/
│   │   ├── PM_PROMPT.md
│   │   ├── DEV_PROMPT.md
│   │   ├── TESTER_PROMPT.md
│   │   └── REVIEWER_PROMPT.md
│   └── plans/                    ← DEV 实现计划 + PM 目标文档
├── reports/                      ← 联合诊断报告 + 代码/测试审核报告
├── test_msc_dataset/
│   └── results/                  ← 所有测试报告 (带时间戳)
└── orchestrate.py                ← 流程 Orchestrator
```
