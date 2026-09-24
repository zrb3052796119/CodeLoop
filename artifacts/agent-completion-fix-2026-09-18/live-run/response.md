Partial result: turn_budget_exceeded: model call budget exhausted. Overall task completion is not confirmed.

## 当前进度交接（未完成，未做任何代码修改）

### 已完成的排查（只读，无改动）

1. **定位实现**：`test_runner` 的正式实现在 **`minicode/tools/test_runner.py`**（pyproject.toml 的 `include = ["minicode*"]`、顶层 `tests/test_tools.py` 的导入均指向它）。仓库里另有 `py-src/minicode/tools/test_runner.py` 和 `ts-src/py-src/minicode/tools/test_runner.py` 两个镜像副本。

2. **找到高度吻合的根因**（强假设，尚未复现验证）：`_run()` 中构建 pytest 命令时有这一行：

```python
cmd.extend([str(f) for f in test_files[:10]])  # Limit to 10 files
```

工具自己用 `os.walk` 发现了目录下**全部** 12 个 `test_*.py`（表头还会打印 `Test files: 12`），但传给 pytest 的只有排序后的**前 10 个**，第 11、12 个文件被静默丢弃。失败的用例如果恰好在被丢弃的文件里，pytest 只对 10 个文件跑出 `returncode == 0`，工具以 `success = result.returncode == 0` 判定通过——这与你描述的"目录里有 12 个 test 文件、直接跑 pytest 能看到失败、工具却报通过"完全吻合。且这是唯一决定成败的通路（计数解析只用于展示），所以文件数 > 10 是最可疑触发条件。

3. **顺带发现的次要问题**（修复时应一并处理）：
   - `pattern` 同时被用作文件名过滤和 pytest 的 `-k` 表达式，语义混用；
   - pytest 退出码 5（"no tests ran"）在 `returncode != 0` 下会判失败，尚可接受，但被静默截断文件这一点没有任何提示。

### 计划的修复（尚未写入任何文件）

- 去掉 `[:10]` 截断，把全部发现文件传给 pytest（或直接传目标目录让 pytest 自行收集），并在命令行过长时分批执行、保证任何一批失败即判失败；
- 报告中显式区分"发现的文件数"与"实际执行的文件数"，杜绝静默丢弃；
- 防御性加固：解析出的 `failed/errors > 0` 时强制 `ok=False`。

### 计划的回归测试（尚未编写）

在顶层 `tests/test_tools.py`（已有 `test_test_runner_rejects_paths_that_escape_workspace` 等同类测试）新增：
- 构造含 12 个 `test_*.py` 的临时目录，让排序最后一个文件里有一个失败用例，断言 `test_runner` 判定失败且失败计数 ≥ 1；
- 断言传入 pytest 的命令包含全部 12 个文件（防截断复发）。

### 验证状态：**均未执行**

- 本次回合**没有产生任何已确认的文件改动**（有界观察者确认无变更），上述修复方案与测试都还停留在计划阶段；
- 尚未运行任何 pytest/ruff 检查，根因也还未用最小目录（12 文件、第 12 个失败）实际复现过一次。

### 下一步（按序）

1. 先用临时目录复现 bug，确认 `[:10]` 截断即根因；
2. 修改 `minicode/tools/test_runner.py`（如需，同步两个镜像副本由你决定）；
3. 补上述回归测试；
4. 运行 `pytest tests/test_tools.py`（及全量相关用例）与 ruff，汇报命令、范围与结果。

任务整体未完成，以上仅为排查结论与修复计划，不能视为修复成功。