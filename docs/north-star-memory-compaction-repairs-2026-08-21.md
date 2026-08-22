# MiniCode North-Star Memory / Compaction Repair Report

Date: 2026-08-21

## Outcome

The two product defects reproduced by the 20-task North-Star acceptance have
been repaired and verified at three levels: deterministic regressions, the
complete repository suite, and new real-provider task replays.

## 1. Empty structured values no longer poison recovery Memory

The trace safety gate previously applied the durable Memory body's
empty-content rule to every string inside structured tool events. A legal root
argument such as `list_files {"path":""}` therefore made an otherwise verified
recovery suspicious and pending.

The trace scanner now treats empty/whitespace-only structural values as absent
evidence and skips them. It does not weaken the durable-content gate:

- an empty or whitespace-only Memory body remains unsafe;
- non-empty instruction-injection-like trace text remains suspicious;
- the exact `config` -> root `list_files` recovery shape now creates a safe,
  approved, active and retrievable lesson.

## 2. Forced and cybernetic compaction now honor retry/circuit state

The high-water `should_trigger` path honored the circuit breaker, but
`execute_selected` and `dispatch(force_full=True)` did not. The outer feedback
controller could therefore continue forcing full compaction after failures.

The dispatcher now keeps a non-secret SHA-256 identity of each failed message
state per strategy:

- the same strategy against unchanged messages is suppressed without another
  summary/compaction attempt;
- a materially changed message state may retry;
- the selected-strategy and forced paths honor the global circuit breaker;
- provider overflow recovery retains one explicit bypass because a real API
  rejection is new hard evidence, then falls back to reactive truncation.

`ContextCompactor.last_result` also records ineffective auto-compaction attempts
instead of leaving stale success state.

## 3. Compaction failures are structured Run observations

The canonical parent and sub-agent journals now accept
`context.compaction.failed`. Pre-request cybernetic, direct compactor, in-loop,
and feedback-forced paths observe real failures. Cheap unchanged-state and
open-circuit policy skips are not emitted repeatedly.

The payload contains only bounded safe fields:

- path, trigger and strategy enums;
- a bounded reason enum such as `too_few_messages`, `no_token_reduction` or
  `internal_error`;
- attempted, consecutive-failure count and circuit-open state.

Raw messages, summaries, tool results and exception text are never projected.
The Dashboard timeline uses the same strict validator and omits operation IDs.

## 4. Verified TLS works in the actual macOS Python environment

The first real replay uncovered a separate runtime defect: the active
python.org installation produced an SSL context with zero CA certificates and
`certifi` was not installed. The provider request correctly refused the
self-signed/unknown chain.

MiniCode now checks the default context's CA count. Only when it is empty, it
loads the first existing standard system CA bundle (`/etc/ssl/cert.pem` and
common Linux equivalents), while preserving hostname and certificate
verification. Optional `certifi` remains an additional trust source. In the
actual environment the verified store increased from 0 to 128 CA entries, and
a subsequent provider request connected without `SSL_CERT_FILE`.

## Real-provider replay evidence

### Runtime Memory chain

- Result: 6/6 oracles passed.
- First Run made a real wrong-path tool call, recovered and wrote one verified
  lesson.
- Second Run selected and rendered one Memory entry.
- The written and rendered entry ID was identical:
  `project-1787284975356191000-adb84691`.
- Persisted state: `safety_status=safe`, `approval_status=approved`.

The stochastic live model chose a `read_file` path correction rather than the
original empty-root `list_files` correction. The exact empty-root shape is
therefore proven by the deterministic end-to-end Memory regression, while the
real replay proves the complete write -> approve -> retrieve -> render chain.

### Large-file compaction case

| Metric | Original North-Star run | Repair replay |
| --- | ---: | ---: |
| Strict oracles | 5/5 | 5/5 |
| Duration | 153.2 s | 25.8 s |
| Model calls | 25 | 5 |
| Tool calls | 31 | 5 |
| Input tokens | 257,088 | 49,541 |
| Repeated compaction-failure warnings | yes | no |

This is one stochastic replay, so the full numerical improvement is not claimed
as a causal effect size. It does show that the repaired runtime completed the
same frozen task without the previously observed compaction-failure loop.

### TLS smoke

Without an `SSL_CERT_FILE` override, the provider completed one real request
with provider-reported input/output tokens. The task returned the correct
marker but scored 4/5 because the model used `ask_user` to return its answer,
which the canonical outcome correctly leaves as incomplete/unknown. That is a
separate tool-choice behavior, not a TLS failure or part of this repair.

## Verification

- Focused Memory regressions: 18 passed.
- Focused Context, cybernetics, journal, Dashboard and sub-agent matrix: 246
  passed before the final added journal/timeline cases; those cases also pass.
- Scoped Ruff: passed.
- Python compile checks: passed.
- Complete authoritative repository suite:
  **4069 passed, 2 skipped, 0 failed** in 258.07 seconds.

An initial system-Python selected run had four environment-only failures
(`setuptools` absent and localhost binds denied). The authoritative Miniconda
run includes the wheel and localhost Gateway tests and is fully green.

## Remaining limitations

- The large-file efficiency delta is a one-run observation; multiple repeated
  paired runs are needed before claiming a stable latency/token effect.
- A correctly answered synthetic task can still be marked incomplete when the
  model misuses `ask_user` as a final-answer channel. This should be addressed
  as a separate tool-routing/completion-contract defect.
- Repository-wide `git diff --check` still reports one pre-existing trailing
  whitespace line in `.mini-code-memory/MEMORY.md`; scoped checks for all repair
  files are clean, and the user-owned Memory file was not altered.
