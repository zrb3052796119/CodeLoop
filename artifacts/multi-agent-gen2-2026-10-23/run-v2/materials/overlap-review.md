# Gen-2 历史题目重叠审计

审计日期：2026-10-23。仅核对可达 Git 历史、旧题登记、公开提示、保留报告/fixture，并执行不接外网的历史代码确定性探针。未运行任何评测模型，未改生产代码，未制造反向补丁基线。本报告不是模型运行预注册提交。

## 判定单位与结论

按 **触发输入、责任边界、故障行为和验收断言** 判断题目是否重叠。更名或拆出旧题一个已公开失败分支仍算重叠；同一生产模块、共享预算对象或曾作为评测宿主使用，本身不构成题目重叠。

旧八题和后续 memory-corroboration 任务的具体缺陷面必须排除。预算候选 `gen2-summary-budget`（上下文摘要遗漏实际发送的工具 schema 费用）与旧视觉 HTTP 绕过共享预算不同；在已保留的旧题登记、提示和报告中未发现它被作为修复任务使用。已用真实父/修复提交证明行为红绿。原八题完整冻结包已删除，因此不能声称对不可恢复的全部历史 oracle 做过逐字节排重。

## 不可重复使用的已知题目

| 历史 ID / 家族 | 不可复用的具体缺陷面 | 证据 |
| --- | --- | --- |
| `real-digest-authority` | 文件摘要缓存只凭相对路径/size/mtime，跨项目或保留时间戳修改后错误沿用旧摘要；权威快照、验证和回写需要重新读字节 | 旧八题 audit-report；10/13 repair summary 的第一项 |
| `real-conditional-adjudication` | `select_b` 带未满足 conditions 仍推进执行/回写；非空条件必须停止，审计回放同规则 | 同上；`tests/test_conditional_adjudication_regression.py` |
| `real-remediation-context` | 通用子代理缺失或非法结果补报从初始消息重建，丢已完成调查/工具证据；非法恢复 note 未验证；短补报覆盖实质回答；补报调用统计缺失 | effectiveness:23；`tests/test_subagent_context_delivery_regression.py`；v16/v17/v18 fixtures |
| `real-concurrent-context` | 并行 task 位置参数漏 sessions/todo，导致 deadline、交互权限和 skill policy 错位；同轮 continuation 丢状态 | effectiveness:35–41、139；`tests/test_subagent_concurrent_context.py` |
| `real-result-ownership` | 兄弟代理覆盖/挤掉宿主预留正式报告槽，结果所有权与封存失效 | 旧八题 audit-report；10/13 summary；result ownership regression |
| `real-async-delivery` | 生命周期状态读取或超时 poll 消费了尚未写进父对话的结果，缺少真正 owner acknowledgement | 旧八题 audit-report；10/13 summary；delivery ack regression |
| `real-vision-budget` | inspect_image 直接 HTTP 绕过共享预算预留与结算；耗尽后仍请求；视觉取消/迟到用量/未知用量归因 | 旧八题 audit-report；10/13 summary；`tests/test_inspect_image_budget_regression.py` |
| `real-profile-recovery` | 坏 profile / symlink 异常中断初始化；自定义只读 profile 缺发布工具；非法 action/status 类型不能保留合法摘要 | 旧八题 audit-report；10/13 summary；profile/report-type regressions |
| `real-memory-corroboration` | rendered memory IDs 与真实验证、用户 accept/correct/reject 绑定；持久反馈计数、可信度排序和 dashboard；同任务 v1–v27 续跑 | retained last-run manifest/results；`failed-candidate-source.zip` 内 PUBLIC_TASK.md |
| 旧 north-star / multi-agent-cost 题 | iso_date、legacy 测试生成、重复正值校验抽取、top_n、chunks 重尾、subtotal 抽取/可变默认参数、safe_divide；只读 architecture/test-risk/data-flow/reliability/change-impact | `tests/fixtures/agent_quality/multi-agent-cost-manifest.json`、north-star manifest |
| 10/21 skill 评测 | skill presel、discovery gate、body-exclusive routing 与 placement 的既有任务集 | 最近四个 skill commits 的说明及保留 live evidence |

旧六格明确重复使用前三题中的 remediation/concurrent/vision，并非独立 holdout：`scripts/run_implementation_progress_live.py:43–46,232`；`docs/multi-agent-effectiveness.md:139`。与其绑定的 V15 handoff、V18 锚点修复、V25 连续上下文等历史 fixture 不能直接改名成为 gen-2 新题；应另证不同触发与验收目标。

## 最近五个真实提交与预算候选

| Commit | Parent | 时间（+08:00） | 判定 |
| --- | --- | --- | --- |
| `e092a9af973360e3239d4d49aeb1adf18e56146d` | `dd8f8eecc7963b6ac71e539aaeef223f93211c4a` | 10-21 19:25:33 | skill placement |
| `dd8f8eecc7963b6ac71e539aaeef223f93211c4a` | `1294c6fd1d07bf36acd3ad126025f71c7e17e6ee` | 10-21 19:25:16 | skill 评测证据 |
| `1294c6fd1d07bf36acd3ad126025f71c7e17e6ee` | `faffefda9caf04761360d17a1bb03e23f09aadf5` | 10-21 19:24:58 | skill discovery gate |
| `faffefda9caf04761360d17a1bb03e23f09aadf5` | `ad20e4a893248a747e3b3ac741c280ded413ff54` | 10-21 19:24:39 | skill directory presel |
| `ad20e4a893248a747e3b3ac741c280ded413ff54` | `9aaa6a434ae0a86766d28f8b1b91529451a31eb8` | 10-19 18:18:43 | accumulated collaboration work，含多个真修复，可按独立缺陷选题 |

这是本地 all-refs 在 10/13 后的全部五个 commit。`ad20` 父为 9/25 的真实 `9aaa`；提交日期晚于 10/13 不证明每个子修复首次评测也晚于该日期。

候选预算题落在既有 `LLMSummaryGenerator.summarize` 公共入口：父版 `_call_model_once` 只预留消息估算，实际 OpenAI adapter 仍发送 tools；修复版将完整请求 schema 计入有限 token 准入，同时保留 provider 实际用量结算和无限预算路径。涉及 `minicode/context_compactor.py` 与复用 helper 的 `minicode/model_call_control.py`。同一汇总提交还修复 curator 的类似问题，但本题限定摘要入口，避免扩大 scope。

相同字节的既有确定性探针 `test_request_schema_auxiliary_budget.py -k compaction`，分别在 `git archive 9aaa… minicode` 与 `git archive ad20… minicode` 上运行：父版 **1 failed / 1 passed**，有限预算仍发起 9531 输入 token 的请求，失败断言为 `calls == []`；参考版 **2 passed**。无导入失败、无缺依赖、无模型 HTTP。该旧 regression 只用于确认历史修复，本次正式 oracle 与公开兼容检查另行冻结。详情见 `summary-budget-precheck.json`。

不推荐把“活动 nested subprocess lease 的 exact receipt 检查”直接当新题：父版只有有限 lease，已有 reserved-token 拒绝可能已覆盖该路径；nullable lease 同次新增，缺少旧 API 同字节失败证明。条件裁决原问题则已经明确是旧题，不能重用。

## 已知缺口与可复核范围

- `docs/archive/multi-agent-retired-2026-10-19/README.md` 明确删除 10/13–18 原轨迹、冻结源码和工作区；`cleanup-result.json` 记录原 `artifacts/three-arm-real-2026-10-13` 删除且无保留文件。
- `git log --all -- artifacts/three-arm-real-2026-10-13` 无记录，`git rev-list --all --objects` 无该路径；不能从 Git 恢复原 tasks.json/manifest/oracle。
- 对 docs、scripts、tests/fixtures、retained artifacts 的 schema-budget/auxiliary-budget/具体函数检索，没有发现摘要 schema 预算作为旧公开修复任务。命中保留 test-signal 源清单和旧宿主源码，不等同模型已做该题。
- 条件裁决、修复上下文、并发上下文的旧目标已有充分正面证据可直接排除；其他新候选仍须逐题比触发、职责和断言，不能仅按模块名一概排除或放行。
- 不可恢复材料、仓库外未登记实验、未存入 Git 的其他环境历史无法确认。本报告只对上述可审计语料负责，不把“未找到”改写成绝对未发生。

主要来源：`docs/multi-agent-effectiveness.md:51–53,139`；`docs/archive/multi-agent-retired-2026-10-19/historical-reports/three-arm-real-2026-10-13/audit-report.md`；同历史目录的 `multi-agent-fixes-2026-10-13/implementation-summary.md` 与 `multi-agent-review-2026-10-13/review.md`；保留 last-run；真实 Git 父子 diff。
