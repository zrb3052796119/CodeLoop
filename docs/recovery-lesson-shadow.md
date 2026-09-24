# Recovery lesson Agent shadow

The recovery learner receives one bounded failure/recovery episode at a time. It uses a tool-free structured-generation client, the existing evidence validator and value gate, and the same turn budget and deadline as reflection. It can describe changed invocation inputs and edits without adding a rule for each error message.

The production entry is an optional branch of `ReflectionEngine.complete_shadow()`. In the memory pipeline it runs after rule-based memory is committed. Its generated claims never participate in memory selection or persistence. This is a comparison stage: structural validation cannot prove that every natural-language conclusion follows from a genuine quote.

## Configuration

The feature is disabled by default. To observe it on real tasks, use the existing reflection configuration and add:

```dotenv
MINI_CODE_REFLECTION_SYNTHESIZER_MODE=llm_shadow
MINI_CODE_RECOVERY_LESSON_SHADOW_ENABLED=true
MINI_CODE_RECOVERY_LESSON_MAX_CALLS=2
```

It reuses the configured reflection model and `allowRemoteReflectionModel` permission. The new switch does not grant remote access. Maximum episode calls is between 1 and 10, default 2, in addition to any existing whole-task reflection call. Both paths share the turn budget and preserve the existing final-call reservation. Sampling uses the existing reflection shadow sample rate. Cancellation and the parent deadline stop work; ordinary provider failures leave the rule result intact. Because this work is synchronous, enabling it can extend task finalization by the time spent in the bounded calls.

When reflection shadow metrics are enabled, recovery diagnostics use the same bounded metrics file. Records contain task hashes, counts, fixed reason codes and nullable usage/cost totals. They omit task text, file paths, invocation arguments, quotes and lesson bodies. The explicit offline evaluation report contains synthetic claim text for review and must not be confused with production metrics.

The dedicated structured client requests JSON-object output and low reasoning only when the adapter explicitly advertises those capabilities. It enforces the per-call output limit, disables tools and uses zero retries, without changing ordinary Agent requests. The schema binds the existing exact task title and outcome contract to host-provided constants. A provider-local timeout returns `provider_timeout` and permits later episodes to continue; the real parent deadline and cancellation still stop the operation.

Episode results and explicit evaluation reports preserve an allowlisted `stop_reason` and nullable `reasoning_present` boolean, never the reasoning body. A length-limited response is `output_limit_reached`, including when its partial output happens to parse. A candidate projection retains bounded same-target failed retries and their invocations as failed observations; it does not turn them into successful proof. Missing evidence is not evidence that an action was never performed, and applicability conditions must not assume an unproven cause.

## Reproducible evaluation

The dedicated evaluator uses only checked-in synthetic traces and calls `RecoveryLessonAgent`, comparing it with rules on the same episode projection. It does not invoke `MemoryPipeline`.

```sh
.venv-acceptance/bin/python scripts/evaluate_recovery_lesson_shadow.py \
  --output artifacts/recovery-lesson-scripted.json

.venv-acceptance/bin/python scripts/evaluate_recovery_lesson_shadow.py \
  --execute --max-calls 10 --timeout 15 \
  --output artifacts/recovery-lesson-live.json
```

The first command uses scripted outputs to exercise extraction, admission and accounting. It does not measure model quality. The second uses the configured provider, honors remote permission, and has a batch call limit. Each report preserves the actual call count, unknown usage values, per-episode baseline and model outputs, and annotations for semantic review. Cases that produce no episode are extraction checks, not successful model abstentions.

Review each generated claim for factual support in its cited events, causal status, verification scope, cross-episode leakage, and added value over the rule baseline. Candidate episodes should remain inferred observations. A verified recovery does not establish a root cause or a universally applicable fix. Record valid quotes with unsupported added conclusions as semantic errors even when the validator admits them.

Do not promote the learner into durable selection on the basis of a green scripted test suite or a small smoke test. The integration baseline is recorded in `artifacts/recovery-rule-review-2026-09-09/shadow-evaluation/`. The subsequent provider repair, successful real replay and semantic adjudication are in `artifacts/recovery-rule-review-2026-09-09/model-response-fix/completion.md`.
