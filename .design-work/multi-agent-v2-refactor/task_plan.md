# Task Plan: Multi-Agent V2 Refactor Stage Two

## Goal

Reduce `minicode/tools/task.py` safely by extracting the workspace transaction
and parent-promotion state machine behind one deep module interface, without
changing workflow behavior, authority ordering, or durable contracts.

## Scope

- inventory the transaction cluster and its dependency graph;
- compare at least three candidate interfaces and choose one seam;
- add characterization tests at that seam;
- extract the transaction implementation and narrow compatibility aliases;
- update callers/tests, remove duplicated ownership from `task.py`;
- independently review, run focused/affected/full verification, and commit only
  this refactor.

Audit/trace registry consolidation and decomposition of `_run_workflow_phases`
are deferred until this extraction is stable.

## Phases

- [x] Phase 1: Create persistent plan and freeze constraints
- [x] Phase 2: Inventory dependencies and compare interface designs
- [x] Phase 3: Add seam-level characterization tests
- [x] Phase 4: Extract the deep workspace-transaction module
- [x] Phase 5: Remove old ownership, update callers, and verify compatibility
- [x] Phase 6: Independent review and focused/affected/full verification
- [x] Phase 7: Conventional commit and handoff

## Key Questions

1. What is the smallest interface that hides snapshot, diff, lock, apply, and
   recovery mechanics from the workflow orchestrator?
2. Which task-level helpers are genuine transaction implementation, and which
   belong to orchestration or policy?
3. How can existing private imports remain compatible without creating a new
   long-term pass-through layer?
4. Which tests should move to the new interface so later internal refactors no
   longer require editing `task.py` tests?

## Constraints

- This is a structural refactor: observable behavior and serialized contracts
  must remain unchanged.
- Filesystem/Git dependencies are local-substitutable through temporary
  workspaces and existing command seams; no new public adapter is justified.
- Parent mutation, lock cleanup, cancellation, and commit-state-unknown
  precedence remain fail-closed.
- Existing unrelated working-tree changes must not be staged or rewritten.

## Errors Encountered

- The first dependency-search regex included a literal newline without
  multiline mode; reran it as independent anchored alternatives.
- The previously used `.venv-canonical` environment is not present in this
  checkout. The repository's available `.venv-acceptance` environment is
  healthy; the 35 transaction tests pass there.
- `python -m build` is not installed as an executable module in the available
  environment. Used `pip wheel --no-build-isolation --no-deps` instead; the
  wheel built successfully and contains both extracted modules.

## Status

**Complete** — two independent reviews are clean, 473 affected tests and the
5,018-test full suite pass, the built wheel contains both new modules, and the
exact refactor file set is isolated for its conventional commit.
