# T5: isolate each recovery's complete verification chain

The validator now resolves each cited recovery's own error records and its own later passing checks together, before checking evidence linkage. It no longer passes an error union shared by all recoveries. A claim still qualifies when one cited recovery/check pair supplies an independently complete chain; adding unrelated recovery references cannot manufacture a chain out of separate fragments.

Changed production file: `minicode/reflection_synthesis.py`, method `ReflectionClaimValidator._reject_unlinked_recovery_verification`. The exact task-local production diff is in `t5-production.patch`; the actual pre-fix file is retained as `t5-reflection_synthesis.before.py`.

New tests: `tests/test_reflection_recovery_chain_isolation.py` (12 cases). Coverage includes separate and combined recovery references, reversed reference order, actual later successful billing verification, truthful/added/substituted/empty claim error references, and unbound error call IDs. The tests exercise real candidate parsing, validation, and the value gate; no model or memory APIs are called.

## Validation

- Existing relevant baseline before the change: **46 passed**.
- New regression before production edit: **2 failed, 10 passed**. Both failures are the expected false admissions when two incomplete recovery chains are cited together (`t5-red.txt`).
- Same new tests after production edit: **12 passed** (`t5-green.txt`).
- Real-baseline proof: **PROVEN**, using the actual pre-fix source snapshot and identical test bytes/interpreter/command. Fixed side: 12 passed. Baseline side: 2 failed, 10 passed, specifically at the false-admission assertions. See `t5-counterexample-proof.json`, `t5-counterexample-proof.txt`, and `t5-baseline.json`.
- Related synthesis/LLM/replay/value/selection/lesson/regression modules: **233 passed**, including the 12 new cases (`t5-related-tests.txt`).
- Historical multi-recovery scope probe now rejects auth-only, billing-only, and combined references (`t5-multi-recovery-probe.json`).
- Previous error-reference/null-call and episode probes remain fixed (`t5-previous-validator-episode-probe.json`): all false error-reference variants reject, null-call rule stays inferred, rendered-cwd true retry is retained, and the fifth check passes in both parent and projected evidence.
- `git diff --check` passed for the two owned source/test paths.

Commands:

```sh
.venv-acceptance/bin/python -m pytest -q tests/test_reflection_recovery_chain_isolation.py tests/test_reflection_synthesis.py tests/test_reflection_llm.py tests/test_reflection_llm_modes.py tests/test_reflection_llm_replay_regression.py tests/test_reflection_value_pipeline.py tests/test_reflection_claim_selection.py tests/test_recovery_lesson.py tests/test_recovery_misassociation_regression.py
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/three-state-review/multi_recovery_scope_probe.py
.venv-acceptance/bin/python artifacts/recovery-rule-review-2026-09-09/objective-contract-review/validator_episode_probe.py
```

Only the owned production module and new test file were edited; historical review probes were read without modification. No live provider or durable memory calls were made.
