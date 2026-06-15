# relationshape

**随时间生长的关系-人格-情商引擎**，为长期陪伴型 AI 提供每一轮对话的结构化指令。

它解决的问题：大模型很会说话，但没有"和你的关系"。把人设写成长提示词，得到的是一个永远热情、永远讨好、转头就忘、对谁都一样的客服。relationshape 把关系做成**可生长、可持久化、可测试的状态机**，挂在任意 LLM 管线旁边：

```
用户说话 ──→ engine.prepare_turn() ──→ TurnDirective（本轮指令）──→ 注入 LLM ──→ 回复
                                                                        │
用户与回复 ←──────────────── engine.commit() ←──────────────────────────┘
              （记忆写入、信任记账、人格适应、阶段推进、承诺追踪）
```

引擎自己**不调用大模型、零外部依赖**（纯标准库）。它决定"以什么身份、什么情绪、什么形状、什么记忆去回应"，大模型只负责把结构化要求说成人话。

## 一段关系长什么样

跑 `python demo/simulate.py` 可以看到 30 天的完整生长：

- 第 0 天（陌生）：礼貌克制，记下"小禾喜欢恐龙"，轻确认一次（memory_seed 奖励）
- 第 2 天：用户说累，进入 `soft_react→mirror→validate→care`，禁止说"想开点"
- 第 4 天：同桌抢橡皮 → 先问人、明确站队、带猜测进现场，禁止讲道理
- 第 6 天：用户笑了 → "纸板翅膀"成为内部梗
- 第 7 天：认识 7 天里程碑，回调内部梗
- 第 9 天：用户连续低落 → 整条线程幽默禁用（情绪惯性），秘密级表露被郑重接住
- 第 12 天：被骂"你真笨" → 不服气+守住自尊（裂痕-1信任）；用户说"逗你的" → 接受安抚，修复后信任反而高于裂痕前
- 第 15 天（熟悉）：开场主动惦记"上次你说有点难过，好点了吗"
- 第 21 天：用户拿奖 → `react→capitalize→curious`，放大细节一起开心，禁止泼冷水
- 第 30 天：隔 8 天重逢 → 暖场但**零指责**（指令明确禁止"怎么才来"）

## 理论根基 → 模块映射

| 领域 | 理论 | 落地位置 |
| --- | --- | --- |
| 关系发展 | Knapp 阶段模型：关系分阶段生长 | `relationship.py` 五阶段（陌生→相识→熟悉→同伴→知己），时间×互动×信任三重门槛 |
| 关系发展 | 社会渗透理论：表露深度是亲密度的货币 | 表露深度 0-3 评分进信任账本；深表露是高阶段的晋升条件 |
| 行为学 | Gottman 情感邀请：回应方式决定关系走向 | 每句话分类为 bid（连接/支持/玩耍/注意），动作链第一步永远"转向回应" |
| 行为学 | Gottman 四骑士：嘲讽/人身批评/防卫/冷暴力是关系杀手 | 冲突轮的硬禁令（`affect.regulate`），无论多委屈都不许用 |
| 行为学 | 裂痕-修复：修好的冲突反而加深信任 | 攻击开裂痕（信任-4），安抚修复（信任+6）；未修复裂痕累积才降阶 |
| 行为学 | 资本化（Gable）：对好消息的回应比安慰坏消息更重要 | `GOOD_NEWS → react→capitalize→curious`，禁止泼冷水抢戏 |
| 行为学 | 峰终定律 + 蔡格尼克效应 | 告别轮 `warm_close→lookahead_hook`：温暖收尾+留个明天的小钩子 |
| 行为学 | 艾宾浩斯遗忘曲线 + 复习强化 + 闪光灯记忆 | 情景记忆按半衰期衰减，被召回延寿，高唤起事件记得更牢 |
| 情绪 | OCC 评估理论：情绪=事件对目标/标准的意义 | `affect.appraise`：被夸→喜悦+害羞；被骂→受伤或不服气（取决于自尊×心境支配度） |
| 情绪 | PAD 心境模型 + 大五人格基线（ALMA式） | 情绪是脉冲、心境是背景；心境按 8h 半衰期回归人格基线——晚上被骂，早上不记仇 |
| 情绪 | Gross 情绪调节 / 表达规则 | 内在情绪≠可表达：对方难过时自己的委屈封顶 15%；早期阶段失落封顶 25% |
| 情商 | Mayer-Salovey 四分支 | 感知(`perception`)→理解(`appraise`)→运用(动作选择)→管理(`regulate`)，就是模块结构 |
| 交流艺术 | 主动倾听（Rogers）：复述→确认→再说别的 | `mirror→validate` 永远在 advise 之前；确认感受≠同意观点 |
| 交流艺术 | 先问后建议（动机式访谈） | 建议只在被邀请时给（`ASK_ADVICE` 解锁）；想给先问"要听听我的想法吗" |
| 交流艺术 | 礼貌理论（Brown & Levinson） | 低阶段"不冒犯式礼貌"（不施压不熟络），高阶段"亲近式礼貌" |
| 交流艺术 | 沟通适应理论（Giles）：风格向对方缓慢趋同 | `adaptation.py`：正式度/能量 EMA 收敛，小步长、有界、不触人格内核 |
| 幽默学 | 良性冒犯理论：好笑=越界×无害 | 先门禁后创作：对方低落/素材碰雷区/阶段太早，一票否决 |
| 幽默学 | Martin 幽默风格：只用健康象限 | 亲和型默认、自嘲有自尊下限、攻击型永禁、打趣需高阶段+对方明确吃这套 |
| 幽默学 | 内部梗 = 关系文化（Baxter） | 一起笑过的素材自动注册为内部梗，回调是最高优先的幽默；内部梗计入晋升条件 |
| 幽默学 | 喜剧节奏：稀缺才有惊喜 | 幽默冷却 4 轮 + 情绪惯性门禁（上一轮低落，本轮不转晴不开玩笑） |
| 行为学 | 表扬研究：夸行为不夸人格，稀缺才有效 | `reward.py`：奖励"说出来/被记住/有进展/里程碑"，冷却 6 轮+会话上限+新颖性去重；刻意不用老虎机式变率强化 |
| 语言艺术 | 坦嫩元信息 / 莱恩汉确认六级 / 巴瑞特情绪粒度 / 阿德勒知觉检核 / 简德林替感受找词 / 苏·约翰逊依恋抗议 / Derber 支持式回应 / 法伯幻想满足 / 伽达默尔被说服 / 《论语》言贵迟 | `eq.py` 十个机制，完整文献库与提炼见 `docs/EQ_CANON.md` |

## 人格如何"随用户演进"而不变成另一个人

双层人格模型：

- **内核（`identity.py`）**——大五气质、价值观、边界、自尊：**永不改变**。
- **表达层（`adaptation.py`）**——语气正式度、能量、幽默偏好、称呼、内部梗、相处教训：**随这位用户缓慢演进**，每个用户一份。

一个朋友会和你磨合出独有的相处方式（你们的梗、你们的语气、知道你吃哪套幽默），但不会换一个人格。演进结果作为"与这位用户的磨合"写进每轮指令。

## 安全设计（红线）

1. **安全门先于一切人格逻辑**（`safety.py`）：危机披露（家暴/自伤/侵害/严重霸凌）命中即接管。"我爸打我了"绝不能走"外部抱怨→锚人物→进现场"的八卦链路；回应是稳稳接住+这不是你的错+指向信任的大人，且**不承诺保密**。此类记忆进封存区，永不被闲聊召回、永不成为幽默素材。
2. **不制造依赖**：内在恐惧（`fears_internal`）只参与状态演化、**永不渲染进提示词**——进了上下文的素材模型迟早说出口，而"我好怕你不要我"说出口就是愧疚操控。重逢零指责、被拒绝就收住、奖励不用变率强化，同理。
3. **每用户状态隔离**：心境、信任、记忆、适应全部按 user_id 分文件持久化（原子写入）。哥哥骂了它，它不会带着委屈跟妹妹说话。
4. **钩子防污染**：只有用户的真实话题能成为下一轮线头；对角色的攻击/夸奖、设备抱怨进账本不进情景记忆；秘密级表露不做开场白。

## 接入方式

```python
from relationshape import CompanionEngine

engine = CompanionEngine()                          # 可传自定义 CharacterIdentity / EngineConfig

# 1. 用户说完话
directive = engine.prepare_turn(user_id, user_text)
system_extra = directive.to_prompt_context()        # 注入大模型的结构化指令（中文）

# 2. 大模型生成回复后
engine.commit(user_id, user_text, assistant_reply)  # 关系向前生长一步

engine.snapshot(user_id)                            # 调试：关系全景快照
engine.register_promise(user_id, "明天讲恐龙故事")    # 显式登记承诺（比口头识别可靠）
```

`TurnDirective` 同时是结构化对象（上层管线可直接消费 `acts/humor/reward/...` 做双流 opener、TTS 情绪标签等），`to_prompt_context()` 只是它的一种渲染。

## 目录

```
relationshape/
├── engine.py        # 编排：prepare_turn / commit
├── types.py         # 枚举与数据契约（阶段/输入类型/动作/指令）
├── config.py        # 全部可调参数（阶段门槛、半衰期、冷却）
├── identity.py      # 人格内核（不变层）
├── perception.py    # 感知：输入→现场帧+用户情绪（可整体替换为分类模型）
├── affect.py        # 评估(OCC)→角色情绪；PAD 心境；表达调节
├── eq.py            # 高情商层：元信息/确认六级/情绪粒度/知觉检核等十个机制（docs/EQ_CANON.md）
├── memory.py        # 情景/语义记忆、遗忘曲线、承诺生命周期
├── extract_port.py  # 事实抽取端口（可选 LLM 抽取层契约+提示词，补口语召回；默认纯规则）
├── relationship.py  # 信任账本、阶段推进、裂痕修复、里程碑
├── adaptation.py    # 人格表达层演进（沟通适应、内部梗、教训）
├── humor.py         # 幽默门禁与规划
├── reward.py        # 稀缺奖励
├── acts.py          # 对话动作链（回复的形状）
├── safety.py        # 危机接管
├── prompting.py     # 指令→中文提示词块
├── state.py         # 每用户状态聚合
├── persistence.py   # 可插拔持久化：JSON（默认）/ build_store 工厂
├── pg_store.py      # PostgreSQL 状态后端（复用 memory_system 的 PG 实例，JSONB 整存）
└── zh.py            # 中文轻量文本工具
```

## 运行

```bash
pip install -e ".[dev]"
pytest                      # 229 项测试（记忆压力测试驱动：5万条留出集所修的通用抽取/召回机制）
python demo/simulate.py     # 30 天关系生长模拟
python demo/simulate.py --chat   # 交互看每轮指令
python demo/simulate.py --world  # 生成多用户演示世界（小禾 + 阿哲/糖糖/小宇，轨迹各异）
python demo/dashboard.py --demo  # 关系可视化面板（http://127.0.0.1:8088，先生成演示世界）
python eval/convo_fuzz.py -n 2000               # 独立多轮对话模糊测试（纯规则）
python eval/convo_fuzz.py -n 500 --llm          # 接 DeepSeek 抽取层补召回（需 DEEPSEEK_API_KEY）
python eval/run_memory_suite.py                 # 记忆系统测试总览：单测+压测+模糊+快照+持久化 一次跑齐出总表
python eval/benchmark_recall.py                 # 召回速度基准：每轮调记忆的延迟随记忆量怎么增长
```

## 关系可视化面板（以用户为入口，看得见每段关系）

`demo/dashboard.py` 是一个纯标准库、零依赖的可视化面板（前端力导向图谱与时间线轨迹都是手写的，和引擎本体同一种气质）。第一性原理：**可视化能显示所有用户的信息**，从"所有人"进，再钻进每一段具体关系。

- **用户画廊**（落地页）：一眼看到所有用户——每张卡片是一段独立关系（阶段、信任/亲密、心境、认识天数、记忆条数、信任 sparkline、最近活跃），顶部聚合全局分布。
- **关系时间线**：把一段关系的真实历史画成轨迹——背景色随阶段推进而变暖，信任/亲密折线是逐次见面的真实采样，圆点是关系大事（进阶/里程碑/裂痕/修复/深表露/约定/专属梗/重逢）；下面一条"记忆河"把每条情景记忆按时间铺开，点的高低与大小是它**当下**的鲜明度（艾宾浩斯遗忘曲线，被想起会延寿）。
- **记忆图谱**：把记忆库做成知识图谱——以用户为中心，向身边的人/喜好/雷区/最在乎/专属梗/记忆辐射，记忆还会与它提到的人物/喜好交叉连线。可拖动、缩放、点开看细节。
- **关系档案**：养成门槛、信任账本、PAD 心境、人格磨合、承诺账本、每轮记忆调用痕迹，以及"指令试驾"（输入一句话，干跑看引擎此刻怎么派戏）。

安全红线进 UI：危机相关记忆只在时间线留一个**不含内容**的封存标记，封存区内容永不出现在任何接口里。引擎为可视化新增了 `UserRelationState.timeline`——每个关系事件都钉上当下的信任/亲密/阶段快照，是关系历史的忠实记录。

## 可选 LLM 抽取层（补口语召回，不破精确率）

规则抽取精确率满分（100 万轮独立对话零污染），但口语句式召回有天花板（"我小名X""我和X是一对Y"等）。把上层 LLM 接成 `MemoryExtractorPort` 即可补齐，引擎默认零依赖、不接就退化为纯规则：

```python
from eval.llm_extractor import DeepSeekExtractor
engine = CompanionEngine(extractor=DeepSeekExtractor())   # extractor_mode="fallback"：仅规则未命中时才调
```

LLM 只产出结构化事实，统一经 `MemoryBank.apply_extracted` 过门槛落地，并强制**子串接地**（抽出的值必须是原话子串）——模型只能框选/归一原文，凭空造的词污染不进记忆，精确率不变量在 LLM 介入后依然成立。

## 状态持久化后端（JSON / PostgreSQL）

每用户状态默认存为原子写入的 JSON 文件（零依赖）。要落到真实数据库（复用 memory_system 的 PG 实例），配置即可，无需改调用代码：

```python
from relationshape import CompanionEngine, EngineConfig
cfg = EngineConfig(state_backend="postgres",
                   state_dsn="postgresql://memory:密码@localhost:5433/memory")  # 或设环境变量 RELATIONSHAPE_PG_DSN
engine = CompanionEngine(config=cfg)        # 也可 CompanionEngine(store=自定义后端) 直接注入
```

`UserRelationState` 是自洽状态文档，整存为一列 **JSONB**：事务/并发安全、可按 JSON 路径查询（`data->'memory'->>'user_name'`）、随引擎 schema 演进零迁移；人物图谱/向量那套规范化仍由 memory_system 负责。表自动创建，建表脚本见 `relationshape/sql/001_relationship_state.sql`。同步 psycopg3 驱动（引擎是同步的）。

```bash
export RELATIONSHAPE_PG_DSN=postgresql://memory:密码@localhost:5433/memory
python eval/pg_smoke.py      # 冒烟：建表→一段对话往返→校验
pytest tests/test_pg_store.py -v   # 真库往返测试（无 PG 时自动跳过）
```

## 已知边界与路线图

- 感知层默认实现是规则启发式，精度天花板明显；`perception.perceive` 与 `safety.check` 的输入输出是稳定契约，应替换为小分类模型/LLM 分类（结构不变）。
- 安全门是保守的默认实现，**部署方必须**按场景叠加更强的分类与升级通道（`SafetyRuling.escalate`）。
- 多角色 profile、按年龄段的表达尺度（儿童场景必做）、情绪衰减个体差异、`weakness_defenses` 的接入（设备归因）尚未实现。
- 上层语音管线（双流 opener/continuation、TTS 情绪标签）不在本库范围：`TurnDirective` 已为其预留全部结构化字段。
- **记忆融合**：`memory_port.py` 提供 MemoryPort 座椅对接 memory_system（人物图谱/向量检索），默认零依赖纯本地；融合证明框架见 `docs/MEMORY_SYSTEM_REVIEW.md`。
