# Agent Turn 状态归属测量（2026-10-24）

本文件由 `scripts/analyze_agent_turn_state.py` 生成；只读取源码，不导入或运行 Agent，不是新增 gate。

源码：`minicode/agent_loop.py`；SHA-256：`ee82348ae566289c950ca35ec9fd6abf36b03ca97cec3c839d2f6764af6207fe`。
`run_agent_turn` 第 2103–7423 行，共 5321 物理行、54 参数；函数体直接语句中的函数定义 26 个；同一词法层（含控制块内定义）28 个；所有层级共 30 个。

## 口径与限制

AST 给出函数边界；Python `symtable` 给出词法作用域，区分函数参数、局部绑定、闭包自由变量、模块全局和未被模块绑定遮蔽的 builtin。闭包自由变量包含 Python 为更深层闭包转交的捕获；只把实际解析到 `run_agent_turn` 绑定的名字计入 turn 状态，深层函数自己父闭包的局部值单列在 JSON。
这里的“状态”包含回调、对象引用和内部函数绑定，不能仅由变量名判断可变性。记录 `nonlocal` 重新绑定和捕获对象属性/下标写入；通过方法调用、别名、反射及外部函数发生的修改未作静态推断。未捕获的主循环局部量、主循环调用顺序和模块全局依赖不进入共享比例；因此这不是全函数耦合率。
职责组是脚本中可审阅的人工假设：lifecycle / tools / budget / telemetry / context / completion。后两组保留上下文投影和完成审查的实际职责，不为凑四个模块而并入其他组。分类变化会改变跨组比例。

## 共享比例与 D1 决策

- 分母：92 个被任一命名闭包捕获的唯一 turn 绑定（turn 全部局部绑定 483 个）。
- 被至少两个闭包捕获：41 / 92 = 44.57%。
- 跨至少两个职责组：28 / 92 = 30.43%。
- 试行阈值的明确解释：跨职责组共享比例 **>60%** 时，不按这套分组直接搬移闭包；先画状态所有权及调用顺序。阈值是本次治理的启发式，不是业界标准或质量评分。
- 测量结果：**未触发 >60% 阈值**；这只允许进入边界设计，不构成直接重构的安全证明。
- 当前决策：D1 测量完成，允许进入边界设计；抽取验收尚未进行。先选择单一职责明确状态所有者、输入/输出和副作用顺序，逐一验证 cancellation、budget、delivery 与 completion 的契约；本次不改生产循环。

## 闭包矩阵

| 函数 / 行 | 职责 | 所有自由变量 | turn 捕获 | 全局 / builtin | nonlocal 重绑 |
| --- | --- | ---: | ---: | ---: | --- |
| `reconcile_task_ledger` / 2233 | context | 2 | 2 | 0 / 2 | — |
| `observable_message_count` / 2252 | telemetry | 0 | 0 | 0 / 1 | — |
| `raise_if_deadline_exceeded` / 2400 | budget | 1 | 1 | 2 / 0 | — |
| `enforce_skill_load_before_final` / 2407 | tools | 4 | 4 | 0 / 1 | skill_requirement_nudged |
| `enforce_discovery_before_final` / 2431 | tools | 5 | 5 | 0 / 1 | skill_discovery_nudged |
| `enforce_subagent_completion_before_final` / 2457 | lifecycle | 5 | 5 | 4 / 8 | — |
| `observe_workflow_delivery` / 3106 | lifecycle | 2 | 2 | 0 / 1 | — |
| `committed_delivery_remaining` / 3111 | lifecycle | 3 | 3 | 0 / 7 | — |
| `finish_workflow_delivery` / 3128 | lifecycle | 8 | 8 | 3 / 2 | authority_delivery_ready, turn_outcome |
| `restore_committed_child_receipts` / 3152 | lifecycle | 2 | 2 | 1 / 3 | — |
| `interrupted_parent_message` / 3167 | lifecycle | 4 | 4 | 1 / 1 | — |
| `_revalidate_model_visible_memory` / 3182 | context | 3 | 3 | 0 / 3 | — |
| `observe_implementation_tool_result` / 3263 | tools | 4 | 4 | 0 / 1 | — |
| `submission_correction_progress` / 3275 | tools | 9 | 9 | 3 / 3 | — |
| `progress_view_paths` / 3376 | context | 3 | 3 | 0 / 1 | — |
| `implementation_request_messages` / 3382 | context | 5 | 5 | 0 / 0 | — |
| `implementation_view_active` / 3389 | context | 2 | 2 | 0 / 1 | — |
| `configured_output_limit` / 3399 | budget | 2 | 2 | 0 / 5 | — |
| `completion_input_estimate` / 3408 | budget | 6 | 6 | 1 / 1 | — |
| `_attempt_max_steps_soft_landing` / 3416 | completion | 23 | 23 | 17 / 13 | — |
| `closing_failed` / 3455 | completion | 7 | 2 | 4 / 0 | — |
| `finish_bounded_completion` / 3595 | completion | 12 | 12 | 2 / 0 | authority_delivery_ready, turn_outcome |
| `observe_batch_feedback` / 3644 | tools | 4 | 4 | 0 / 0 | direct_implementation_batch_status, direct_implementation_checks_failed |
| `flush_write_feedback_batch` / 3654 | tools | 5 | 5 | 2 / 0 | write_feedback_batch, write_feedback_group |
| `deliver_batch_feedback` / 3674 | tools | 2 | 2 | 0 / 0 | — |
| `supersede_completion_feedback` / 3678 | tools | 1 | 1 | 0 / 2 | — |
| `enforce_implementation_completion_before_final` / 3686 | completion | 43 | 43 | 9 / 3 | direct_completion_accepted, direct_completion_attempt, direct_completion_classification, direct_completion_limits, direct_completion_limits_initialized, direct_completion_review, direct_implementation_batch_status, direct_implementation_checks_failed, direct_migration_state, implementation_completion_feedback |
| `retain_migration_checkpoint` / 3781 | completion | 1 | 1 | 0 / 0 | direct_migration_state |
| `_predictive_compaction` / 4137 | context | 4 | 4 | 1 / 2 | — |
| `phase_input_estimate` / 4294 | budget | 9 | 9 | 4 / 1 | — |

## Turn 绑定 × 职责矩阵

| 绑定 | 职责组 | 捕获该绑定的闭包 | 重绑 / 属性或下标写入 |
| --- | --- | --- | --- |
| `ExecutorCheckCapability` | tools | observe_implementation_tool_result:3263, submission_correction_progress:3275 | — |
| `ExecutorResultChannel` | tools | submission_correction_progress:3275 | — |
| `_attempt_max_steps_soft_landing` | completion | finish_bounded_completion:3595 | — |
| `_revalidate_model_visible_memory` | completion | _attempt_max_steps_soft_landing:3416 | — |
| `abandoned_event` | completion | enforce_implementation_completion_before_final:3686 | — |
| `agent_budget` | budget, completion | _attempt_max_steps_soft_landing:3416, closing_failed:3455, enforce_implementation_completion_before_final:3686, phase_input_estimate:4294 | — |
| `agent_depth` | completion | enforce_implementation_completion_before_final:3686 | — |
| `authority_delivery_ready` | completion, lifecycle | finish_bounded_completion:3595, finish_workflow_delivery:3128 | finish_bounded_completion:3595, finish_workflow_delivery:3128 |
| `batch_feedback_by_call` | tools | flush_write_feedback_batch:3654 | flush_write_feedback_batch:3654 |
| `batch_feedback_messages` | tools | deliver_batch_feedback:3674, flush_write_feedback_batch:3654 | — |
| `cancellation_token` | completion | _attempt_max_steps_soft_landing:3416, enforce_implementation_completion_before_final:3686, finish_bounded_completion:3595 | — |
| `committed_child_batches` | lifecycle | restore_committed_child_receipts:3152 | — |
| `committed_delivery_remaining` | lifecycle | finish_workflow_delivery:3128 | — |
| `completion_evidence_views` | completion | enforce_implementation_completion_before_final:3686 | — |
| `completion_hard_limit` | completion | finish_bounded_completion:3595 | — |
| `completion_input_estimate` | completion | _attempt_max_steps_soft_landing:3416, enforce_implementation_completion_before_final:3686 | — |
| `completion_policy` | completion | _attempt_max_steps_soft_landing:3416, finish_bounded_completion:3595 | — |
| `configured_output_limit` | completion | enforce_implementation_completion_before_final:3686 | — |
| `context_checkpoint_sink` | completion | _attempt_max_steps_soft_landing:3416 | — |
| `context_compactor` | completion, context | _attempt_max_steps_soft_landing:3416, _predictive_compaction:4137 | — |
| `context_manager` | completion, context | _predictive_compaction:4137, _revalidate_model_visible_memory:3182, enforce_implementation_completion_before_final:3686 | _predictive_compaction:4137, _revalidate_model_visible_memory:3182, enforce_implementation_completion_before_final:3686 |
| `current_messages` | budget, completion, context, lifecycle, tools | _attempt_max_steps_soft_landing:3416, _predictive_compaction:4137, _revalidate_model_visible_memory:3182, completion_input_estimate:3408, deliver_batch_feedback:3674, enforce_discovery_before_final:2431, enforce_implementation_completion_before_final:3686, enforce_skill_load_before_final:2407, enforce_subagent_completion_before_final:2457, finish_bounded_completion:3595, finish_workflow_delivery:3128, implementation_request_messages:3382, interrupted_parent_message:3167, restore_committed_child_receipts:3152, supersede_completion_feedback:3678 | _predictive_compaction:4137, _revalidate_model_visible_memory:3182, enforce_implementation_completion_before_final:3686, restore_committed_child_receipts:3152, supersede_completion_feedback:3678 |
| `cwd` | completion | enforce_implementation_completion_before_final:3686 | — |
| `deadline_monotonic` | budget, completion | _attempt_max_steps_soft_landing:3416, enforce_implementation_completion_before_final:3686, raise_if_deadline_exceeded:2400 | — |
| `deliver_batch_feedback` | completion | enforce_implementation_completion_before_final:3686 | — |
| `direct_completion_accepted` | completion | enforce_implementation_completion_before_final:3686 | enforce_implementation_completion_before_final:3686 |
| `direct_completion_attempt` | completion | enforce_implementation_completion_before_final:3686 | enforce_implementation_completion_before_final:3686 |
| `direct_completion_classification` | completion | enforce_implementation_completion_before_final:3686 | enforce_implementation_completion_before_final:3686 |
| `direct_completion_limits` | completion | enforce_implementation_completion_before_final:3686 | enforce_implementation_completion_before_final:3686 |
| `direct_completion_limits_initialized` | completion | enforce_implementation_completion_before_final:3686 | enforce_implementation_completion_before_final:3686 |
| `direct_completion_owner` | completion | enforce_implementation_completion_before_final:3686 | — |
| `direct_completion_review` | completion | enforce_implementation_completion_before_final:3686 | enforce_implementation_completion_before_final:3686 |
| `direct_implementation_batch_status` | completion, tools | enforce_implementation_completion_before_final:3686, observe_batch_feedback:3644 | enforce_implementation_completion_before_final:3686, observe_batch_feedback:3644 |
| `direct_implementation_checks_failed` | completion, tools | enforce_implementation_completion_before_final:3686, observe_batch_feedback:3644 | enforce_implementation_completion_before_final:3686, observe_batch_feedback:3644 |
| `direct_migration_state` | completion | enforce_implementation_completion_before_final:3686, retain_migration_checkpoint:3781 | enforce_implementation_completion_before_final:3686, retain_migration_checkpoint:3781 |
| `event_sink` | completion, lifecycle, tools | _attempt_max_steps_soft_landing:3416, closing_failed:3455, enforce_implementation_completion_before_final:3686, finish_bounded_completion:3595, finish_workflow_delivery:3128, submission_correction_progress:3275 | — |
| `execution_trace` | completion, lifecycle | _attempt_max_steps_soft_landing:3416, enforce_implementation_completion_before_final:3686, enforce_subagent_completion_before_final:2457 | — |
| `finish_workflow_delivery` | completion, lifecycle | finish_bounded_completion:3595, interrupted_parent_message:3167 | — |
| `flush_write_feedback_batch` | completion | enforce_implementation_completion_before_final:3686 | — |
| `has_structured_handoff` | budget, completion | enforce_implementation_completion_before_final:3686, phase_input_estimate:4294 | — |
| `host_native_result_tool` | tools | submission_correction_progress:3275 | — |
| `implementation_completion_feedback` | completion, context | enforce_implementation_completion_before_final:3686, reconcile_task_ledger:2233 | enforce_implementation_completion_before_final:3686 |
| `implementation_continuation` | completion | enforce_implementation_completion_before_final:3686 | — |
| `implementation_feedback` | tools | observe_implementation_tool_result:3263, submission_correction_progress:3275 | — |
| `implementation_observation_context` | tools | observe_implementation_tool_result:3263 | — |
| `implementation_phases` | budget, completion, context, tools | _attempt_max_steps_soft_landing:3416, observe_batch_feedback:3644, phase_input_estimate:4294, progress_view_paths:3376 | — |
| `implementation_request_messages` | budget, completion | _attempt_max_steps_soft_landing:3416, completion_input_estimate:3408 | — |
| `implementation_request_state` | context | progress_view_paths:3376 | — |
| `implementation_spec` | completion | enforce_implementation_completion_before_final:3686 | — |
| `implementation_view_active` | budget, completion | _attempt_max_steps_soft_landing:3416, completion_input_estimate:3408, phase_input_estimate:4294 | — |
| `max_retries_override` | completion | _attempt_max_steps_soft_landing:3416 | — |
| `model` | budget, completion | _attempt_max_steps_soft_landing:3416, configured_output_limit:3399, enforce_implementation_completion_before_final:3686, phase_input_estimate:4294 | — |
| `mutation_observer` | context | progress_view_paths:3376 | — |
| `observe_batch_feedback` | tools | flush_write_feedback_batch:3654 | — |
| `on_assistant_message` | completion, lifecycle | _attempt_max_steps_soft_landing:3416, finish_bounded_completion:3595, finish_workflow_delivery:3128, interrupted_parent_message:3167 | — |
| `on_progress_message` | completion, lifecycle | enforce_implementation_completion_before_final:3686, enforce_subagent_completion_before_final:2457 | — |
| `orch` | context | _revalidate_model_visible_memory:3182 | — |
| `owned_implementation_feedback` | completion, tools | enforce_implementation_completion_before_final:3686, observe_batch_feedback:3644, observe_implementation_tool_result:3263 | — |
| `permissions` | completion | enforce_implementation_completion_before_final:3686 | — |
| `phase_observed_input` | budget | completion_input_estimate:3408, phase_input_estimate:4294 | — |
| `phase_snapshot` | budget | phase_input_estimate:4294 | — |
| `presentation` | completion | enforce_implementation_completion_before_final:3686 | — |
| `previous_model_input_estimate` | budget | completion_input_estimate:3408 | — |
| `progress_view_paths` | context | implementation_request_messages:3382, implementation_view_active:3389 | — |
| `project_implementation_progress_messages` | context | implementation_request_messages:3382 | — |
| `project_recovery_messages` | context | implementation_request_messages:3382 | — |
| `raise_if_deadline_exceeded` | completion | _attempt_max_steps_soft_landing:3416, enforce_implementation_completion_before_final:3686, finish_bounded_completion:3595 | — |
| `reconcile_task_ledger` | completion, context | _predictive_compaction:4137, enforce_implementation_completion_before_final:3686 | — |
| `recovery_view_active` | context | implementation_request_messages:3382, implementation_view_active:3389 | — |
| `repair_input_calibration` | budget, completion | _attempt_max_steps_soft_landing:3416, completion_input_estimate:3408, phase_input_estimate:4294 | — |
| `required_skills` | tools | enforce_skill_load_before_final:2407 | — |
| `restore_committed_child_receipts` | lifecycle | interrupted_parent_message:3167 | — |
| `runtime` | budget, completion | _attempt_max_steps_soft_landing:3416, configured_output_limit:3399, enforce_implementation_completion_before_final:3686 | — |
| `seen_submission_rejections` | tools | submission_correction_progress:3275 | — |
| `skill_discovery_gate` | tools | enforce_discovery_before_final:2431 | — |
| `skill_discovery_nudged` | tools | enforce_discovery_before_final:2431 | enforce_discovery_before_final:2431 |
| `skill_requirement_nudged` | tools | enforce_skill_load_before_final:2407 | enforce_skill_load_before_final:2407 |
| `skill_usage_tracker` | tools | enforce_discovery_before_final:2431, enforce_skill_load_before_final:2407 | — |
| `step` | completion, lifecycle, tools | _attempt_max_steps_soft_landing:3416, enforce_implementation_completion_before_final:3686, enforce_subagent_completion_before_final:2457, finish_bounded_completion:3595, finish_workflow_delivery:3128, submission_correction_progress:3275 | — |
| `store` | completion | _attempt_max_steps_soft_landing:3416 | — |
| `subagent_lifecycle` | completion, lifecycle | committed_delivery_remaining:3111, enforce_implementation_completion_before_final:3686, enforce_subagent_completion_before_final:2457 | — |
| `subagent_mailbox` | completion, tools | enforce_implementation_completion_before_final:3686, submission_correction_progress:3275 | — |
| `suggested_skill_directories` | tools | enforce_discovery_before_final:2431 | — |
| `supersede_completion_feedback` | completion | enforce_implementation_completion_before_final:3686 | — |
| `task_ledger` | context | reconcile_task_ledger:2233 | — |
| `todo_state` | lifecycle | committed_delivery_remaining:3111 | — |
| `tools` | budget, completion, lifecycle, tools | _attempt_max_steps_soft_landing:3416, enforce_implementation_completion_before_final:3686, observe_workflow_delivery:3106, phase_input_estimate:4294, submission_correction_progress:3275 | — |
| `turn_outcome` | completion, lifecycle | finish_bounded_completion:3595, finish_workflow_delivery:3128 | finish_bounded_completion:3595, finish_workflow_delivery:3128 |
| `verification_tracker` | completion | enforce_implementation_completion_before_final:3686 | — |
| `workflow_delivery` | lifecycle | committed_delivery_remaining:3111, finish_workflow_delivery:3128, observe_workflow_delivery:3106 | — |
| `write_feedback_batch` | tools | flush_write_feedback_batch:3654 | flush_write_feedback_batch:3654 |
| `write_feedback_group` | tools | flush_write_feedback_batch:3654 | flush_write_feedback_batch:3654 |

## 复现

```sh
python scripts/analyze_agent_turn_state.py --markdown docs/agent-turn-state-ownership-2026-10-24.md
```

可另用 `--json <输出路径>` 保存完整参数、局部、全局、builtin、自由变量及词法所有者清单。
