# Recovery lesson shadow integration

The runtime now has an independent, default-off recovery lesson shadow branch. It reuses the existing approved structured-generation client and runs only when both `reflectionSynthesizerMode=llm_shadow` and `recoveryLessonShadowEnabled=true` are set. `recoveryLessonMaxCalls` defaults to 2 and is bounded to 1–10.

Configuration is available through settings and process/private user environment:

- `MINI_CODE_RECOVERY_LESSON_SHADOW_ENABLED`
- `MINI_CODE_RECOVERY_LESSON_MAX_CALLS`

Both names are included in the private user `.env` allowlist. Existing remote-model approval, timeout, sampling, metrics opt-in, and shared Agent budget controls remain in force.

## Runtime boundaries

- `CyberneticOrchestrator.initialize` creates the dedicated learner with the same client object used by the existing reflection synthesizer; it does not create another credential or provider route.
- `ReflectionEngine.complete_shadow` completes whole-task and episode branches separately. Each has its own completion flag set before optional work, so repeated completion, provider errors, and recorder errors cannot cause repeat calls.
- Episode eligibility uses actual recovery episodes. It deliberately does not reuse the whole-task durable-signal gate, which rejects unproven timeout retries even though those retries are the intended observation input for the new learner.
- The existing `MemoryPipeline` sequence is unchanged: it requests deferred shadow, commits rule output, and then calls `complete_shadow`.
- The learner receives the same budget, cancellation token, absolute deadline, and event sink. Ordinary learner failures are isolated. Cancellation and deadline signals preserve the existing Pipeline contract.
- The engine retains only `RecoveryLessonShadowSummary`. The raw lesson result, generated text, invocation arguments, evidence excerpts, and observations are not attached to `ReflectionResult` or passed into production selection. The summary is excluded from `to_memory_entry`.

## Metrics

`ReflectionShadowMetricsRecorder.record_recovery` has a separate field allowlist and shares existing bounded/locked JSONL writes. Records are tagged `record_type=recovery_lesson_shadow`. Old whole-task aggregate counts exclude them; the summary reports the recovery branch separately.

Fields contain only hashed task identity, fixed state/reason codes, episode/call/claim counts, and nullable usage totals. Missing provider token/cost data remains null. Unattempted episodes do not invalidate the measured latency of attempted episodes. Generated claims and raw result serialization are never used in the metrics path.

## Validation

Added `tests/test_recovery_lesson_shadow.py`: **27 passed**. Coverage includes default-off and mode gates, independent sampling, timeout candidate eligibility, zero calls when unavailable or budget-denied, shared successful-call accounting, provider/unexpected/metrics failure isolation, cancellation/deadline propagation, repeated completion, safe metrics, nullable usage, actual persistable shadow lessons remaining outside rule selection, and real MemoryPipeline ordering.

The persistence test captures every stored entry's content, approval status, and metadata at provider-call time and compares them after shadow completion; the snapshots are identical. A separate test generates a validator/value-gate accepted recovery lesson and confirms the rule memory projection and selected claim IDs remain unchanged.

Added a private user `.env` test in `tests/test_config.py`: it failed with the missing allowlist entries, then passed after the entries were added. It also verifies process environment precedence, maximum-call clamping, and that file loading does not populate `os.environ`. All configuration paths and memory stores used by tests are temporary.

Related test command:

```sh
.venv-acceptance/bin/python -m pytest -q tests/test_recovery_lesson_shadow.py tests/test_reflection_shadow_metrics.py tests/test_reflection_llm_modes.py tests/test_cybernetic_orchestrator.py tests/test_config.py
```

Result: **168 passed in 1.01s**. `git diff --check` passed on the owned integration/config/metrics/test files. No live provider calls or real memory writes were performed.
