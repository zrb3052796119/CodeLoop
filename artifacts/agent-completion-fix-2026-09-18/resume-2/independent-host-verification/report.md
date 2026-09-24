# 本轮宿主改动独立审查与验证

结果：**299 passed in 4.39s**；最终 18 文件范围的 CI 式 Ruff、源码编译及 `git diff --check` 全部通过。未发现本轮两个生产改动需要阻断的问题。集成检查记录的 17 个文件及最终静态检查范围的 18 个文件，在各自检查前后 SHA256 均完全一致。未读取或修改正在实测的目标仓库，未发起模型或网络请求。

## 明确范围

测试范围严格为上轮保存的 12 个模块，加本轮 `test_primary_model_timeout_recovery.py`、`test_git_status_regressions.py` 和现有 `test_model_adapter_call_controls.py`，共 15 个模块。完整文件清单及 argv 在 `scope.json`；可直接重现的命令在 `pytest-command.txt`；未经裁剪的 stdout 在 `pytest.txt`。解释器为宿主 `.venv-acceptance/bin/python`。

这是一轮明确的新集成范围，不能描述为重跑旧的“353 项”。两项新模块共 20 项：timeout 13 项、Git 7 项。adapter 控制回归加上此前 223 项，形成此次 299 项。

手工审查只比较 `resume-2/before` 快照与当前 `minicode/agent_loop.py`、`minicode/tools/git.py`；所审 diff 已保存为 `agent_loop.reviewed.diff`、`git.reviewed.diff`，避免把项目既有大量未提交改动都算作本轮修改。

## 超时恢复行为

- 恢复仅适用于启用 bounded completion 的主 Agent。全轮最多一次自动超时重试，成功模型步骤不会重置这个额度；旧模式和子 Agent 保持原来的失败行为。
- `continue` 回到正常循环和预算准入，每次尝试都会计入同一预算。失败 reservation 通过 `charge_estimate=True` 结算输入估算，并释放预留；重试没有替换预算、重置计数或绕过 `preserve_model_calls`。
- 第二次超时、工作步数已达上限，或重试预算拒绝，都会用宿主部分摘要收尾；不额外请求模型解释服务商失败。`model_timeout_retry_pending` 防止预算/步数路径偷偷消耗原本预留的总结调用。
- 超时后先记录 `model.failed`，再检查 cancellation/deadline。取消和 deadline 异常继续向上抛；`ModelCallDeadlineExceeded` 在 `_model_next` 已转换为 turn deadline 异常，不会被当作可重试 transport timeout。
- 重试使用已有对话中的完整工具结果，不重新遍历上一工具批次。测试覆盖真实写文件、验证回执和必须交付的 committed receipt，确认宿主恢复路径没有重放它们。该保证不等于外部操作“永远恰好一次”：模型后续主动提出的新工具调用仍属于新行为。
- 部分流式文本可以已经展示给客户端；失败响应不会被加入成功 assistant final。再次超时后的 final 带 `_partial`，typed outcome 为 unknown / goal_achieved false。当前测试也显示重试可再输出相同流式片段，客户端展示去重不是这次补丁实现的能力。
- 两次失败事件与 started 事件按 operationId 配对，重试得到新的 operationId，metrics 的 turn 会正确结束。测试没有把模拟 provider 的调用说成一次真实远端恢复成功。

预算限制的既有边界没有改变：超时输入是估算，未知输出/费用不是零；费用 guard 依赖已结算金额，真实模型输出可令最终结算超过入场阈值。此次恢复未消除这些边界，也未额外关闭控制。

## Git 状态行为

`_run_git` 从 `.strip()` 改为只剥离结尾换行，保留首行 XY 状态列前导空格，因此首个未暂存 `alpha.py` 不再变成 `lpha.py`。`status --porcelain=v1` 给出稳定机器格式；X / Y 分列统计，同一 `MM` 或 `RM` 条目可同时计入暂存区和工作区，`??` 单独统计未跟踪项。

7 项回归使用真正的临时本地 Git 仓库和 Git 子进程，隔离继承的 Git 环境、禁用 hooks/签名，无网络。覆盖首行未暂存路径、未跟踪项、混合状态、暂存改名后再编辑、只暂存及干净仓库。`_run_git` 被其他只读操作复用，保留前导空白不会改变其返回码或 commit 权限检查。

状态输出仍保留 Git 的路径转义/目录条目语义及最多展示 30 条的既有界面；这次未声称增加特殊文件名解码或完整文件枚举能力。

## 同一测试反证核对

本次独立检查核对了已保存 proof 的测试字节，未重复执行已匹配的 before/after：

- `../timeout-regression-proof.json`：当前 timeout 测试 SHA256 `d37984380bca503c874bc0fd14fb558a6ca12e8a99b9fb2890ad1d8e39fda3eb` 与最终 proof 一致；旧源码 10 failed / 3 passed，修复版 13 passed。初始 `timeout-red.json` 属于较早测试版本，不用它冒充当前字节证明。
- `../git-status-proof/proof.json`：当前 Git 测试 SHA256 `e0712d447130ac6e01f13c5ee0fc5ea4ffddf64210e820bc5a11dcbcfe954bf3` 与 proof 一致；旧源码 5 failed / 2 passed，修复版 7 passed。
- `prior-proof-hash-check.json` 保留上述核对结果；此次 299 项是本子任务另行执行的独立集成证据。

## 静态检查及证据

Ruff 使用仓库 CI 规则 `--select=E,F --ignore=E501`。初次静态检查针对两个生产文件及两个新增测试模块；主线程随后明确最终统一范围为 18 文件，因此补全为此前 8 个生产文件 + 7 个任务测试 + `minicode/tools/git.py` + 两个本轮测试，三个静态命令均已在该最终范围通过，没有重复执行 299 项测试。初次 4 文件结果保留为 `initial-*`。

compile 使用 Python `compile` 编译这 18 个文件内容，不生成产物；不是 mypy/pyright 类型检查。diff check 只针对这 18 个任务路径；新增测试另由 Ruff 和编译验证。静态 18 文件与测试 15 模块是不同集合，文件数不应相加解释成 33 个唯一文件。

- `pytest-result.json`、`pytest-command.txt`、`pytest.txt`：准确测试命令、退出码与完整输出。
- `static-results.json` 及各 `*-command.txt`、stdout/stderr：准确静态检查命令和结果。
- `hashes-before.json`、`hash-comparison.json`：集成阶段 2 个生产文件 + 15 个测试模块共 17 个文件前后字节未变。
- `static-hash-comparison.json`：最终 18 个静态检查文件前后字节未变。
- `scope.json`、两个 `*.reviewed.diff`：此次实际审查范围。

这轮结果证明本地确定性行为和集成兼容性；实际服务商能否恢复、模型是否完成目标修复、是否成功创建 PR，仍以正在运行的真实 CodeLoop 证据独立判断。
