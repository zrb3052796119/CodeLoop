# CodeLoop 秋招面试深度准备手册

> 基于对仓库代码与实验工件的完整走读整理（2026-09-07）。所有 file:line 均可在仓库中直接翻到。
> 仓库根目录：`/Users/zhourunbo/code/coding agent/MiniCode-Python-main`

---

## 0. 必背：这个项目到底是什么

### 0.1 一句话版本
"CodeLoop 是一个参考 Claude Code 架构的本地 Coding Agent 运行时（Python 实现），我在开源基线 MiniCode-Python 之上，围绕四个方向做了系统性扩展：多层 Skill 路由、自进化持久记忆、上下文压缩保真、中心化多 Agent 写操作流水线，并且给每个方向都配了可复现的评测证据。"

### 0.2 30 秒版本（项目介绍的标准开场）
- 项目形态：终端 CLI/TUI 的本地 Coding Agent，单进程 Python，支持 Anthropic/OpenAI 兼容双协议。
- 核心是一个 Query Loop：模型 → 工具调用 → 结果回填 → 再推理，直到模型给出无工具调用的最终回答。
- 我在基线之上做的四件事：
  1. **Skill 三层路由**：意图识别 → Directory 召回 → Skill 精排 → Top-K 元数据注入 + 按需 load_skill 全文加载，全程确定性可解释，弱证据主动弃权；
  2. **三层记忆**：USER/PROJECT/LOCAL 作用域 × 时间分层，把"验证过的失败恢复"沉淀为可复用教训，配对实验省 79.2% 工具调用；
  3. **分层上下文压缩**：大工具结果外置 + 缓存友好占位 + 结构化摘要 + 预测性触发，5 轮任务累计输入 token -28.7%；
  4. **中心化多 Agent**：写操作强制走 Analyst→Executor→Verifier→Reviewer 契约流水线，快照事务 + digest 审计链，fail-closed。
- 每个方向都有冻结的评测契约或配对实验，CI（3 OS × 2 Python）跑全量 9000+ 测试。

### 0.3 ⚠️ 最重要的诚实边界（面试官翻仓库必问）
仓库 README 和 CONTRIBUTIONS.md 明确写着：
- CodeLoop 是 **MiniCode-Python 的 substantial derivative**，不是 from-scratch；
- 导入的基线已包含主循环、适配器、工具、TUI、压缩、记忆、Skill 路由的**雏形**；
- 你自己的工作是 README "What Changed from the Imported Baselines" 表里的部分。

**标准应对话术**（主动说，别等被戳穿）：
"这个项目的定位是'在开源 Claude Code 复刻基线上做深度工程扩展'。基线提供了 agent loop 和工具的骨架，但它没有真实的路由弃权机制、没有证据回流的闭环、没有配对实验。我做的核心工作是：把确定性的骨架改造成有契约、有审计、可评测的系统——比如 Skill 路由我加了操作约束 veto、显式调用语法、digest 绑定加载和有界证据回流；多 Agent 我把自由派发改成了强制契约流水线。所有的评测体系（冻结 manifest、外部 oracle、统计检验）也都是我搭的。"

**绝对不要说**："从零实现"（CONTRIBUTIONS.md 明令禁止）、"全部代码是我写的"。可以说："我是唯一提交者/维护者，部分 commit 带 AI 辅助标记。"

---

## 1. 架构总览（能在白板上画出来的程度）

```
用户输入
  │
  ▼
意图解析 parse_intent() ──► IntentType/ActionType/子句级 IntentOperation（肯定/否定极性）
  │
  ▼
Skill 路由 SkillRouter.route() ──► routed / abstain / clarify / no_skill
  │   （Directory 召回 → Skill 打分精排 → Top-K 元数据注入系统提示）
  ▼
系统提示组装（静态前缀 + 动态后缀，缓存边界）+ 记忆注入（MemoryPipeline.inject）
  │
  ▼
┌─────────────── run_agent_turn 主循环 (agent_loop.py:2832) ───────────────┐
│  预算预留 → 模型调用 → 解析 AgentStep                                    │
│    ├─ 纯文本(无工具调用) → 最终回答，turn 结束                            │
│    └─ 工具调用 → 调度（并发安全/串行分组）→ 权限门 → 执行 → 结果回填      │
│         ├─ 普通工具：read/edit/bash/task...                              │
│         ├─ task 工具 → 子 Agent（独立上下文/受限工具/轮数上限/摘要回传） │
│         │    └─ workflow → CentralScheduler: Analyst→Executor→Verifier→Reviewer
│         └─ load_skill → digest 校验后加载 SKILL.md 全文                  │
│  每步循环：上下文压力监测 → 预测 → 动态压缩策略                          │
└──────────────────────────────────────────────────────────────────────────┘
  │ turn 结束
  ▼
反思引擎 ReflectionEngine.reflect() ──► claims（错误模式/恢复/根因）
  │
  ▼
记忆管道 MemoryPipeline.write_result ──► 审批(AUTO_APPROVE_VERIFIED/pending)
  ──► PROJECT 层持久化 ──► 下次会话检索注入（闭环）
```

关键 file:line 速查：
| 阶段 | 位置 |
|---|---|
| 运行时组装根 | `minicode/agent_runtime.py:381` `create_agent_turn_runtime` |
| 主循环 | `minicode/agent_loop.py:1867` `run_agent_turn`；while 循环在 `:2832` |
| 工具分发 | `minicode/tooling.py:1077` `ToolRegistry.execute` |
| 单工具执行 | `minicode/agent_loop.py:778` `_execute_single_tool` |
| task 工具入口 | `minicode/tools/task.py:2721` `_dispatch` → `:1837` `_run` |
| 意图解析 | `minicode/intent_parser.py`（9800 行，双语正则 + 可选语义分类器） |
| Skill 路由入口 | `minicode/skill_router.py:833` `SkillRouter.route()` |
| 系统提示 | `minicode/prompt.py:215-341`（Skill 注入格式） |
| 记忆捕获 | `minicode/memory_pipeline.py:1845` `write_result` |
| 压缩触发 | `minicode/context_cybernetics.py:740` `run_cycle` |

---

## 2. Query Loop 与 Tool Use（基础层，一定被问）

### 2.1 两分钟标准答案
"核心是一个 while 循环（`agent_loop.py:2832`）：每步先把上下文长度报给预算管理器做预留（超预算直接抛异常而不是事后计数），然后调模型拿到 `AgentStep`。如果这一步没有工具调用且有实质文本，就是最终回答，turn 成功结束；如果有工具调用，就解析成统一的 `ToolCall{id, toolName, input}` 列表，按'并发安全性'分组调度——只读工具（或声明了 concurrency_safe 的 task 调用）进线程池并发，写类工具强制串行，结果按**原始顺序**回填成 `tool_result` 消息再进下一轮。

工具协议上，我定义了中立的 `ChatMessage`/`ToolCall`/`AgentStep` 类型（`types.py`），Anthropic 和 OpenAI 两个适配器都是裸 urllib 实现的（不依赖厂商 SDK），各自处理流式 tool_use 増量 JSON 拼接、消息历史重放时的格式差异——比如内部格式是一个工具调用一条消息，回放时要按 `assistantTurnId` 重新合并成一个 assistant turn，这是 DeepSeek 思考模式要求的。

安全性上有一套分层权限：DEFAULT/AUTO/BYPASS/PLAN 四种模式，命令有 100+ 危险模式正则分类器，headless 场景没有交互通道时**任何需要审批的操作直接失败**（fail-closed）。还有一个工程细节：Python 杀不掉线程，所以超时放弃策略只用于只读工具；写类工具必须在拥有者线程上跑并自己实现可取消的超时，否则超时返回后写操作还会继续改文件。"

### 2.2 高频追问

**Q: 模型怎么决定停下来？**
A: 模型驱动——一步没有 tool_use 且文本非空即终止（`agent_loop.py:3604-3622`）。其余全是防御层：max_steps 默认 50（子代理 8~15 按类型）、`AgentTurnBudget`（token/调用数/美元三重预算，预留制防并发超支）、单调时钟 deadline、线程取消令牌、每步工具调用数上限 1000。子代理超限走"软着陆"：注入一条"不要再调工具，直接给最终答案"的提示，多花一轮收尾，结果标记 `partial`。

**Q: 工具结果怎么反馈给模型的？**
A: 每个调用生成两条消息：`assistant_tool_call`（带 input 和 turn 分组 id）+ `tool_result`（带 isError）。失败的结果后面还会跟一条 ErrorClassifier/NudgeGenerator 生成的系统提示，教模型怎么修。有一个特殊通道 `_must_deliver`：工具发生了不可逆状态变更后，结果必须原样送达，不允许被遥测/截断吞掉。

**Q: 两个 provider 适配最难的地方？**
A: 历史重放的规范化。内部存储是每调用一条消息，Anthropic 需要合并成一个 turn 的多个 tool_use block；OpenAI 需要函数调用参数重新 json.dumps 成字符串；DeepSeek 思考模式要求重放的 assistant turn 带回 reasoning_content，否则拒绝。另外 thinking block 的签名回放（Anthropic）也是坑。

**Q: 权限系统怎么设计的？**
A: 三层：模式级（AUTO 自动批安全操作/高危弹窗/危险直接拒）、模式持久化（allow_always 写入 pattern 存储，原子写）、会话级临时授权。编辑类还有 fast-edit 预算（小 diff 自动放行）和 7 选项审批（含"本轮允许""带反馈拒绝"——拒绝原因会注入给模型）。

**Q: 为什么不用厂商 SDK？**
A: 可控性——需要取消感知的 sleep、字节上限、受控 socket 读取、以及统一的预算/成本挂钩。SDK 的重试和超时行为不可控。

---

## 3. Skill 三层路由（简历亮点一）

### 3.1 两分钟标准答案
"目标：几十个 Skill 的全文全塞进提示词不可行，但靠模型自由发挥又不可控。我做了三层：

第一层 **Directory 召回**：Skill 按目录组织（`SKILL_DIR.md` 声明目录级 domains/scopes/keywords，子 Skill 继承）。先对意图（IntentType × ActionType × 实体 × 关键词）做确定性打分，选出相关目录，缩小候选池——召回上限 min(16, max(8, topK))。

第二层 **Skill 精排**：每个候选用多维信号打分——意图标签命中 +3.0、关键词 +1.0、实体 +1.5、能力域词 +1.0、目录成员 +1.0、工具亲和度（声明的工具在注册表里且能力域匹配 +0.7，只读任务配了破坏性工具直接 -1.0）、可选 embedding 相似度 +1.5（有熔断，失败回退纯词法）。关键的**准入门**：分数再高，specific_score（意图/目录/实体这类'具体信号'）不达标、或激活词契约不满足、或被否定 veto，都不能进入候选——加分项只能排序，不能创造候选人。

第三层 **Top-K 注入 + 懒加载**：系统提示里只注入选中 Skill 的**元数据**（名字、描述、可能用的工具），全文必须由模型显式调 `load_skill` 加载，加载前校验 turn 级的 SkillLoadPolicy（冻结的 digest 绑定集合）和 sha256 内容摘要，杜绝'路由时是旧版本、加载时换成新文件'的漂移。

误触发防护是双面的：路由侧有否定解析（'不要用 pytest-debugging'→ veto）、约束 veto、弱证据弃权（没有确认的操作意图就不做任何推断路由）；执行侧 Skill 声明的 `required_operations` 与用户否定操作冲突时整轮禁用，即使被显式点名。评测是 60 例冻结集：40 正例 + 20 弃权/陷阱例（'给我讲个笑话''我是记忆棉枕头'这类硬负例），Top-1 准确率 100%、误触发率 0，完全确定性无远程调用。"

### 3.2 分层细节（追问时引用）

**打分公式**（`skill_router.py:3275` `_score_text`，确定性权重）：
| 信号 | 分值 |
|---|---|
| 意图/动作标签命中（置信度≥0.5） | +3.0 |
| 确认关键词（双语别名） | +1.0 |
| 实体命中 | +1.5 |
| 能力域词 | +1.0 |
| 能力 scope 词 | +0.5 |
| 显式点名 | +100（绕过 top_k） |
| embedding ≥ 阈值 | +1.5（信号）/ +0.6（仅排序） |
| 证据回流 | ±0.25（仅排序） |

**弃权语义**（`skill_routing_turn.py:121` `route_skill_turn`）：路由结果分 routed / abstain / clarify / no_skill 四类。abstain 时注入"仅名字清单"模式（无路由证据，只给名字）；clarify 时给名字 + 让模型问一个澄清问题。

**显式授权契约**（防误触发的核心）：
1. Skill 路由只注入元数据，SKILL.md 全文被 `load_skill` 门禁（`tools/load_skill.py:61` 在打开文件前先查 policy）；
2. `SkillLoadBinding{qualified_name, content_digest, routing_identity_digest}` 冻结集合，路由结果编译成 turn 级 policy（`skill_router.py:545`），deny-all/歧义时为空集=整轮禁止加载；
3. 用户显式调用有严格语法：`$skill-name` sigil、"use the X skill"动词+标签语法、中文"使用…技能"，正文里裸出现名字只算相关性证据不算授权（防止叫 `test` 的 Skill 从闲聊里获得执行权）；
4. 引号内、代码块、示例提及都会被 mask 掉再匹配。

**证据在线回流**（`skill_evidence.py` + `skill_feedback.py`）：
- 每 turn 发 `skill.routed`/`skill.loaded`/`task.outcome` 事件进 RunJournal；
- 影子账本扫描 ≤200 runs，构建"加载了该 Skill"vs"路由到但没加载"的对照队列，Wilson 95% 置信区间；
- 调整生效的门槛极高：双侧 ≥20 runs、置信区间不重叠、差值 ≥0.15，且正面调整要求全部验证通过+3 次用户接受+0 纠正；
- 最终只允许 **±0.25 的排序调整**，不能准入/剔除候选、不能打破弃权——"学到的信号只能重排独立获得资格的候选"，这是整个系统最重要的权力边界设计。

### 3.3 高频追问

**Q: 为什么不全用 embedding/向量检索？**
A: 三个理由：可解释（每个选择有 reason 链）、可测试（确定性=可冻结回归）、失败模式可控（embedding 服务挂了词法兜底）。embedding 作为可选增强信号，有 300 秒熔断。
（注意：**没有向量索引/ANN**，embedding 路径是全目录余弦+缓存。别说自己建了 ANN。）

**Q: 误触发率 0 怎么做到的？**
A: 不是模型分类出来的，是契约保证的：(1) 准入门要求具体信号达标；(2) 否定解析共享语法（解析器和路由器用同一份 `intent_negation.py`，不会漂移）；(3) 20 个硬负例专门测"表面关键词像但语义不是"的 case（memory foam pillow ≠ memory skill）；(4) 弃权是默认——证据弱就不路由。

**Q: 60 例 100% 是不是过拟合？**
A: 诚实回答："60 例是**冻结的回归契约**，证明的是'实现与验收点一致且确定性强'，不是泛化估计。我还跑了 96 例联合挑战（96/96 路由决策、84/96 全链路）、110 例野生语言集（76/110 严格通过）、以及一个独立盲测 v4 语料（132 例，80/124 严格通过，存在 37.5% 误 veto）——盲测暴露了泛化缺口，这正是我把'有界证据回流'设计成只能微调排序而不能改变准入的原因：宁可欠路由（abstain），不可误路由。"

**Q: 三层和 RAG 有什么区别？**
A: RAG 是"检索即答案"，这里是"检索只影响提示词里出现哪些指令"，执行权始终在显式 load_skill + 权限系统手里。另外召回信号是结构化的（意图/能力域/工具亲和度），不是纯文本相似度。

### 3.4 坑与禁语
- ❌ 不要说"向量召回/ANN 索引"（实现是词法+可选余弦）；
- ❌ 不要说 100% 是泛化准确率；
- ✅ "Directory" 指 Skill 目录分组（SKILL_DIR.md 继承元数据），不是倒排索引；
- ⚠️ 当前工作树实测 57/60（代码漂移，3 个 database 例挂了），冻结基线 artifact 仍是 100%——如果现场跑，用 `python3 -m scripts.evaluate_agent_quality --profile current` 说的算，要提前知道这一点。

---

## 4. 自进化记忆系统（简历亮点二）

### 4.1 两分钟标准答案
"两个正交维度：**Scope**（USER 跨项目 `~/.mini-code/memory/`、PROJECT 项目共享 `.mini-code-memory/`、LOCAL 项目私有）× **Tier**（WORKING→SHORT_TERM→LONG_TERM→ARCHIVAL，按使用次数和时间晋升/降级）。教训类记忆统一写 PROJECT，注入优先级 LOCAL>PROJECT>USER。

沉淀链路是'证据分级'的：turn 结束后反思引擎先跑**规则合成器**（从结构化执行轨迹确定性提取 claims），LLM 只做补差；claim 按证据强度分级——最高级（优先级 4）要求'根因确认 + 错误事件 + 恢复动作 + 验证通过 + 决策记录'五要素齐全，恢复类（优先级 3）要求错误-恢复-验证三元组。审批策略分三档，其中 **AUTO_APPROVE_VERIFIED**：只有带完整验证链、认知状态 confirmed 的 claim 才自动入库，'模型觉得有用'的一律 pending 等人审。入库后还有 Curator 周期任务做去重合并、对代码库校验路径是否过期、tier 晋升。

检索默认是 BM25 词法 + 结构化信号（tag/领域/scope/tier/时效）的加权融合，纯本地确定性的；语义混合检索（embedding + 三段式 LLM 裁决）是**证据门控启用**的：必须有冻结的晋升证据（holdout SHA、one-shot 回执、验收门全过）才允许加载，任何环节失败直接不注入而不是静默降级。

效果来自一个闭环机制：失败的工具调用+修正+验证通过 → 沉淀为 verified-recovery 教训 → 下次会话检索命中注入 → 注入的执行策略要求'记忆里命名了具体路径时，第一个仓库工具调用必须直接验证该目标'。冷启动路径是'读错路径→list_files 探索×3→读对'≈5 次调用，热路径第一个动作就成功。48 对随机配对实验（16 任务族×3 随机化 block，warm/cold 顺序平衡）：工具调用 -79.2%、输入 token -57.6%、耗时 -54.1%，成功率 100% vs 97.9%；教训创建成本约 3.9 万 token，每次复用省约 2 万，约 2 次回本。"

### 4.2 关键细节（追问引用）

**检索评分公式**（`memory_retrieval.py:2082`）：
```
final = lexical*0.72 + tag*0.10 + category*0.035 + file*0.08 + domain*0.035
      + scope*0.02 + tier*0.005 + recency*0.005 + usefulness*0.005 + corroborated*0.05
scope_score = {PROJECT:1.0, LOCAL:0.7, USER:0.5}
```

**注入控制器**（`memory_injector.py:67`）：反馈式——上下文用量 ≥90% 不注入、≥75% 只注摘要；检索质量高加量、近期失败加量、用户纠正多收紧。默认 5 条 × 200 token。

**注入回执**（重要设计）：`MemoryPromptReceipt` 记录"哪些精确 entry_id 到达了模型"，反馈归因只认真实渲染过的 ID——防止"没注入的记忆背锅/邀功"。

**混合检索三段式**（`memory_hybrid.py`）：Verifier（对象+关系双布尔，≥0.85）→ Challenger（deny-by-default，8 种 veto 理由，≥0.8 才有效）→ Resolver（只裁分歧对，必须引用绑定证据段）；确定性规则优先于 LLM，模型调用上限 8/turn，失败=不注入。

**教训样例**（真实实验产物）：
> content: "After error[not_found]: File does not exist. (config/billing_rules.yaml), the recovery action was: use the corrected read_file invocation `read_file {"path":"services/billing/config/rules.yaml"}`..."
> applies_when: "When read_file fails on config/billing_rules.yaml with not_found."

### 4.3 高频追问

**Q: 79.2% 怎么测的？**
A: "16 个合成任务族 × 3 个随机化 block（种子冻结在 manifest）× warm/cold 两条件 = 48 对，每 block 8 对 warm 先行 8 对 cold 先行（顺序平衡）。任务是只读的'路径恢复'：fixture 预置一个不存在的诱饵路径，warm 条件注入对应教训。成功判定是**外部 oracle** 不是 agent 自报：run 完成 + 任务 outcome + 工作区字节不变 + 成功读取 + 响应含精确 marker + 教训确实被注入（journal 事件）。推断单位是任务族（n=16）不是 48 对（block 是嵌套重复），家族聚类 percentile bootstrap 2 万次 + 精确 Wilcoxon + Holm 校正，p=0.000061，16/16 家族方向一致。"

**Q: 这个数字的边界？（必被追问）**
A: 主动交代："合成只读任务、错误是注入的、相关教训保证存在——它证明的是'**相关且已批准的路径恢复经验能消除重复探索**'，不能外推成'所有 coding 任务省 79%'。我还做了第二研究（非路径类教训，36 对）：命令恢复 -34.8%、抽象项目约束是负结果——效果强依赖教训类别。这个边界我自己写在报告里。"

**Q: 记忆污染/错误记忆怎么办？**
A: 多重防线：写入侧（审批三档，弱证据 pending）、检索侧（Corroborated 信号、安全状态 suspicious/unsafe 不可注入）、反馈侧（失败衰减，只归因渲染过的 ID）、周期侧（Curator 对代码库校验路径时效，stale 标记）。另外 ARCHIVAL 重摘要会强制重新送审。

**Q: 为什么 lexical 是默认，semantic 要门控？**
A: 词法检索有 36 个已确认的语义鸿沟（中文问英文记忆零词重叠等），语义检索确实能补——但 embedding 和 LLM 裁决引入了不确定性和外部依赖。所以设计成"证据晋升制"：必须拿冻结 holdout 上的一次性验收证据才能启用，故障即熔断回词法。这是一个可靠性优先的取舍。

**Q: '2 次回本'怎么算的？**
A: "教训创建 turn 平均输入 39,228 token ÷ 每次复用平均节省 20,320 ≈ 1.93 次；把 run 后反思也算进成本是 2.03 次。这是描述性保守估计——它把整个恢复 turn 全算成记忆开销，而那个 turn 本身也完成了任务。"

### 4.4 坑与禁语
- ❌ 不要说"embedding 检索是默认路径"（默认是 BM25 词法）；
- ❌ 不要把 79.2% 说成普适结论；
- ✅ 主动讲非路径研究的负结果（-34.8% 且约束类无效）——这是加分项，证明你有实验诚实度。

---

## 5. 分层上下文压缩（简历亮点三）

### 5.1 两分钟标准答案
"三层机制 + 一个控制回路。

**外置化**：超过 4000 字符的单个工具结果、或单步累计超 8000 字符的聚合结果，全文原子写入 `.mini-code-tool-results/`（带 JSON 元数据头，7 天 TTL），原位置替换成 ≤500 字符的占位符——头 8 行 + 尾 3 行 + 省略行数 + 磁盘路径 + '需要全文就 read_file 这个路径'。

**缓存友好**：这是行为设计不是 API——替换是**原位替换**（消息不删不重排，prompt 前缀字节稳定，吃 provider 的隐式前缀缓存）；最近 5 个工具结果永不外置（模型正在迭代它们，且还在缓存窗口内）；时间型微压缩有 1 小时门限（避免每 turn 清一次刚进缓存的内容）；系统提示拆静态前缀+动态后缀，日期这类易变内容放边界后面。

**结构化摘要**：全量压缩时，被丢弃的对话按 120 消息/2.4 万字符分块送 LLM 生成结构化摘要（保留顺序：用户任务与约束 > 关键决策**包括被否决的方案** > 已验证事实 > 当前状态；标识符/错误码/路径原样保留），失败退化为确定性清单摘要，再叠一层**确定性关键状态锚点**——正则抽取含'目标/约束/必须/拒绝/根因'的句子和高熵标识符，确保进摘要。这个锚点层是 V1 实验失败的直接修复。

**闭环**：摘要预览（占位符里的头尾行）→ 占位替换 → 按需检索（模型自己 read_file 外置路径）→ 超限兜底（provider 报上下文溢出时：重置熔断器→强制全量压缩→要求压到阻塞线 90% 以下→不行就渐进硬截断 keep_ratio 从 0.4 递减到 0.15，最多 3 次）。

**控制回路**（cybernetics 层）：压力传感器算用量/增长率/加速度+异常检测（突刺/加速/振荡）→ 双指数平滑 + 线性外推预测'还有几轮溢出'→ PID（kp=2.0 ki=0.15 kd=0.3，设定点 0.70）+ 自适应阈值 → 策略选择器按强度映射 微压缩/会话记忆压缩/全量压缩，紧急度>0.9 强制全量 → 压缩效果回流，检测到振荡就在线调 PID 参数。

效果：预注册的 12 对配对实验（4 任务族×3 重复×2 条件，canonical 14k 窗口 vs 对照 128k 窗口，每案例 5 个带历史轮次=120 turn）：provider 实报累计输入 token 4,288,270 vs 6,011,480 = **-28.67%**（配对中位数 -22.65%，精确符号置换检验 p=0.00537，11/12 对偏 canonical），且节省随轮次累积：第 1 轮均值差 4k → 第 5 轮 144k。"

### 5.2 关键细节

**占位符格式**（`context_compactor.py:464`）：
```
[Tool result persisted to disk — {N} chars]
Tool: {tool_name}
Path: {path}
Use read_file with this path if the full result is needed.
--- Preview (first/last lines) ---
（头8行）... (N lines omitted) ...（尾3行）
```

**压缩安全轨**：tool_use/tool_result 对永不拆开；最后一条被丢弃的用户消息**原文重新插入**（"丢任务原话就是压缩后变傻的原因"）；load_skill 对永不压缩；**节流门**——压缩后 token 没降反升则判定失败保留原文；熔断器（连续 3 次失败停主动压缩）+ 消息状态 SHA（同一状态不重试失败的压缩）。

**token 估算**：字符估算（ASCII 4 字符/token、CJK 1.5）+ EMA 用 provider 实报用量校准（α=0.2，钳位 [0.25,4.0]）——观测性校准，不改变压缩决策。

### 5.3 高频追问

**Q: 28.7% 的实验怎么设计的？**
A: "预注册（先冻结设计再看结果，V1 试点失败记录保留在案——一个案例丢了受保护 marker，为此加了确定性锚点层）。对照不是'不压缩的残缺系统'而是 128k 大窗口同 runtime（对照 0 次压缩），唯一差异是压缩与否。token 用 provider 实报值（journal 的 model.completed 事件），不是我方估算。"

**Q: 压缩会不会伤质量？**
A: "严格成功率 9/12 vs 10/12（差的全是远端 IncompleteRead 网络错误，可执行测试和 marker 全过），McNemar p=1.0 不显著；canonical 多用了 9.7% 模型调用。我的表述边界写在报告里：**支持 token 效率，不支持质量中性结论**。确定性机制层另有 60 例压力测试（1-5 轮压缩×4 种历史规模×3 种 marker 布局）100% 保留 marker/最新用户消息/工具对完整性，中位本地估算 token 降 92%。"

**Q: Prompt cache 具体怎么受益的？**
A: "没有用 Anthropic 的 cache_control 断点（可以直说），靠的是前缀字节稳定性：原位替换不重排、近期保护窗、1 小时门限、静态/动态提示边界。提供商的前缀缓存是隐式的，前缀稳定就能命中。"
（如果被追问"为什么不用 cache_control"：单 provider 专属 API 会破坏双协议中立性；且行为层优化对两个 provider 都生效。）

**Q: 预测怎么做 的？**
A: 双指数平滑（α=0.25）平滑用量和增长率，线性外推 `turns_to_overflow = ceil((0.95 - usage)/growth)`，安全边际 3 轮，紧急度分四档。本质是简单的指数平滑+外推，不吹成机器学习。

### 5.4 坑与禁语
- ❌ 不要说"用了 Anthropic prompt caching API"；
- ❌ 不要说"压缩对质量无影响"（只能说未 Establish 差异）；
- ⚠️ `memory_blob_store.py` 是记忆子系统的大对象存储，**不是**工具结果外置（外置是 `.mini-code-tool-results/` 平面文件）——别混。

---

## 6. 中心化多 Agent 协作（简历亮点四）

### 6.1 两分钟标准答案
"设计原则：**模型可以建议，但不能拥有派发权**。主 Agent 通过 task 工具派发子 Agent，四种子类型（explore 只读 12 轮 / plan 只读+评审 8 轮 / general 全工具 15 轮 / workflow 编排器），各自独立上下文（全新消息列表，不继承父 transcript）、受限工具集（注册表重建而非拷贝，子代理还拿不到 task 工具所以不能递归，深度上限 2）、轮数上限+单调时钟 deadline（嵌套取 min）、结果只回传结构化摘要（头信息+8000 字符内终稿+严格 JSON 契约报告，格式坏了降级为 fallback 状态，绝不编造）。

**写操作强制契约流水线**：生产 loop 里直接派 general 做状态变更会被拒绝（`error[central_scheduler_required]`），必须走 workflow。CentralScheduler 是一个确定性状态机：从不可变 TaskProfile 路由出阶段链，默认写操作 = Analyst→Executor→Verifier→Reviewer，高风险插人工门。每个角色的产出都是版本化契约文档（Assignment.v1 / ImplementationOrder.v1 / VerificationResult.v1 / ReviewVerdict.v1），调度器逐阶段校验权威位——**Executor 必须产出 patch_digest，Verifier 必须回显同一个 digest，Reviewer 必须回显且 approved**，任何 mismatch 阶段即失败。

**隔离事务快照**：执行前把工作区 copytree 到临时目录（**故意不用源仓库的 git**——避免仓库持有的 clean/filter/fsmonitor 钩子在宿主执行），在临时目录 init 一个独立 git（空 hooks、禁系统配置），基线 commit 的 ls-tree sha256 是 baseDigest。整条链是七个 digest 的哈希链：objectiveDigest→requestDigest→baseDigest→patchDigest（git diff 的 sha256）→verification/review digest→commit-ready 文档（绑定全部七个，其 sha256 又是 commitReadyId），审计 manifest 逐 artifact 记 contentSha256 且**最后写入作为提交标记**。

**fail-closed**：任何阶段失败/超时/契约无效 = `status=failed, committed=false`，回滚就是把临时目录扔掉（父树从未被碰过）。commit 前必须先持久化 commit-ready 记录——'没有落盘的 commit-ready 就不允许改父工作区'。promote 时三态模型：apply 前设 unknown 哨兵，中断后精确对账（父树等于目标=完成；等于原快照=未应用；否则保持 unknown 并如实报错让用户检查）。

**子进程级隔离**：legacy 写权限 general 子代理跨进程跑（`python -m minicode.subagent_worker`，独立进程组），超时/取消时 SIGTERM→宽限→SIGKILL 整个进程组；杀掉后子进程迟到的结果文件**故意忽略**——'被杀的进程不能事后改写终态'。生产 workflow 的 Executor 则是快照事务隔离+作用域权限对象（冻结工作区、序列化边界内跑不了的东西本来就不给）。

**并发写冲突双层**：静态预检（进程内 WorkflowScopeRegistry，声明写路径双向词法包含即冲突，快照之前就拒绝，不烧预算）+ 基线校验（promote 时对父树重哈希对比快照时 digest，不匹配=重跑；结构化 workflow 只校验声明 scope 内）。

**验证器是无模型的**：DeterministicVerifier 只执行封闭语法的命令（pytest/ruff/mypy/npm test 等白名单形式），可执行文件只从宿主 PATH 解析且拒绝工作区内的，macOS 上套 deny-by-default 的 Seatbelt profile（禁网络、禁 fork、读写限定在验证目录），超时 SIGKILL 进程组，每个 check 用快照的**全新克隆**跑——测试的副作用永远碰不到待提交的 patch 字节。

质量门：12 用例真实模型（glm-5.3-flash，每用例独立子进程、20 分钟墙钟、100 次模型调用预算），5 个并行只读 fan-out + 3 个直接派发拒绝契约 + 4 个完整 workflow 链，全部通过（57/57 外部 oracle），外加 token/调用数/时长上限阈值。"

### 6.2 高频追问

**Q: 为什么 Analyst→Executor→Verifier→Reviewer 四段？能不能省？**
A: "调度器按风险路由：只读简单任务 parent_only 不派；低风险明确 scope 的变更走 EXECUTOR→VERIFIER 两段；默认走全链；高风险在 Analyst 后插人工门。分段的实质是**权限最小化**——Analyst/Reviewer 是只读角色，只有 Executor 有写权限且工具白名单只有 edit/patch/write/read（没有 task、没有 git、没有 web）；每个角色拿到的是恰好够用的上下文。"

**Q: digest 审计链解决什么问题？**
A: "防止'验证的和提交的不是同一份东西'。Verifier 验的是快照 A 的 patch X，如果 Executor 之后又改了文件，回显的 digest 就对不上，阶段直接失败。commit-ready 把七个 digest 绑成一份不可变文档，最后写盘的 manifest 是提交标记——崩溃恢复后能证明'到底批了什么'。"

**Q: fail-closed 具体指什么？**
A: "三类：阶段失败（默认不提交）；崩溃（临时目录丢弃，父树零接触）；promote 中断（三态对账，unknown 如实上报而不是猜）。甚至 finalization 失败也返回 `workflow_finalization_incomplete` 但 changesCommitted=true——不撒谎说没提交。"

**Q: 12 用例里 3 个是'拒绝契约'，这算任务吗？**
A: 主动说："那 3 例测的是安全属性——生产环境必须拒绝绕过调度器的直接写派发（零子代理、零写入、工作区字节不变），它们和 4 条完整链、5 个 fan-out 一起构成覆盖'该并行的并行、该串行的串行、该拒绝的拒绝'的行为契约。链内子角色成功率是 26/30（角色级重试兜住），门本身 12/12。"

**Q: 为什么 Verifier 不用模型？**
A: "验证是权威环节，模型输出不可复现也不能担责。确定性验证器封闭语法+PATH 沙箱+Seatbelt，能证明'这条命令在干净克隆上以这个退出码跑过'。模型的判断留给 Reviewer（建议权），不给提交权。"

### 6.3 坑与禁语
- ⚠️ **准确表述隔离机制**：子进程隔离是 legacy general 写代理的；生产 workflow Executor 是快照事务+权限对象隔离。被问"写权限子代理子进程级隔离"时要能说清这两层（简历这句话对应的是前者）。
- ❌ 不要说"用了 git worktree"（是 copytree 快照+临时目录内独立 git init）；
- ✅ 最强的三张牌：digest 一致性门（三角色回显同一 patchDigest）、七 digest commit-ready 先落盘再动父树、被杀进程不能改写终态。

---

## 7. 数字防御手册（简历上每个数字的出处与口径）

| 简历数字 | 出处 | 口径（被追问时背这个） | 防御要点 |
|---|---|---|---|
| 路由 Top-1 100% / 误触发 0 | `artifacts/agent-quality-baseline.json` `skillRouting` | 60 例冻结集（40 正例+20 弃权/硬负例），确定性无模型，CI 用 `equals` 钉死防漂移 | 是回归契约不是泛化估计；盲测 v4 80/124 是已知缺口，已用"证据只影响排序"的边界设计兜底 |
| 工具调用 -79.2% | `artifacts/persistent-memory-large-study-v3/` | 复用阶段仓库工具调用 50 vs 240；家族聚类 CI 76.1-81.3%，Holm p=0.000061 | 16 族×3 block×2 条件=48 对；外部 oracle；推断单位是族 |
| 输入 token -57.6% | 同上 | 652,911 vs 1,539,738 task 输入 token | provider 实报 |
| 耗时 -54.1% | 同上 | 150.6s vs 327.7s 均值 | CI 49.2-58.0% |
| 成功率 100% vs 97.9% | 同上 | 48/48 vs 47/48；唯一冷失败是自报成功但 marker oracle 抓出 | 反而证明外部 oracle 有效 |
| 教训约 2 次回本 | 同上 analysis-report | 39,228 ÷ 20,320 ≈ 1.93；含反思 2.03 | 描述性保守估计 |
| 累计输入 token -28.7% | `artifacts/context-compaction-paired-v2/` | 4,288,270 vs 6,011,480（aggregate）；中位数配对 -22.65%，p=0.00537 | 12 对/24 案例/120 turn；预注册；对照是大窗口非残缺系统 |
| 节省随轮次累积 | 同上 stats-appendix | 轮均值差 4,035→47,274→77,332→129,471→143,601 | 第 4 轮起显著 |
| 12 用例质量门 100% | `tests/fixtures/agent_quality/multi-agent-cost-baseline-results.json` | 12/12 通过、57/57 oracle；真实模型 glm-5.3-flash | 3 例是拒绝契约；链内角色 26/30 |

**通用防御姿态**：这个仓库的可信度恰恰来自它的诚实——失败尝试全部保留（压缩 V1 试点失败、确认实验 attempt 1/2 失败记录）、预注册、冻结 SHA、CI 钉 digest、CONTRIBUTIONS.md 有"不支持的主张"清单。面试时主动展示这套方法论，比数字本身更打动人。

---

## 8. 高危问题 Top 10（提前演练）

1. **"这项目是你一个人写的吗？/ 和 MiniCode-Python 什么关系？"**
   → 用 §0.3 话术：开源基线 + 我的四大扩展 + 评测体系。主动、简短、不慌。
2. **"100% 准确率太好了吧？"**
   → 确定性系统 + 冻结契约 + 盲测缺口 + 边界设计。主动给出 80/124。
3. **"79.2% 是合成任务，真实场景呢？"**
   → 承认 + 讲非路径研究（-34.8%、约束类负结果）+ "类别依赖"的结论。
4. **"压缩会不会丢信息导致模型变傻？"**
   → V1 失败案例 + 确定性锚点修复 + 60/60 压力测试 + 质量中性的诚实边界。
5. **"多 Agent 为什么不直接用 function calling 让模型自己编排？"**
   → 权力边界：模型建议/调度器决定；写操作是安全关键路径，需要确定性状态机和 digest 链。
6. **"记忆和 RAG 有什么区别？"**
   → 记忆是写入-审批-反馈闭环的资产（有 provenance/生命周期/证据分级），RAG 只读外部语料；检索只是记忆系统的一环。
7. **"PID/控制论是不是过度设计？"**
   → 承认是简单工程控制（EMA+外推+阈值+三参数 PID），价值在于把'什么时候压'从静态阈值变成闭环；ablation 有开关可测。
8. **"为什么 Python？性能问题？"**
   → 单进程 IO 密集（模型调用是瓶颈），Python 足够；真正的硬实时问题（杀线程）用子进程+进程组解决。
9. **"如果给你现在的系统重构机会，你会改什么？"**
   → 好答案素材：agent_loop.py 5300 行该拆；路由泛化（盲测缺口）需要真实分布数据；记忆需要 hard-negative/冲突记忆评测；压缩的 cache_control 断点值得在 Anthropic 单端做。
10. **"AI 辅助写的代码，你怎么保证你都懂？"**
    → "每个模块我都做过代码走读并写了评测契约；能背出打分公式、digest 链、统计口径——你可以随便挑一段问。"（然后真的要能，本手册就是为此）

---

## 9. 演练路径建议

1. **第一天**：背 §0 三段话 + §1 架构图（白板默画）。
2. **第二天**：模块深挖——每天两个模块，把"两分钟标准答案"念熟，再过一遍对应的高频追问。
3. **第三天**：数字防御——对着 §7 表格，每个数字给自己讲一遍"怎么测的、边界在哪"。
4. **第四天**：高危问题模拟——找人对着 §10 提问，或让我扮演面试官。
5. **面试前夜**：只看 §0.3（诚实边界）、§7（数字口径）、§8 第 1/2/3 题。

## 10. 深挖入口（本手册之外）

- `docs/PORTFOLIO_CASE_STUDY.md` —— 作者自己写的 3 分钟记忆案例（含诚实边界 H 节），面试口径的最佳参考
- `docs/agent-quality-gates.md` —— 所有质量门的阈值定义
- `CONTRIBUTIONS.md` —— "支持/不支持的主张"清单
- `.acceptance-work/skill-routing-final-v4/acceptance-report.md` —— 盲测未通过的验收报告（知道它存在，被追问泛化时引用）
