# CallObjective 重构复审

结论：统一合同的结构已落地，上一轮反例也确实修复，但“所有确认路径已经统一”“不确定形态只会降级为候选”目前不成立。本轮确认 **4 类 P1 问题、2 个 P2 问题**。P1 都有通过确认和持久化准入的反例，不能仅作为召回率边界处理。

审查当前工作区源码；没有修改生产代码，也没有调用模型提供方或正式记忆写入。下文“可持久化”指通过 `ReflectionValueGate`，不代表已经写入数据库。两组反例在临时目录执行真实 pytest 获取输出，其余使用合成 ToolResult 或明确构造的 TaskEvidence。

## R1 · P1：共享合同仍不是确认恢复的必经门槛

位置：[主抽取分支](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1995>)、[目标截断](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:2817>)、[generic fallback](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:2377>)、[默认范围例外](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:2368>)、[重定位例外](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:2459>)。

以下每个案例中，共享 `objectives_same_target()` 都返回 false；真实工具输入 validator → runtime trace emitter → ReflectionEngine 却仍产出 `confirmed`，且 `value_accepted=true`：

| 确认通道 | 反例 | 绕过原因 |
| --- | --- | --- |
| `_derive_recoveries` | 失败运行命名六个测试文件，中间修改 README，成功侧删除第六个 auth 测试 | 仍比较 `item.target == failure.target`；target 只有前六个 token（命令名加五个文件），第六个文件丢失 |
| `_derive_recoveries` | 五个测试文件之后，`-m=expired` 改为 `-m=valid` | selection 附加在 token 末尾，也被截断 |
| generic fallback | `pytest tests -m=expired` → `pytest tests -m=valid` | `tests` 被归入 bare operands，无路径即允许 token overlap 覆盖明确的 selection 冲突 |
| generic fallback | `dbtool migrate auth -m=expired` → `-m=valid` | `ambiguous_selection=true` 同样能被 overlap 覆盖 |
| missing-target/default scope | `pytest tests/test_auth.py` 报目标缺失 → `echo done`，输出 `done` | 无输入路径直接当作默认范围重试，未限制 executable 或具体工具语义 |
| missing-target/relocation | `tests/test_auth.py -m=expired` → `backend/tests/test_auth.py -m=valid` | 重定位例外同时放宽了 selection |

主抽取分支错误生成的 `verification_call_ids` 又在 [共享验证器](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:611>) 被直接接受，所以下游没有兜住错误归属。

建议：将所有自动确认分支收敛到一个不可绕过的关系判断；展示用 target 可以截断，身份判断必须保留完整合同。比较结果至少区分“匹配、明确冲突、证据不足”，fallback 只能补充证据不足，不能推翻 executable、selection 或 ambiguity 冲突。重定位例外只放宽经过证明的路径关系；`list_files` 的默认范围行为需要明确工具语义，不能由“无路径”推定。

复现：[consumer_probe.py](consumer_probe.py)，对应前六个 `engine_cases`；完整结果：[consumer_probe.json](consumer_probe.json)。

## R2 · P1：未知参数被删除，会把缩小验证范围判成等价

位置：[call_objective 的未识别 flag 分支](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1100>)。

未命中 selection/output 集合的 flag 直接被跳过，`ambiguous_selection` 仍为 false。因此“没有识别出选择变化”会被当作“选择范围相同”。这不能称为保守漏报。

本轮在临时目录创建一个失败的 auth 测试和一个通过的 billing 测试，实际运行：

```text
pytest tests/ -q --tb=short -p no:cacheprovider
→ exit 1，1 failed, 1 passed

pytest tests/ -q --tb=short -p no:cacheprovider --ignore-glob=*/test_auth.py
→ exit 0，1 passed
```

没有修复 auth 代码，仅排除它。两次调用的共享合同仍匹配；将真实 pytest 输出送入 runtime trace 后，Engine 生成了“使用带 ignore-glob 的 corrected test invocation”的 confirmed recovery，并通过 ValueGate。

建议：保留未分类参数及解析完整性，范围可能改变且无法判定时保留候选。忽略参数应以已知无关语义为依据；仅继续补充选择 flag 名单，无法提供泛化保证。实现记录对 `--lf` 的描述也需要按此区分：未知变化被忽略可能造成误确认，不能直接称为漏报。本轮直接证明的是 `--ignore-glob`，没有运行依赖历史缓存的 `--lf` 实验。

复现：[parser_probe.py](parser_probe.py) 的 `unrecognized_pytest_selector`；[parser_probe.json](parser_probe.json) 包含真实命令、退出码、完整输出、合同与准入结果。另一个 inline-cd 案例正确拒绝，作为负对照保留，不列为问题。

## R3 · P1：真实 traceback 会绕过缺失对象绑定

位置：[_missing_object_is_requested_target](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_evidence.py:1262>)。

函数先尝试提取缺失对象，但提取结果不匹配时，还允许请求路径出现在错误全文任意位置。这不是“缺失语句旁的对象绑定”。pytest traceback 天然会包含正在运行的测试路径。

真实运行中的失败输出包含：

```text
tests/test_auth.py:2: in test_auth
    open("fixtures/credentials.json").read()
E   FileNotFoundError: [Errno 2] No such file or directory: 'fixtures/credentials.json'
```

`tests/test_auth.py` 实际存在。随后运行另一个通过的 `backend/tests/test_auth.py`，Engine 仍生成可持久化的 confirmed recovery。上一轮只给出简短缺失 fixture 消息的探针被拒，加入正常 traceback 后又被接受。

建议：使用结构化异常对象或严格绑定到缺失语句的路径；删除全文 contains 的升级通道。不能确定缺失对象时，维持候选。把完整 traceback 加入回归，避免只测试人为缩短的错误消息。

复现：[consumer_probe.py](consumer_probe.py) 的 `real_traceback_test_path_is_not_missing_object`；JSON 同时保存两次真实 pytest 的退出码和输出。

## R4 · P1：模型能通过 related_error_ids 改写红绿归属

位置：[validator 从 claim 取错误集合](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:1637>)、[空 call 集合取消范围过滤](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/reflection_synthesis.py:576>)。

RecoveryEvidence 明确关联 auth 错误，现场另有 billing 的红绿检查。保持 evidence、statement、recovery 和 verification 不变，只改变模型候选的 `related_error_ids`：

| claim.related_error_ids | parse | validator / ValueGate |
| --- | --- | --- |
| `[error-auth]` | 通过 | 拒绝 |
| `[error-auth, error-billing]` | 通过 | confirmed 可持久化 |
| `[error-billing]` | 通过 | confirmed 可持久化 |
| `[]` | 通过 | confirmed 可持久化 |

原因是 validator 使用 claim 提供的错误集合决定红侧范围，而没有从每个 recovery 自身的关联取权威集合。空集合又被转换为 None，从而变成全任务搜索。即使不改 claim，当 auth 的 ErrorEvidence.call_id 为 None 时，规则合成器也会借用 billing 红绿并确认。

建议：逐 recovery 从自身 `related_error_ids` 派生可用错误调用，核验 claim 引用关系；无法绑定到失败调用时，此红绿分支应返回 false。模型可以选择证据，不能通过修改引用集合扩大证据之间已建立的关系。

复现：[validator_episode_probe.py](validator_episode_probe.py) 的 `claim_controlled_red_scope` 和 `unbound_error_widens_red_scope`；[结果](validator_episode_probe.json)。这是 parse + shared validator + ValueGate 的离线合同测试，未调用真实模型。

## R5 · P2：episode 未恢复 rendered cwd，其他目录的调用挤掉真重试

位置：[_same_operation_objective](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/recovery_lesson.py:209>)。

validator 使用 `_objective_from_rendered()` 恢复 cwd，episode 却直接调用 `call_objective(invocation)`，其中渲染尾部被剥除，cwd 丢失。

backend 的 `python scripts/report.py` 超时后，两次相同相对路径、不同 cwd 的成功调用占满两个 followup 配额；真正 backend 的 timeout=120 重试被丢弃。`actual_retry_retained=false`。此案例仍为 candidate，证明的是错误分片与召回损失，尚未证明持久化污染。

建议：统一 rendered invocation → objective 适配入口，或在 ToolEvidence 中携带结构化合同，保证所有消费者得到相同锚目录。

复现：consumer 和 validator/episode 两组探针均独立覆盖。

## R6 · P2：第五条验证保留了结果，却丢掉验证所需的调用

位置：[episode 截断 followup_calls](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/recovery_lesson.py:256>)、[投影只补 failed check 调用](</Users/zhourunbo/code/coding agent/MiniCode-Python-main/minicode/recovery_lesson.py:419>)。

episode 保留全部 verifications，却只保留前四个验证调用。五次通过同一目标的检查中，第五条仍能被模型引用，但对应 invocation 已丢失。相同候选在父证据中 `valid=true, persistable=true`，在子证据中因 `confirmed_recovery_verification_unrelated` 被拒。

建议：以完整证明链为截断单位，或补回所有保留检查的 invocation。不能留下可引用的 verification 同时删去 validator 建立归属所需的上下文。

复现：`validator_episode_probe.py` 的 `projection_loses_fifth_check_invocation`。

## 已验证的进展与检查范围

- 本轮独立运行 16 个相关测试模块：**359 passed**；没有把此数当作对用户所报全部 770 项的独立复跑。
- 上一轮 identity 四组反例全部拒绝；grounding 七组身份对照及 unrelated-red 对照全部符合预期；episode 的真实重试保留、噪声 red-check 投影测试均通过。
- 历史脚本复制到本轮目录执行，结果为 `previous_identity_probe.json`、`previous_grounding_probe.json`、`previous_episode_probe.json`，没有覆盖旧报告结果。
- 当前新 consumer 探针 7 个 Engine 案例均合同拒绝但 confirmed / ValueGate 接受；parser 的真实跳过测试案例合同匹配且接受。validator/episode 探针包含明确的拒绝对照。
- 不将尚未接线的 RecoveryLessonAgent、未开展的真实模型 shadow、逐字摘录之外的语义支持评测列作新缺陷；这些边界已明确披露。

测试命令：

```sh
.venv-acceptance/bin/python -m pytest -q \
  tests/test_recovery_misassociation_regression.py tests/test_recovery_lesson.py \
  tests/test_reflection_synthesis.py tests/test_reflection_llm_modes.py \
  tests/test_reflection_derived_recovery.py tests/test_reflection_operational_recovery.py \
  tests/test_reflection_generic_tool_recovery.py tests/test_reflection_tool_semantics.py \
  tests/test_reflection_persistence_authority.py tests/test_reflection_evidence.py \
  tests/test_reflection_llm.py tests/test_reflection_claim_selection.py \
  tests/test_reflection_claim_precision_evaluator.py tests/test_memory_reflection_batch_atomicity.py \
  tests/test_reflection_memory_operation_identity.py tests/test_memory_write_contract.py

.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/objective-contract-review/consumer_probe.py
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/objective-contract-review/parser_probe.py
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/objective-contract-review/validator_episode_probe.py > artifacts/recovery-rule-review-2026-09-09/objective-contract-review/validator_episode_probe.json
```

## 下一轮的收敛目标

保留子 Agent 负责提出、抽象经验的方向；确认和持久化准入应建立在下列可测试的不变量上：完整目标身份用于所有确认入口；明确冲突不能被 fallback 覆盖；解析不完整不能提高确定性；错误归属来自 evidence 自身；投影按完整证明链截断。先完成这些约束，再开展已规划的 shadow 对照，才能把规则的泛化收益与错误记忆接受率分开评估。
