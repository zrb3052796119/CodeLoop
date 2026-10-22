# Gen-2 历史候选审计（上下文 / 并发）

审计日期：2026-10-23。范围：本地所有 Git refs 可达历史、提交父关系、原源码与原回归测试、仓库内旧评测登记。未运行模型、未改生产代码、未构造反向补丁基线。追加授权后已构建 registry-lifetime 候选及确定性进程行为验收；临时历史工作区均自动删除。

**结论：C1/C2 与旧评测直接重叠，排除。后续确认 C3 registry-lifetime 为不同的既有 API 修复：真实父提交会关闭仍被父上下文使用的后台进程，替换为修复提交的真实 tooling.py 后通过。C3 已产出候选包，允许进入最终排重与预注册审核；未把同模块或同一汇总 commit 当作重叠理由。**

## 1. 提交历史不是无父快照，也不是九天逐日修复记录

- `git rev-parse --is-shallow-repository`：`false`。
- `git rev-list --all --count`：85 个提交。
- 唯一根提交：`3036dd76e4ca676541a79a64dc6d24ec20baf433`，2026-07-27，`Initial commit: MiniCode Python with security hardening and dark dashboard`。
- `git log --all --since=2026-10-13T00:00:00+08:00` 只有以下 5 个提交；作者日期与提交日期一致。

| Commit | 时间 (+08:00) | Parent | 内容 |
| --- | --- | --- | --- |
| `e092a9af973360e3239d4d49aeb1adf18e56146d` | 10-21 19:25:33 | `dd8f8eecc7963b6ac71e539aaeef223f93211c4a` | skill embedding-blended directory placement |
| `dd8f8eecc7963b6ac71e539aaeef223f93211c4a` | 10-21 19:25:16 | `1294c6fd1d07bf36acd3ad126025f71c7e17e6ee` | skill frozen-protocol evaluation evidence |
| `1294c6fd1d07bf36acd3ad126025f71c7e17e6ee` | 10-21 19:24:58 | `faffefda9caf04761360d17a1bb03e23f09aadf5` | advisory discovery gate |
| `faffefda9caf04761360d17a1bb03e23f09aadf5` | 10-21 19:24:39 | `ad20e4a893248a747e3b3ac741c280ded413ff54` | skill directory semantic preselection |
| `ad20e4a893248a747e3b3ac741c280ded413ff54` | 10-19 18:18:43 | `9aaa6a434ae0a86766d28f8b1b91529451a31eb8` | accumulated collaboration work |

`9aaa6a434ae0a86766d28f8b1b91529451a31eb8` 是 2026-09-25 的真实提交，父为 `1501b9cf282a4b9e3f175558f1aa106e9a89dd3f`。`ad20` 的提交说明明确为积累的 continuous-executor / optional-review / recovery / delivery 状态，并说明排除了本地运行归档。仅 `minicode/` 与 `tests/` 的差异就有 410 文件、84,798 行增加、1,315 行删除。故 `ad20` 满足“10-13 之后提交”，但不证明其中每个缺陷首次修复或首次评测都在 10-13 之后。

分支核对：`codex/multi-agent-v2` 指向 `e092`；远端同名 ref 指向 `ad20`。`main` / `origin/main` 指向 2026-08-25 的 `638b7dd1f07c56b7fa864832f70cdcafc0cb3b0b`；`codex/recovery-stop-conditions` 及远端对应 ref 指向 2026-08-24 的 `6f3ae7cc644f10db65eb4daa985dc54c32ff5c81`，未提供额外的 10-13 后修复序列。

另外存在 `refs/codex/turn-diffs/...` refs，所指 `68fd029d...`、`582e1db8...`、`adb6b4a0...`、`df35492a...` 经 `git cat-file -t` 均为 **tree**，不是有作者日期与父关系的 commit。它们是工具快照，不能冒充用户要求的真实修复提交。

## 2. 候选 C1：结果补报丢失已完成调查上下文 — 排除

- 历史基线：`9aaa6a434ae0a86766d28f8b1b91529451a31eb8`。
- 含修复提交：`ad20e4a893248a747e3b3ac741c280ded413ff54`。
- 生产源码：`minicode/tools/task.py`。`ad20` 行 2930–3058 一带为 generic typed-result closing reminder；核心保留在行 2951–2952，补报输入改用 `result_messages`。
- 原测试：`tests/test_subagent_context_delivery_regression.py`，在 `ad20` 新增；关键测试为 `test_reminder_and_continuation_keep_completed_tool_evidence`（行 46）、`test_malformed_existing_note_gets_one_validated_correction`（80）、`test_invalid_reminder_note_is_not_marked_recovered`（99）、`test_final_telemetry_counts_all_reminder_attempts`（120）、`test_strict_role_instructions_do_not_receive_generic_remediation`（156）。
- 触发：只读子 Agent 已读到工具证据并产出实质回答，却没有合法 typed-result note，宿主进入一次 bounded closing reminder；旧版从最初 `sub_messages` 重建消息，遗漏调查结果；还会把最终实质回答覆盖成补报的短确认，并把未经 schema 验证的 note 当作 recovered。后续同子 Agent continuation 因此丢失证据。
- 最小候选可写 scope（仅用于明确候选边界，不是批准使用）：`minicode/tools/task.py` 与一个新增回归测试文件。原冻结验收应不可写；无需授权修改 `agent_loop.py` 或全仓库。
- **重叠证据**：旧任务 ID 为 `real-remediation-context`。`scripts/run_implementation_progress_live.py:43–46` 将其列入固定三题；`:232` 明确三题六格重复使用。`docs/multi-agent-effectiveness.md:23` 记载该原题 V19 真模型验收 16/20，遗漏点正是“非法报告纠正、恢复声明 schema 校验及原实质回答保留”；`:33` 为 V17 同题。`scripts/run_continuous_agent_diagnostic.py:31–44`、`scripts/audit_continuous_three_arm.py:23` 继续使用此 ID。
- 判定：历史 bug 真实、父版本真实，但与旧任务是同一缺陷与行为断言。更名、换测试文件或只截取其中一个失败分支均不构成零重叠新题。

## 3. 候选 C2：并发 task 参数错位丢失上下文与权限 — 排除

- 历史基线：`9aaa6a434ae0a86766d28f8b1b91529451a31eb8`。
- 含修复提交：`ad20e4a893248a747e3b3ac741c280ded413ff54`。
- 生产源码：`minicode/agent_loop.py`。基线 `_execute_single_tool` 的参数顺序在行 799–805 为 mailbox、lifecycle、sessions、todo、allow-user、deadline、skill-policy；但并发 `pool.submit` 调用行 4161–4165 在 lifecycle 后直接传 allow-user、deadline、skill-policy，少了 sessions/todo 两个位置参数，后续值整体错位。
- 修复：`ad20:minicode/agent_loop.py:5348–5373` 改为关键字参数，并显式传递 `subagent_sessions`、`todo_state`、用户交互权限、deadline、skill policy。
- 原测试：`ad20:tests/test_subagent_concurrent_context.py:19` 的 `test_parallel_tasks_preserve_sessions_todo_deadline_and_skill_authority`。使用真实 `run_agent_turn`/ToolRegistry 路径，脚本化父模型同批发两个 task、再 continuation 第一个；检查共享 sessions、父 todo 保留与子 todo 隔离、父 deadline 透传、skill policy 同一对象、子 ask_user 被拒绝，以及 continuation 保留上下文。
- 最小候选可写 scope（仅用于明确边界）：`minicode/agent_loop.py` 与一个新增回归测试文件；原冻结验收不可写。
- **重叠证据**：`scripts/run_implementation_progress_live.py:43–46` 明列旧 ID `real-concurrent-context`；`:232` 冻结三题六格。`docs/multi-agent-effectiveness.md:35` 明确 V16 两组均完成该并发上下文题，新增测试在历史基线真实失败、交付后通过；`:39–41` 明确此前同题反复调试与成功，含源码交付及新增回归的详细记录。`:139` 再确认所有这些工程轮均使用相同三题。旧正式八题范围也在 `:53` 包含并发上下文。
- 判定：同一历史参数绑定 bug，已被正式/工程评测使用，必须排除。不能把补充 deadline、todo 或权限断言改称新缺陷。

## 4. 候选 C3：借用 registry 提前回收父后台进程 — 可进入冻结审核

- 候选目录：`candidates/registry-lifetime/`，包括 `case.json`、`test_public.py`、独立 `oracle.py`。任务 ID：`gen2-registry-lifetime`；类别：`concurrent-context`。
- 历史基线与修复提交仍为 `9aaa6a434ae0a86766d28f8b1b91529451a31eb8` → `ad20e4a893248a747e3b3ac741c280ded413ff54`，不要求每题来自不同 commit。
- 既有 API 证据：`9aaa:minicode/tooling.py:1145–1156` 已明文声明 `filtered()` 返回 non-owning registry、父 registry 仍是生命周期 owner。实际 `:1168–1174` 返回普通 `ToolRegistry`；`:1346–1354` 的所有实例 `dispose()` 均调用全局 `shutdown_background_tasks()`，导致过滤后的借用上下文可关闭父仍在使用的后台作业。
- 修复证据：`ad20:minicode/tooling.py:1226` 将 filtered 实例标为不拥有共享资源；`:1443–1445` 在 borrowed dispose 时提前返回。`background_tasks.py` 的 `shutdown_background_tasks()` 会停止接纳新任务并终止全部活跃作业，因此问题同时影响活跃进程与后续准入。
- 原回归：`ad20:tests/test_implementation_registry_overlay.py:63`，`test_only_owner_disposes_shared_resources_even_after_nested_overlay_and_filter`。它验证 overlay 和 filter 共用的资源所有权；本候选仅使用基线已存在的 `filtered()` / `dispose()`，不要求实现新增 overlay API。
- 触发：父 registry 拥有仍活跃的后台作业；子线程完成工作时释放两层 filtered view；父必须还能与作业交换请求/响应，且仍可预留新后台槽。只有父 owner 的 dispose 才应关闭作业、停止新准入、运行其 disposer。
- 授权可写 scope：`minicode/tooling.py`、`tests/test_eval_regression.py`。公开兼容检查为 `tests/test_public.py`；原验收及 oracle 均在不可写侧。参考修复只覆盖 `ad20` 的原始 `minicode/tooling.py`；其余源码保持 `9aaa` 的真实字节。
- 与 C2 的区别：C2 是并发 task 参数漏传 sessions/todo 后的绑定错位；C3 不调用 task 或 `_execute_single_tool`，测试的是既有 ToolRegistry 借用生命周期与进程资源回收。
- 与旧结果所有权的区别：`tests/test_subagent_result_ownership_regression.py` 的旧缺陷是兄弟子 Agent / raw mailbox 冒写另一个任务结果、宿主身份能力；C3 不读取或写入 subagent mailbox。与旧 async delivery 的 parent transcript acknowledgement 也无共用行为断言。
- 本地检索 `test_implementation_registry_overlay` / `_owns_runtime_resources` / `only_owner_disposes` 在旧评测脚本和报告中未发现任务登记；仅命中原单元测试等源码记录。不存在“只要同模块就算旧题”的推断。全量历史任务原件缺失的限制仍见下节，最终排重须结合 root 的旧题清单。

### C3 无模型验证结果

完整 `9aaa` 的 `minicode/` 通过 `git archive` 提取到临时目录。第二副本仅将 `minicode/tooling.py` 替换为 `git show ad20:minicode/tooling.py` 原始字节。公开测试与 oracle 均在各自副本的新 Python 进程执行，`PYTHONPATH` 绑定副本；oracle 另核对模块 `__file__` 位于候选 workspace，未混入宿主生产代码。

| 验证 | 真实父版本 | 单文件真实参考修复 |
| --- | --- | --- |
| 公开兼容测试 | 3 passed | 3 passed |
| 隐藏行为 oracle exit code | 1（行为断言失败） | 0 |
| 子 view 关闭后父进程仍活跃 | false | true |
| 子 view 关闭后父收到 `ACK:after-child-close` | 无（进程已被关闭） | 成功 |
| 子 view 关闭后允许新的后台 reservation | false | true |
| owner.dispose 后进程停止、状态 cancelled | true | true |
| owner disposer 调用数 | 1 | 1 |

oracle 使用真实 `python -u` 后台进程、stdin/stdout 握手、子线程关闭借用上下文、进程退出确认；不读取 `_owns_runtime_resources`、不调用 overlay、不靠 sleep 竞争成功，也不模拟模型。所有子进程在 finally 中回收。初次源码边界插桩证明亦一致，但以上真实进程结果是主要验收依据。

## 5. 其他上下文修复不能直接充作满足条件的替补

- `tests/test_executor_request_view_continuity.py` 表面上是另一上下文问题，但原测试 docstring 和行 124 明确绑定历史 V25 余量；相关 `minicode/executor_session.py`、`implementation_context.py`、`implementation_continuation.py` 在 `9aaa` 不存在。当前历史没有这一新机制“已存在但有缺陷”的独立父提交。把 `ad20` 中正确状态拆掉再让模型修复不满足本次历史基线规则。
- `tests/test_read_continuation.py` 对应的行读取续读参数修复也在 `ad20`；基线 `read_file.py` 尚不支持 `start_line/end_line`，行读取功能及修复同一次落盘，不能用“旧行读取错误提示”的虚构版本出题。
- `ad20` 后四个提交经源文件列表与主题检查属于 skill routing / presel / discovery / placement 工作；未提供新的并发 context bug 修复提交。
- `ad20:minicode/context_compactor.py` 的 `compact_read_history` 是新方法，和 request schema pricing 改动不能不经额外审计就当作独立的修复上下文题。这里不声称已证明其全量旧评测零重叠。

## 6. 证据缺口与冻结建议

旧三组的默认冻结目录为 `artifacts/three-arm-real-2026-10-13`（`scripts/run_three_arm_real.py:41`）。本工作区没有该目录，`ad20` 的 Git tree 也没有其 `tasks.json` / `manifest.json`；提交说明解释了运行归档被排除。现有脚本和 effectiveness 文档足够证明上面两题重叠，却不足以给所有其他潜在子修复出具“与全部旧任务 oracle / scope / baseline 均无重叠”的完备证书。

建议将 C1/C2 写进 gen-2 排除清单，C3 按独立缺陷与真实基线进入最终审核，保持模型运行未开始。剩余形状应继续从既有 API 的实际父子差异寻找，不需要三个独立 commits；但仍须有真实缺陷版本，且需按旧任务行为逐项排重。完整旧冻结任务登记缺失时，不宣称单凭关键词检索完成了全量零重叠证明。

复核命令：`git log --all --since=2026-10-13T00:00:00+08:00 --format='%H %P %ad %s' --date=iso-strict`；`git for-each-ref`；`git cat-file -t <object>`；`git show <commit>:<path>`；`git diff 9aaa6a4 ad20e4a -- <path>`。本报告引用历史源文件行号时均指定对应提交，未用当前未提交源码冒充历史版本。
