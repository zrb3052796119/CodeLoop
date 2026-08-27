# Multi-Agent V2 Final Validation Report

## Outcome

The centrally scheduled multi-Agent implementation on validation base
`56d8cd8` passed the final fault-injection stage. No production-code defect was
found in this slice; the changes are regression tests and reproducible evidence.

Eleven new cases close the material test gaps identified by the coverage audit:

- Worker/adaptor crashes at Executor, Verifier, and Reviewer boundaries roll
  back the isolated change, never reach commit, and leave one replayable audit.
- An unsealed model majority has no authority, and typed but contradictory
  Adjudicator/Human results fail closed without triggering an improper fallback.
- A final audit that hangs after promotion is locally bounded and cannot erase
  `changesCommitted=true`; the returned result is failed, mandatory to deliver,
  and explicit that audit persistence was not confirmed.
- A late audit callback cannot overwrite the returned terminal truth. After its
  actual runner thread exits, the shared capacity is reusable by a second
  committed workflow.
- A wrapped host cancellation is restored as the exact typed cancellation
  object at the finalization boundary.

## Test signal

The portable public-entry regression uses identical test bytes on both trees:

- current: pass;
- `f8ae6fd`: ordinary assertion failure because the old synchronous final-audit
  call remains blocked beyond one second;
- no import failure, fixture failure, or verifier process timeout is involved.

Five isolated semantic faults were then injected in temporary worktrees. All
five were killed: exact `ToolResult` sealing, committed mandatory delivery,
late-callback capacity release, truthful audit status, and typed cancellation
identity. The cancellation mutation initially survived the broad integration
test; a direct boundary regression was added, after which the same mutation was
killed. See `test-signal-report.json` and the SHA-bound patches in `probes/`.

The seal test's historical failure occurs while arranging an `_state` slot that
does not exist on the old implementation. Its traced result is therefore
reported only as `REACH_ONLY`, not mislabelled as a behavioral proof.

## Verification results

| Check | Result |
|---|---:|
| Focused finalization/conflict/recovery/audit tests | 181 passed |
| Complete multi-Agent module tests | 529 passed |
| Full repository tests | 5069 passed, 2 skipped |
| Ruff on production plus changed tests (`E,F`) | passed |
| Python compilation of changed test modules | passed |
| `git diff --check` | passed |

The first sandboxed full-suite attempt failed only where existing tests needed
to bind local HTTP sockets. Repeating the same command on the same tree with the
required local test permissions produced the clean full-suite result above.

## Remaining explicit boundaries

- Conflict coordination remains a bounded standalone Scheduler entry point.
  Automatic conflict detection/insertion into the main workflow is still an
  explicit product deferral; unresolved task-path conflicts stop fail-closed.
- In-process timeouts bound the caller, reject late authority, and cap leaked
  concurrency, but cannot forcibly kill arbitrary Python callback code. A
  cancellable RPC or subprocess boundary is required for hard termination.
- Audit replay detects inconsistent/torn tampering and binds workflow/task,
  assignments, receipts, artifacts, and commit-ready authority. It is not a
  cryptographic WORM/MAC anchor against a fully privileged filesystem attacker.
- Crash injection between every individual audit artifact link and final
  manifest seal remains durability-hardening work; complete exact replay and
  write-once behavior are already covered.

No context-compaction source, test, experiment artifact, script, or README
change belongs to this validation set.
