# Task Plan: Unify workflow state and isolate finalization

## Goal
Reach the final validation stage by giving each workflow one authoritative state representation and a read-only finalization seam, without changing routing, permissions, error semantics, or user-visible results.

## Phases
- [x] Phase 1: Re-establish invariants and map the current state handoff
- [x] Phase 2: Compare seam designs and lock the smallest safe interface
- [x] Phase 3: Add behavioral characterization tests and implement state unification
- [x] Phase 4: Isolate post-scheduler finalization behind a read-only snapshot
- [x] Phase 5: Run focused and full verification, independent review, and commit
- [x] Phase 6: Record the handoff into final fault-injection/scenario validation and stop

## Key Questions
1. Which values are truly mutable workflow state versus immutable configuration or transient local data?
2. What is the smallest immutable outcome that finalization needs after `CentralScheduler.execute()`?
3. How do we mechanically prove that finalization cannot invoke workers, mutate scheduler state, or promote changes?

## Decisions Made
- The public `run_workflow(...)` interface remains unchanged.
- `CentralScheduler` remains the sole transition authority.
- This phase stops once the code is ready for final scenario and fault-injection validation; it does not execute that final phase.
- Use one private mutable execution state and a detached frozen finalization snapshot.
- Finalization receives only deadline, audit-persistence, and observation capabilities.
- Reject speculative terminal-disposition and audit-registry abstractions in this phase.

## Errors Encountered
- `.venv-canonical/bin/python` is absent in this checkout; use the repository's existing acceptance environment after detecting it.
- `tests/test_workflow_orchestration.py` was an incorrect remembered filename; enumerate the repository's workflow tests before selecting the focused set.
- Expected TDD red: finalization dependency guard failed until the new module existed, and the single-state guard failed against the old session slots.
- The first finalizer-only fixture used an invalid short subagent ID; corrected it to the production contract shape before asserting the seam.
- The first full-suite invocation ran inside the restricted sandbox and failed on loopback socket binding; it was stopped and rerun outside the sandbox, where the suite passed.

## Status
**Stopped at the final-stage entrance** - State unification and read-only finalization are complete. The next work is the deliberately deferred scenario/fault-injection matrix.
