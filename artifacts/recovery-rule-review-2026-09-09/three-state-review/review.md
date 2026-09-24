# 三态目标合同复审

上一轮报告中的具体反例已关闭，包括 consumer 7 例、未知 ignore-glob、claim 错误引用变体、无法绑定的 red side、不同 cwd 的 episode 和第五条 verification 投影。三态结构的改动生效了。

但当前实现仍有 **5 类 P1**：三态判断之前的参数信息丢失、非测试模式被视为验证、路径例外的对象不一致、新 UsageError 豁免的归属错误，以及多 recovery 证据集合混用。下面的反例都通过确认及 ValueGate；这仍不足以支持“所有冲突不可覆盖、未知不升级”的完成结论。

审查只新增此目录下的报告和探针，没有修改生产源码、调用真实模型或写入正式记忆。“可持久化”只指通过持久化准入。parser 与 consumer 的新案例在临时目录运行真实 pytest，再将真实输出和退出状态送入工具 validator、runtime trace emitter 和 ReflectionEngine。multi-recovery 是 parse + validator + ValueGate 的离线证据合同测试。

## T1 · P1：参数个数和引号边界丢失，让不同目标进入 match

位置：[统一 flag lookahead](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1103>)、[递归拆分带空格 token](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:955>)。

这两个行为都发生在三态判断之前，后续收到的合同已经缺失关键差异。

**布尔 flag 吞掉位置目标。** 新分支对所有 flag 都消费下一个非 flag token，`-q` 实际不接收值，其后的测试路径却被当作值吞掉：

```text
pytest -q tests/test_auth.py tests/test_smoke.py --tb=short
→ exit 1，1 failed, 1 passed

pytest -q tests/test_billing.py tests/test_smoke.py --tb=short
→ exit 0，2 passed
```

两侧合同都只保留 `tests/test_smoke.py`，`objective_relation=match`，生成 confirmed、ValueGate 接受。将同一个 `-q` 放到目标之后，负对照正确返回 conflict 并拒绝。未修改 auth 测试或产品代码。

**带空格的选择表达式只保留首词。** `_expand_command_tokens()` 对已经由 shlex 解析成单一参数的文本再次拆分，失去 argv 边界：

```text
pytest tests/ -q --tb=short -k 'test_smoke or test_expired_auth'
→ exit 1，1 failed, 1 passed, 1 deselected

pytest tests/ -q --tb=short -k 'test_smoke and not test_expired_auth'
→ exit 0，1 passed, 2 deselected
```

两侧 selection 都变为 `-k=test_smoke`，不同的后半段进入 bare operands；有路径时这些差异不参与匹配。再次生成 confirmed、ValueGate 接受。

建议：直接保留工具已有的结构化 argv；仅在明确的 shell wrapper 边界解析其脚本文本。按具体程序的参数语义决定消费零个还是一个值，不能以“下一项不以横线开头”代替参数个数。保留完整 `-k`/`-m` 表达式；无法可靠解析时返回证据不足。

复现：[parser_probe.py](parser_probe.py) 的 `boolean_flag_swallows_first_target`、`quoted_selector_tail_disappears`；[完整结果与负对照](parser_probe.json)。

## T2 · P1：--version 被当作展示参数，未执行测试也能确认恢复

位置：[presentation 白名单](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:925>)、[成功状态默认转为 passed](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:3307>)。

`help`、`version` 被视为不影响验证的参数。本轮实际运行失败的 auth 测试后，追加 `--version`：

```text
pytest tests/test_auth.py -q --tb=short --version
→ exit 0，输出仅为 pytest 9.1.1
```

这次根本没有执行 auth 测试，但合同返回 match，verification 被判 passed，生成“使用带 --version 的 corrected test invocation”的 confirmed recovery 并通过 ValueGate。

建议：将查询版本、帮助等模式与执行验证区分；工具适配器应证明实际完成了所声明的验证。展示参数也需要按程序解释，不能对所有可执行程序使用同一语义白名单。直接复现的是 `--version`，没有把未运行的其他 flag 场景算进结果。

复现：`parser_probe.py` 的 `version_is_not_test_verification`。

## T3 · P1：路径例外没有把缺失对象、重定位对象和完整目标集绑定起来

位置：[_proven_path_exception](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1244>)、[任意路径后缀即匹配](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1426>)、[无 input_paths 即默认范围](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1255>)。

真实 pytest 的两个反例：

| 首次失败 | 后续通过 | 实际问题 |
| --- | --- | --- |
| `pytest tests/test_auth.py tests/test_missing.py`，missing 不存在 | `pytest backend/tests/test_auth.py` | 被证明缺失的是 missing，被重定位的是 auth；missing 被直接删除，仍不存在 |
| `pytest tests/test_auth.py`，auth 不存在 | `pytest billing` | billing 是显式选择的另一个目录，被误当作默认范围 |

两例初始 relation 都为 conflict，但路径例外放行，Engine 产出 confirmed、ValueGate 接受。第一例的“任一请求目标缺失”和“任一请求目标重定位”来自两个不同对象；第二例的 bare operand 并非省略了目标。

建议：保留具体 missing target，而非只返回布尔值。证明链应明确记录“哪个目标缺失、它对应成功侧的哪个目标、其他目标如何继续覆盖”；默认范围必须由具体工具输入合同证明。单纯 executable 相等无法提供这些证明。

复现：[consumer_probe.py](consumer_probe.py) 前两例；[consumer_probe.json](consumer_probe.json) 保存真实退出码 `[4, 0]`、输出以及 missing 仍不存在的检查。

## T4 · P1：新增 UsageError 豁免会把嵌套程序的错误套用到外层选择参数

位置：[_invalid_argument_flags_named_by](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1274>)、[从失败合同移除命中参数](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1284>)。

豁免只扫描错误文本中的 `unrecognized arguments`，没有证明拒绝参数的是本次顶层调用的解析器。

本轮临时 pytest 项目注册了合法的 `--run-integration` 开关。启用时会执行集成测试，其中一个 nested-helper 的 argparse 报同名参数不受支持；外层 pytest 已成功接收该参数并执行测试：

```text
python -m pytest tests/test_cli.py --run-integration -q --tb=short
→ 1 failed, 1 passed
→ captured stderr: nested-helper: error: unrecognized arguments: --run-integration

python -m pytest tests/test_cli.py -q --tb=short
→ 1 passed, 1 skipped
```

删掉开关使失败测试被跳过。原始合同明确是 conflict，豁免删除失败侧的 unknown flag 后却确认恢复，并通过 ValueGate。再次运行原范围仍失败。仅将合成 trace 中 nested-helper 的错误短语替换掉，保持调用和其他输出不变，负对照正确拒绝，定位到此豁免。

建议：无效参数修正需要绑定“拒绝参数的解析器/程序、具体 token、验证阶段”；已经进入测试执行阶段的嵌套错误不能证明顶层参数无效。无法建立归属时，应保留原始 scope 冲突或候选状态。

复现：`consumer_probe.py` 的 `nested_usage_error_waives_valid_parent_selection_flag`。真实完整输出、恢复后原范围再失败、错误短语移除对照均在 JSON 中。

## T5 · P1：多个 recovery 的错误并集会拼出不存在的验证链

位置：[先合并所有引用 recovery 的 errors](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:1636>)、[逐 recovery 使用同一并集](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:1650>)。

上一轮从 claim.related_error_ids 改为 evidence 中的权威关联是有效修复，但当前先把所有引用 recovery 的 errors 合并了。它仍未逐条隔离证据关系。

离线证据序列：auth 修复（位置 3）→ billing 检查通过（位置 5）→ billing 修复（位置 6）。auth 没有自己的相关验证，billing 的通过检查发生在修复之前。

| 引用的 recovery | parse | validator / ValueGate |
| --- | --- | --- |
| 只引用 auth | 通过 | 拒绝 |
| 只引用 billing | 通过 | 拒绝 |
| 同时引用 auth、billing | 通过 | 接受 / 可持久化 |

两条恢复各自的完整关系均为 false，同时引用却把 auth 的时间条件和 billing 错误中的 Invoice 共词拼接在一起，触发文本关系 fallback。

建议：在每个 `(recovery, verification)` 的关系检查内部，从该 recovery 自己的关联派生 errors；时间、目标、错误关系必须在同一条证明链上成立，不能分别来自不同恢复。

复现：[multi_recovery_scope_probe.py](multi_recovery_scope_probe.py)、[结果](multi_recovery_scope_probe.json)。未调用真实模型，测试的是模型候选必须经过的 parse、validator 和 ValueGate。

## 已关闭项与验证范围

- 独立运行 16 个相关模块：**369 passed in 3.23s**。没有将此数当作对用户所报全部 780 项的独立复跑。
- 上一轮 consumer 七例均拒绝；旧 `--ignore-glob` 案例冲突并拒绝。
- 旧 claim 加/换/空 error IDs 和 unbound-call 案例全部拒绝，规则相应保留 inferred。
- cwd 后续筛选保留真实重试；第五条 verification 的父、子证据均通过，原两项 P2 关闭。
- 旧探针输出另存为 [previous_consumer_results.json](previous_consumer_results.json)、[previous_parser_probe.json](previous_parser_probe.json)、[previous_validator_episode_probe.json](previous_validator_episode_probe.json)，没有覆盖历史证据。
- 本轮 parser 三个正例均 `relation=match, value_accepted=true`，移动 `-q` 的负对照拒绝；consumer 三个正例均原始 `relation=conflict, value_accepted=true`；multi-recovery 有两组单独引用拒绝的对照。
- 未开展真实模型 shadow、RecoveryLessonAgent 未接线、语义推广评测未完成，均为已披露边界，没有重复列为新缺陷。

复跑命令：

```sh
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/three-state-review/parser_probe.py
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/three-state-review/consumer_probe.py
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/three-state-review/multi_recovery_scope_probe.py > artifacts/recovery-rule-review-2026-09-09/three-state-review/multi_recovery_scope_probe.json
```

16 模块测试命令与上轮 review.md 相同；完整文件名单及本轮源码摘要保存在 validation.json。

## 建议的收敛方向

保留专门子 Agent 提炼经验的设计。自动确认可以先限定在明确支持的工具形态：保存结构化 argv，由工具适配器声明参数个数、选择语义与是否实际验证；例外输出带具体对象的证明；validator 始终逐条核验完整关系链。其余形态留作候选给子 Agent 分析。这样可以在有限、可验证的确认范围内推进 shadow 评估，而不要求当前启发式解析器承担任意 shell 命令的完整语义。
