# Candidate failed-followup evidence

The `retry-still-failed` live observation asserted that no verification check
was performed. Its original synthetic trace contains a second invocation of
the same pytest target and another failed result (`event-000007` through
`event-000009`). The parent evidence retained this failed check, while the
candidate projection dropped it because it had no passing verification.

Candidate projections now retain up to four subsequent failed checks after
their recovery action, using the shared `CallObjective` comparison. Their
invocations, result events, referenced paths and event positions stay with
the retained verification records. The checks remain `failed`; the recovery
context's original failed-call IDs, successful-followup IDs and passing
verification IDs are unchanged. Its ordered event list includes the added
evidence. The existing projection of verified proof chains is unchanged.

The window starts after the recovery action, or after the original failed
call when the candidate has no recovery record. It ends before an observed
successful followup or the next recovery boundary, including that later
recovery's own distinct failed-call root. This deliberately stops at another
recovery even if interleaved work could later return to the first objective.
Unknown call/result timing is not guessed. Target/cwd/selection relationship
filtering and chronological ordering precede the four-check limit.

Verification:

- New regression file: before the change, 6 failed and 3 passed. The exact
  live-fixture test could not find `call-3` in projected verification, and
  the prompt test lacked its event IDs.
- After the change, all 9 new tests passed: live fixture and prompt evidence,
  noise filtering, bounds and ordering, independent-chain isolation,
  pre-recovery exclusion, missing timing, and unchanged verified evidence.
- Related recovery/prompt/shadow/evaluator/response-diagnostic/chain tests:
  119 passed. Ruff F checks passed.
- No model or formal memory calls were made for this change. The original
  live report and its independent adjudication remain intact.

```sh
.venv-acceptance/bin/python -m pytest -q tests/test_recovery_lesson_failed_followup.py tests/test_recovery_lesson.py tests/test_recovery_lesson_prompt.py tests/test_recovery_lesson_shadow.py tests/test_recovery_lesson_shadow_evaluator.py tests/test_reflection_response_diagnostics.py tests/test_reflection_recovery_chain_isolation.py
```
