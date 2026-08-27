# Multi-Agent V2 Refactor Stage-Two Report

Status: complete

This report will record the selected seam, ownership moved out of `task.py`,
compatibility strategy, verification evidence, residual risks, and commit.

## Selected seam

`workflow_workspace.py` will own snapshot creation, trusted Git isolation,
change freezing, verification clones, parent locks, promotion, and interrupted
promotion reconciliation. The scheduler remains the only owner of dispatch,
approval, commit timing, audit/trace projection, and final user-visible result.

The first extraction deliberately preserves the existing transaction protocol.
A frozen-change capability API is the next deepening candidate after this
mechanical boundary is proven stable.

## Ownership moved

- `minicode/workflow_workspace.py` now owns snapshotting, trusted Git setup,
  frozen change evidence, verification copies, parent locks, promotion, and
  interrupted-promotion reconciliation.
- `minicode/task_deadline.py` owns the shared deadline exception and default.
- `minicode/tools/task.py` is 1,269 lines smaller and retains orchestration,
  commit authorization, finalization projection, context deadline checks, and
  the commit-state-unknown escalation.

## Compatibility

The former task-private transaction, isolation error, and deadline error names
are identity aliases to the extracted types. Existing integration monkeypatches
of transaction methods therefore continue to hit the production class. Lock
helper tests now patch their true owner rather than a pass-through alias.

## Verification

- Pre-change transaction baseline: 35 passed.
- Seam and transaction: 38 passed.
- Focused workspace/scheduler/structured/P2 regressions: 69 passed.
- Affected multi-Agent suite: 473 passed.
- Full suite: 5,018 passed, 2 skipped.
- Two independent reviewers: approved, no P0-P3 findings.
- AST equivalence: extracted state machine and all remaining task definitions
  match `HEAD` after intentional symbol renames/removals.
- `ruff --select E9,F,I`, `py_compile`, and `git diff --check`: passed.
- Offline wheel: built successfully and contains `task_deadline.py` and
  `workflow_workspace.py`.

## Residual risks and next seam

Native Windows locking was not exercised; existing Windows branches remain
covered by simulation. External code reflecting on underscore-private class
`__module__` values or patching private lock helpers through `task.py` will see
the owner change.

The next safe deepening step is a separate typestate change:
`freeze() -> FrozenWorkflowChange`, with verification and promotion available
only from the frozen capability. It should not be combined with audit/trace
registry consolidation or `_run_workflow_phases` decomposition.
