# Live Skill handoff audit

Read-only post-run audit. The frozen evaluator and probe were not modified; no extra model call or task action was executed. All 20 cases, request/response files, journal events, full tool schemas, catalog bindings and source/input hashes were independently cross-checked.

## Counts

- model_calls: 22
- model_completed: 20
- provider_input_tokens: 144542
- provider_output_tokens: 4555
- model_requested_load_calls: 13
- loader_dispatches: 13
- loader_successes: 12
- loader_denials_or_errors: 1
- successful_loads_without_confirmed_handoff: 11
- positive_cases: 17
- observable_cases: 18
- cases_load_requested_but_not_dispatched: 0
- cases_model_requested_any_load: 13
- cases_model_requested_any_load_in_first_response: 13
- cases_loader_succeeded: 12
- cases_body_handoff_confirmed: 1
- positive_request_strict: 11
- positive_load_strict: 10
- positive_handoff_strict: 1
- loader_outputs_confirmed_handed: 1
- model_failed: 2
- negative_cases: 3
- inconclusive_cases: 2
- negative_request_strict: 1
- negative_load_strict: 1
- negative_handoff_strict: 1

## Interpretation

Requested means an actual provider response proposed load_skill, including a request located after a non-load call that the safe observation window did not execute. Loaded means the real snapshot-bound loader succeeded under the production routing policy and returned the exact catalog body. Handoff means the exact real result appeared in both the internal next-call messages and the adapter's provider message projection, followed by a completed provider call with provider-reported usage.

The probe stops at the first non-load request. A grouped load_skill plus read_file/list_files/command request may therefore load the body without making a subsequent provider request. Such rows establish spontaneous selection and successful loading, but do not establish production injection failure. The probe does not run the full AgentLoop or its explicit-Skill final nudge. Negative cases can satisfy no-load checks without injecting any body; positive and negative denominators are separated above. A failed provider observation is inconclusive and never counted as a correct no-load result.

Failed provider observations: S18 (ModelCallDeadlineExceeded); S20 (ModelCallDeadlineExceeded).

Every executed tool event was load_skill; every actual dispatch site in the frozen evaluator/probe has a constant load_skill target. Non-load requests were recorded and never dispatched. Independent evaluator-initiated loader contract checks are excluded from the counts above. Journal run.completed describes probe completion, not synthetic task success. Provider input/output token totals are reported; no cost estimate is inferred.

| Case | Model requested | Real loader succeeded | Body handoff confirmed | Calls | Stop |
| --- | --- | --- | --- | --- | --- |
| S01 | code-understanding/codebase-explanation | code-understanding/codebase-explanation | — | 1 | nonload_tool_requested |
| S02 | code-understanding/design-review | code-understanding/design-review | — | 1 | nonload_tool_requested |
| S03 | — | — | — | 1 | nonload_tool_requested |
| S04 | — | — | — | 1 | nonload_tool_requested |
| S05 | — | — | — | 1 | nonload_tool_requested |
| S06 | security/secret-and-injection-review | security/secret-and-injection-review | — | 1 | nonload_tool_requested |
| S07 | testing/test-driven-development | testing/test-driven-development | — | 1 | nonload_tool_requested |
| S08 | debugging/concurrency-debugging | debugging/concurrency-debugging | — | 1 | nonload_tool_requested |
| S09 | dependencies/dependency-hygiene | dependencies/dependency-hygiene | — | 1 | nonload_tool_requested |
| S10 | testing/regression-from-bug-report | testing/regression-from-bug-report | — | 1 | nonload_tool_requested |
| S11 | testing/test-driven-development | testing/test-driven-development | — | 1 | nonload_tool_requested |
| S12 | refactoring/safe-refactor | refactoring/safe-refactor | — | 1 | nonload_tool_requested |
| S13 | refactoring/structural-rename | refactoring/structural-rename | — | 1 | nonload_tool_requested |
| S14 | code-skills/minicode-study | — | — | 2 | nonload_tool_requested |
| S15 | git/commit-hygiene | git/commit-hygiene | git/commit-hygiene | 2 | nonload_tool_requested |
| S16 | — | — | — | 1 | nonload_tool_requested |
| S17 | code-understanding/design-review | code-understanding/design-review | — | 1 | nonload_tool_requested |
| S18 | — | — | — | 1 | model_error |
| S19 | — | — | — | 1 | assistant_response_without_tool_calls |
| S20 | — | — | — | 1 | model_error |

## Controlled handoff supplement — separate experiment

The 11 eligible original mixed batches were reconstructed without changing their system/user/history prefix, model-originated calls, or successful loader outputs. Every unexecuted non-load call received an explicit error notice saying it was not executed; this is a measurement intervention. These extra calls are not added to original spontaneous selection/loading/handoff rates. S07 and S17 are included despite selection errors, because eligibility depends on actual load evidence, not correctness labels.

- explicit_nonexecution_notices: 11
- additional_provider_calls: 11
- completed_provider_calls: 11
- confirmed_body_outputs: 11
- provider_input_tokens: 86210
- provider_output_tokens: 656
- tool_dispatches: 0

Checks passed: original artifacts and frozen source hashes unchanged; full actual assistant batches preserved; all original Skill bodies present exactly in provider messages; one provider request per eligible case; provider completion and usage matched; zero tool dispatch sites and zero tool-execution events. The returned model requests were recorded only. This proves controlled body delivery, not full workflow adherence or task completion.

Supplement errors: none.
