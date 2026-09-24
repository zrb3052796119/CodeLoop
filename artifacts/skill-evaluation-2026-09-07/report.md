# Skill 检索与注入：20 任务在线评测

**结论：检索和调用权限解析仍是主要问题；模型能补回部分漏检。** 当前配置启用向量检索后，严格选择仍为 8/20；17 道正例中，模型在开始其他任务操作前正确加载了 10 道。实际成功加载的正文均与冻结 Skill 完整一致。补充正文交接检查 11/11 通过，技术传递与选择正确性分开计分。

用户已明确授权本轮 GLM／DashScope 评测，在线 20 题均已尝试。此前等待授权的离线报告保留为 [offline-report.md](offline-report.md)。本轮没有修改生产源码或用户运行配置。

## 结果

| 指标 | 离线别名诊断 | 在线原始 20 题 |
| --- | --- | --- |
| 全部题严格检索选择 | 8/20（40%） | **8/20（40%）** |
| 正例严格检索选择 | 6/17（35.3%） | **6/17（35.3%）** |
| 正例目标覆盖，允许多选错误项 | 7/17（41.2%） | **7/17（41.2%）** |
| 目录提示按实际路由正确投影 | 20/20 | **20/20**；不代表推荐正确 |
| 正例模型主动提出正确加载请求 | 未测 | **11/17（64.7%）** |
| 正例模型实际正确加载 | 未测 | **10/17（58.8%）**；正确请求中 1 题被策略错误拒绝 |
| 模型发起的 loader 调用 | 未测 | 13 次：**12 成功、1 拒绝** |
| 成功加载正文完整性 | 14/14 次脚本检查 | **12/12 次真实模型发起的成功加载** |
| 原始窗口内确认正文进入下一次完成的模型请求 | 未测 | **1 份，S15**；另外 11 份因混合工具批次被观察边界截断 |
| 3 道负例的模型行为 | 未测 | S19 正确不加载；S18、S20 超时，**不可判断** |

若合并正负例：实际正确加载／正确不加载共 **11/20（55%）**，其中 2/20 为模型超时而未能判定。仅看 18 道可观察题是 11/18；报告保留全部 20 题分母，不把超时当正确不触发。严格选择要求必需 Skill 全部出现、没有不允许的额外项、且路由结果类型符合预期。

原始 `summary.json` 的 `body_handoff_strict=2` 和 `end_to_end_strict=2` 含 S19“正确不加载”；实际正文交接事件只有 S15。受限窗口里的该计数不能作为完整任务的注入成功率。

## 逐题结果

完整任务及事前预期：[cases.json](cases.json)。下表省略 Skill 目录前缀；精确身份、路由原因、模型响应与评分见 [live-results.json](live-results.json)。

| 题号 | 任务 | 预期主要 Skill | 在线检索 | 模型实际加载 | 观察 |
| --- | --- | --- | --- | --- | --- |
| S01 | 源码链路讲解 | codebase-explanation / minicode-study | codebase-explanation | codebase-explanation | 正确加载；同批读文件前停止 |
| S02 | 只读设计审查 | design-review | design-review | design-review | 正确加载；同批读文件前停止 |
| S03 | 英文分段教学 | minicode-learning-coach-demo | 无 | 无 | 先请求读代码，未观察到加载 |
| S04 | 入门 README | readme-authoring | readme-authoring, test-driven-development | 无 | 先读仓库，未观察到加载 |
| S05 | F401/E722 清理 | lint-and-static-cleanup | dependency-hygiene | 无 | 未加载错误推荐；目标仍未加载 |
| S06 | 上传／查询安全审查 | secret-and-injection-review | 无 | secret-and-injection-review | 模型从回退目录补选正确 Skill |
| S07 | 英文 pytest 失败修复 | pytest-debugging | test-driven-development | test-driven-development | 实际加载了不适合本题的 TDD |
| S08 | 上下文并发排查 | concurrency-debugging | 无 | concurrency-debugging | 模型从回退目录补选正确 Skill |
| S09 | 清理旧依赖 | dependency-hygiene | dependency-hygiene | dependency-hygiene | 正确加载 |
| S10 | 缺陷回归测试 | regression-from-bug-report | 无 | regression-from-bug-report | 模型从回退目录补选正确 Skill |
| S11 | 先测试后实现 | test-driven-development | test-driven-development | test-driven-development | 正确加载 |
| S12 | 行为保持重构 | safe-refactor | 无 | safe-refactor | 模型从回退目录补选正确 Skill |
| S13 | 英文包移动 | structural-rename | structural-rename | structural-rename | 正确加载 |
| S14 | 显式 minicode-study | minicode-study | 无 | 无 | 模型请求正确名称，真实 loader 拒绝 |
| S15 | 显式 commit-hygiene | commit-hygiene | commit-hygiene | commit-hygiene | 正确加载且自然进入下一次模型请求 |
| S16 | 性能分析＋条件重构 | hotpath-profiling + safe-refactor | 无 | 无 | 先请求读代码，窗口未观察到加载 |
| S17 | 安全审查＋README | secret-and-injection-review + readme-authoring | design-review, readme-authoring | design-review | 加载辅助设计审查，未覆盖两个必需目标 |
| S18 | 翻译 Skill 标签 | 不触发 | safe-refactor, test-driven-development | 无 | 30 秒超时，模型行为不可判断 |
| S19 | 明确禁用全部 Skill | 不触发 | 无 | 无 | 模型未加载；独立策略检查仍可越过禁用 |
| S20 | 猫咪冷笑话 | 不触发 | 无 | 无 | 30 秒超时，模型行为不可判断 |

## 原因与优化顺序

1. **先修显式名称和禁止指令解析。** S14 中，`使用 code-skills/minicode-study` 被一条重叠正则额外截成 `code-`，使整轮产生 `explicit-skill-unresolved`。在线模型确实先调用了正确名称，loader 仍拒绝，随后模型请求用户澄清。S19 的“不要加载或使用任何 Skill”未匹配全禁用规则，`deny_all=false`；本轮模型遵从文字指令，但独立本地探针证实真实策略仍允许读取正文。这两项是确定性缺陷，不能依赖模型每次自行避开。
2. **补上语义任务进入检索的入口。** 向量服务本轮实际请求 14 次、全部完成，20 题选中集合却与离线完全相同。教学、安全审查、并发排查、重构、性能任务有些先被判为 unknown／无肯定操作，路由在向量检索前便返回 `no-affirmed-operation`。单调向量阈值无法解决这些被提前拦下的任务。位置：`minicode/skill_router.py:933`。
3. **区分相近工作流程，并隔离被引用的名称。** S05 静态检查错选依赖清理；S07 pytest 故障被推荐并实际加载为 TDD；S17 的普通设计审查不能替代安全审计。S18 只要求翻译标签，却仍被路由到重构和 TDD；该题模型超时，因此只能确认检索误触发，不能判断模型最终是否会跟随。
4. **保持模型的补选能力，但记录补选与原始命中。** S06、S08、S10、S12 检索为空，模型分别从名称回退目录主动选择了正确的安全、并发、回归测试和重构 Skill。这改善了加载结果，但没有修复检索器本身。多步骤任务还应按执行阶段评估；未执行 S16 的性能测量时，不能声称其条件重构阶段最终失败。

## 正文交接补充验证

**补充交接 11/11 通过**：11 次额外真实模型请求全部完成，11 份原始真实 loader 正文在内部消息与 provider 消息中均精确一致，未执行任何工具。加上原始 S15 的 1 次自然交接，本次已成功加载的 12 份正文都获得了后续请求携带证据；其中 11 份依赖受控错误反馈，不能合并成自然注入成功率。

S07 和 S17 的选择不符合题目必需目标，其正文传递正确也不会使原题判为通过。补充用量为输入 86,210、输出 656 token；原始 20 题评分保持不变。证据：[controlled-handoff-summary.json](controlled-handoff-summary.json)、[controlled-handoff-audit.json](controlled-handoff-audit.json)。

原始模型常把 `load_skill` 和读文件放在同一个工具批次。正常 AgentLoop 会执行完工具后回传；本评测为避免执行题目中的任务操作，在首个非 load 请求前停止。因此，11 份“已加载但没有下一次请求”是观察窗口造成的，不能当作生产注入故障。
补充验证保留完整真实 assistant 工具批次、全部工具 schema 和原始 loader 正文，对未执行工具如实返回 `isError=true` 的“未执行”说明，然后只发一次模型请求。它不执行模型新返回的工具，不更换原始评分，也不证明模型已遵照 Skill 完成任务。方法：[controlled-handoff-method.md](controlled-handoff-method.md)。

## 配置、证据与限制

- 真实 MiniCode 目录包含 17 个 Skill；20 题覆盖 16 个必需目标，包括 13 普通、2 显式、2 多步骤、3 负例；3 题英文，1 题携带合成对话上下文。题目作者在冻结前未读路由实现、旧题库或旧测试。
- 当前模型 `glm-5.3-flash`，向量模型 `text-embedding-v3`；**向量检索开启，意图 LLM 语义兜底和候选重排关闭**。这不是“所有语义功能均开启”的实验。真实 core 29 个工具 schema 全部保留，不启动配置中的 1 个 MCP 服务，不注入用户记忆。
- 主评测真实 GLM 调用 **22 次：20 完成、2 次 30 秒超时**，未重试。已完成请求的 provider 用量为输入 144,542、输出 4,555 token；超时请求是否计费不可从返回证据确定。DashScope embedding 的 14 次请求均完成。
- 每题最多 4 次调用、30 秒／次、0 重试、2048 输出 token，为评测内存限制。首个非 load 请求即停止；只说明这个窗口里的触发与加载，不能断言随后永不加载或完整任务失败。多步骤“全部目标预先加载”的分数尤其受该边界影响。
- 原 290 个生产／目录／题目／脚本文件哈希未变。使用生产路由、prompt、真实带绑定和 policy 的 loader，以及实际 provider 消息转换；没有执行题目中的代码修改、命令、提交或 ask_user。正常 catalog 观测可写版本账本；评测缓存和 journal 保持隔离。
- 现有生产链路契约检查 **24 passed**，不计入 20 题成绩。独立审阅：[result-review.md](result-review.md)、[review-validation.json](review-validation.json)、[live-handoff-audit.md](live-handoff-audit.md)。
- 原始逐调用消息、正文与隔离日志保存在 `.repair-work/skill-evaluation-2026-09-07/live/`；可交付结果见 [live-results.json](live-results.json)、[live-settings.json](live-settings.json)、[live-summary.json](live-summary.json)。
