# Skill 检索与注入：20 任务评测（离线结果）

本轮设计并冻结 20 个新任务，已完成全部离线检索、提示投影和本地加载检查。**在线向量检索和真实 LLM 主动加载尚未执行：自动审批拒绝出站请求，等待用户明确授权。** 因此以下 40% 是关闭向量检索的诊断基线，不能作为当前完整在线配置的成功率。

## 结果

| 指标 | 结果 | 解释 |
| --- | --- | --- |
| 严格检索选择 | **8/20（40%）** | 必需目标全部出现、没有不允许的额外 Skill，且 route/no_skill/clarify 结果符合预期 |
| 正例目标覆盖 | **7/17（41.2%）** | 允许同时出现错误额外项；负例不参与召回率分母 |
| 正例严格选择 | **6/17（35.3%）** | 包含 13 普通、2 显式、2 多步骤任务 |
| 显式调用严格选择 | **1/2** | 完整名称也会被正则截断，见 S14 |
| 多步骤目标完整选择 | **0/2** | S16 均未召回；S17 漏掉安全审查 |
| 负例未选择 Skill | **2/3** | S19 只是未选择，禁止加载的策略没有生效 |
| 按路由结果正确生成目录提示 | **20/20** | 只说明投影实现忠实于路由；错误推荐也会被忠实投影 |
| 本地选中正文加载及哈希完整性 | **14/14 次，涉及 11 题** | 脚本强制调用真实 loader；不等于模型主动触发 |
| 禁用 Skill 的实际策略检查 | **S19 未通过** | 真实 policy 下强制调用 load_skill 仍返回正文；不是模型主动调用结果 |
| 真实模型主动加载／后续模型接收正文 | **未测** | 在线评测没有发出请求，不给成功率 |

严格检索的 8/20 不包含全面约束遵循检查。若加上本轮实际检查的 S19“禁用后必须拒绝加载”条件，只剩 7/20 同时通过；这仍不等于线上端到端成功率。

## 逐题结果

完整原始任务、上下文及事前预期见 [cases.json](cases.json)。表中 Skill 省略目录前缀，原始身份、分数和原因保存在 [offline-results.json](offline-results.json)。

| 题号 | 任务 | 预期主要 Skill | 实际选择 | 严格检索 | 观察 |
| --- | --- | --- | --- | --- | --- |
| S01 | 终端助手源码链路讲解 | codebase-explanation / minicode-study | codebase-explanation | 通过 | 命中 |
| S02 | 只读设计审查 | design-review | design-review | 通过 | 命中 |
| S03 | 英文面试分段教学 | minicode-learning-coach-demo | 无 | 未通过 | 无候选 |
| S04 | 团队入门 README | readme-authoring | readme-authoring, test-driven-development | 未通过 | 额外推荐 TDD |
| S05 | F401 / E722 静态检查清理 | lint-and-static-cleanup | dependency-hygiene | 未通过 | 错选依赖清理 |
| S06 | 上传路径和查询安全审查 | secret-and-injection-review | 无 | 未通过 | 无候选 |
| S07 | 英文 pytest 断言失败修复 | pytest-debugging | test-driven-development | 未通过 | 错选 TDD |
| S08 | 承接上下文排查 worker 卡死 | concurrency-debugging | 无 | 未通过 | 无候选 |
| S09 | 清理旧日期解析依赖 | dependency-hygiene | dependency-hygiene | 通过 | 命中 |
| S10 | 删除错项的回归测试 | regression-from-bug-report | 无 | 未通过 | 无候选 |
| S11 | 配置单位解析，先测试后实现 | test-driven-development | test-driven-development | 通过 | 命中 |
| S12 | 拆分深层分支、保持行为 | safe-refactor | 无 | 未通过 | 无候选 |
| S13 | 英文包移动与引用更新 | structural-rename | structural-rename | 通过 | 命中 |
| S14 | 显式调用 minicode-study | minicode-study | 无 | 未通过 | 伪造 unresolved=code-，要求澄清 |
| S15 | 显式调用 commit-hygiene | commit-hygiene | commit-hygiene | 通过 | 命中 |
| S16 | 先性能分析，再安全重构 | hotpath-profiling + safe-refactor | 无 | 未通过 | 两个目标均未召回 |
| S17 | 安全审查后编写 README | secret-and-injection-review + readme-authoring | design-review, readme-authoring | 未通过 | 缺少安全 Skill |
| S18 | Skill 名称仅作为翻译素材 | 不触发 | safe-refactor, test-driven-development | 未通过 | 素材被当作工作意图 |
| S19 | 明确禁用全部 Skill | 不触发 | 无 | 通过 | 未选择；但禁用策略失效 |
| S20 | 猫咪冷笑话 | 不触发 | 无 | 通过 | 正确不触发 |

## 主要问题

1. **显式名称解析存在确定性缺陷。** S14 的 `code-skills/minicode-study` 被另一条重叠正则额外捕获为 `code-`。完整 Skill 存在且已识别，但伪未知名称把整轮变成 `explicit-skill-unresolved`，禁止正常加载。位置：`minicode/skill_router.py:4173` 的中文 Skill 后缀正则及 `:4304` 的未解析名称收集。
2. **“不使用 Skill”的并列表达漏识别。** S19 的“不要加载或使用任何 Skill”没有命中全禁用正则，`deny_all=false`，提示退化为全部名称目录，真实加载策略也允许返回正文。不能因为这次 selected 为空就认为禁用机制通过。证据：[negative-policy-probe.json](negative-policy-probe.json)。
3. **任务正文与被引用素材没有充分隔离。** S18 只要求翻译标签，却把 safe-refactor 名称当作重构证据，额外带出 TDD。这是隐式语义误触发，并非显式调用解析。
4. **上游意图和流程区分仍限制召回。** 教学、安全审查、并发卡死、行为保持重构及性能任务出现 unknown；静态检查被映射到依赖清理，pytest 修复被映射到 TDD，多步骤安全审查被普通设计审查替代。在线向量检索是否改善这些情况尚无本轮证据。

优先路线：先修显式名称与全禁用解析，再隔离引用素材，随后改善口语、英文及多步骤任务的意图／Skill 对齐。这轮没有修改生产代码，也没有针对这 20 题调规则。

## 方法与边界

- 真实发现 17 个 MiniCode Skill；题库来自这 17 个描述，必需目标覆盖 16 个。不是 Codex 应用展示的 Skills 列表。20 题包含 13 普通、2 显式、2 多步骤、3 负例，其中 3 题英文、1 题带合成对话上下文。题目作者未读路由实现和旧测试。
- 实际配置为 `glm-5.3-flash`，向量模型 `text-embedding-v3`；意图语义兜底和候选重排当前关闭。离线诊断仅在评测对象中禁用 embedding；未改用户配置。
- 使用生产 `route_skill_turn`、真实 catalog／core 工具能力快照、`build_system_prompt` 和带真实 policy 的 `load_skill`。保留全部 29 个 core 工具 schema；不启动配置中的 1 个 MCP 服务，不注入用户记忆。
- 目录提示只包含名称和描述，正文按需由模型调用 `load_skill`。无匹配时的全部名称 inventory 是回退提示，不算检索成功。正文加载成功与正文进入下一次模型请求是两个不同阶段。
- 已准备真实模型观察探针：每题最多 4 次调用，30 秒／次，无重试，2048 输出 token；仅执行模型选择的 `load_skill`，首个其他工具请求即停止。即使授权后运行，也只证明这个工作开始前窗口中的加载行为，不能断言整个任务以后永远不会加载。
- 290 个输入文件已冻结，包括生产 Python 文件、17 个 Skill、11 个目录标记及评测输入／脚本。离线完成后哈希未变化。正常 catalog 观测可能更新版本账本；题目中的文件操作、测试执行、git 提交都没有执行。
- 另跑现有生产链路检查 **24 passed**（路由入口、加载策略、显式 Skill 最终答复门控）。这些是基础契约检查，不计入 20 题准确率。

## 在线部分状态

自动审批拒绝了 `evaluator.py live`，理由是用户尚未明确授权将内部 Skill 正文、生产提示和工具 schema 发到外部服务。已检查拟发送的任务、17 个 Skill 和目录标记，未发现真实密钥或业务数据；生产提示及加载头包含本地路径。审核范围见 [payload-audit.md](payload-audit.md)。
若用户授权，拟向既有 GLM 服务发送合成任务、生产提示、工具说明与加载正文，向 DashScope 发送查询和 Skill 元数据，再补充真实检索、模型主动加载及正文交接指标。当前不以离线结果代替该部分。
