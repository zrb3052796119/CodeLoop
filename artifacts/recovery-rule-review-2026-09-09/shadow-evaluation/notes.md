# Shadow stage notes

Prior five structural fix groups are complete. This stage evaluates RecoveryLessonAgent utility and readiness. Existing uncommitted work is preserved.

## Confirmed integration/evaluation decisions

- Reuse the orchestrator's permission-checked reflection client; default-off episode shadow branch in `complete_shadow`, after MemoryPipeline commits the rule result. No new durable-selection path.
- Whole-task eligibility cannot gate episodes: the business timeout retry fixture has one candidate episode but fails whole-task `no_durable_signal_candidate`.
- Runtime currently has an authorized remote custom reflection model (`glm-5.3-flash`), credential present; no secret/provider endpoint copied into this report. Live pilot will use only checked-in synthetic data and at most 10 provider calls.
- Agent call ceiling defaults to 2 and caps at 10; shared parent budget still reserves one model call. Six added Agent tests currently pass (23 total in that module), including provider failures consuming the ceiling, nullable usage and rejected-claim review.
- Add explicit episode status and ordered ID context to the specialized prompt. The parent task's success is not proof of the episode's recovery, and genuine quotes do not entail arbitrary added conclusions.
- Raw generated statements are retained only in explicit synthetic review artifacts; runtime metrics must use an allowlist summary.

## Validation before live adjudication

- Final integration regressions: 43 modules, 973 passed in 6.93 seconds; includes 69 tests added in this stage. Ruff F checks and git diff --check pass.
- The first sandboxed live batch failed locally: 10 client-boundary attempts returned URLError in approximately 38 ms total; a credential-free DNS probe reported gaierror errno 8. This is connectivity failure, not semantic evidence. Preserved as `live-sandbox-attempt.json`.
- Automatic approval review authorized a second batch outside the network sandbox, using the same synthetic dataset and a 10-request ceiling. No provider retries are enabled on the dedicated adapter.
- Reflection's old estimate used a generic default tariff for unlisted models. Changed this telemetry path to return null unless the model has an explicit tariff; a real adapter test covers the unknown-model case, with the existing known-model case retained.
