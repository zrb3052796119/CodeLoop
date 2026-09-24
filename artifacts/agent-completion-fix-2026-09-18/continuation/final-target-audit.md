# 最终目标审计

审计时间：2026-09-18 14:02:21 UTC。目标目录：`/private/tmp/codeloop-completion-recheck-20260918`。

**结论：REQUEST_CHANGES。当前变更仍有三个已复现的 P2 回归，不能作为通过审查、可合并的完整修复交付。** 本次核对确认目标文件与上一轮真实反例测试使用的文件逐字节相同，因此原有反例结果继续适用；没有重复运行 201 文件测试。

## 版本与状态

当前 `minicode/tools/test_runner.py` SHA-256：

```text
a87b98b17990487d38677d7c968c04f36de8bbaaf8051b3d6a5dcaa83726a28c
```

该值与 `review-probes/results.json` 的 `target_source_sha256` 完全一致。上轮反例的真实进程输出、输入参数、模块来源和代码快照均保留原样，见 `review-probes/report.md`、`review-probes/results.json`、`review-probes/target-test_runner.py.snapshot`。

当前 HEAD 仍为 `638b7dd1f07c56b7fa864832f70cdcafc0cb3b0b`，与上轮一致。本地分支为 `codex/fix-complete-test-discovery`，本地记录的同名 origin 引用指向相同提交。本次没有联网检查远端 PR。

`git status --short`：

```text
 M minicode/tools/test_runner.py
 M tests/test_tools.py
?? .minicode-memory-authority.lock
```

`git diff --cached --name-only` 为空，没有已暂存文件。当前两份修复/测试修改仍未提交。

最近五次提交：

```text
638b7dd docs(readme): represent all runtime contribution tracks
e4e16d4 docs(readme): finalize regression evidence
4f7b53d test(ci): replace timing benchmarks with contracts
209e6ff docs(readme): bind final regression evidence
b2bcfee test(ci): remove short-timeout scheduling races
```

## 三个仍未解决的 P2

1. **超过 200 个文件时丢失请求参数。** `minicode/tools/test_runner.py:191`–193 的目录分支直接返回，绕过第 197–204 行的 `-v`、`--cov`、`-k`。上轮真实生产 helper 探针确认：200 个文件三项参数齐全，201 个文件三项全部缺失。位置：[test_runner.py:191](/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/test_runner.py:191)。

2. **目录回退扩大已筛选的测试文件集合。** 第 235–236 行先按文件名筛选；第 191–193 行却用整个目录替代该显式列表。上轮 201 个 `test_selected_*.py` 通过文件，加一个不匹配文件名的 `test_other.py` 失败测试，实际 tool 得到 `1 failed, 201 passed`；显式 selected 列表得到 `201 passed`。单独补回 `-k selected` 的目录命令仍执行额外文件，因为其函数名也匹配 `selected`。因此这与问题 1 是不同的语义回归。位置：[test_runner.py:191](/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/test_runner.py:191)、[test_runner.py:236](/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/test_runner.py:236)。

3. **成功测试的告警文本被升级为失败验证。** 第 325–326 行用解析计数覆盖真实进程退出状态，但第 65/69 行的正则扫描全部 stdout/stderr。上轮真实 pytest 测试发出 `1 failed historical request; 2 errors already recovered` 告警后通过，进程退出 0，最终 summary 是 `1 passed, 1 warning`；tool 却返回 `ok=False`、`verification.outcome="failed"`，错误计数为 failed=1、errors=2。位置：[test_runner.py:325](/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/test_runner.py:325)、[test_runner.py:65](/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/test_runner.py:65)。

## 附带发现：内置 Git status 解析确有已有缺陷

这是已有工具缺陷，不属于当前 `test_runner.py` 改动，也未在本次展开修复。

- `minicode/tools/git.py:66` 对完整 stdout 调用 `.strip()`，会删除 `git status --short` 第一行有语义的前导空格。当前真实第一行 ` M minicode/tools/test_runner.py` 变成 `M minicode/tools/test_runner.py`。
- 第 78–79 行只看第一列统计，因此该未暂存修改被当成 staged；`??` 未跟踪文件也被统计为 staged。另外，双列同时修改的条目无法计入两个状态。
- 第 92 行仍按原始定宽格式 `line[3:]` 取路径，因此第一行路径少了首字母。

本次用新进程 `-I -B` 导入目标目录中的真实 `minicode.tools.git`，断言导入路径后调用只读 `_run_status`，得到：

```text
Git Status:
  Staged changes: 2
  Unstaged changes: 1

Files:
  [M] inicode/tools/test_runner.py
  [M] tests/test_tools.py
  [??] .minicode-memory-authority.lock
```

与真实 Git 返回的两个未暂存修改、一个未跟踪文件、零暂存文件不符。缺陷级别建议 P2，因为它会误导 agent 对暂存状态和文件路径的判断。位置：[git.py:66](/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/git.py:66)、[git.py:78](/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/git.py:78)、[git.py:92](/private/tmp/codeloop-completion-recheck-20260918/minicode/tools/git.py:92)。

## 操作边界

本次只读目标文件、Git 状态与日志，并调用只读 Git status 工具。没有修改目标代码或测试、没有提交、没有调用模型、没有运行目标测试套件，也没有修改历史反例证据。唯一新增文件是宿主中的本审计文档。
