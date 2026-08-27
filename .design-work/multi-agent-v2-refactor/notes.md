# Notes: Multi-Agent V2 Refactor Stage Two

## Baseline

- Starting commit: `4d3b7c1` (`fix(workflow): harden multi-agent coordination`).
- Full-suite baseline from the preceding stage: 5,014 passed, 2 skipped.
- Unrelated README, context-compaction, artifact, script, and launch-config
  changes already exist and must remain outside this refactor.

## Dependency Classification

- Workspace filesystem, local Git process, temporary directories, and advisory
  locks are local-substitutable dependencies exercised through `tmp_path` and
  existing deterministic command seams.
- No remote or true-external dependency is expected in the extraction.

## Findings

- The extraction cluster is `task.py:1563-2827`: two domain exceptions, four
  parent-lock helpers/state values, the frozen change-set record, and the
  1,126-line workspace transaction.
- The cluster depends only on standard-library filesystem/process primitives
  and the leaf `minicode.advisory_lock` backend, except for the task-owned
  `_TaskDeadlineExceeded` type/default timeout.
- The scheduler caller consumes snapshot paths/digests, path remapping,
  verification cloning, frozen change evidence, promotion status, and commit.
- Transaction-focused tests currently import the private task symbols and
  monkeypatch lock helpers through `minicode.tools.task`; those tests should
  move to the owning module while `task.py` keeps narrow identity aliases for
  compatibility.
- Baseline: `tests/test_workflow_v2_transaction.py` has 35 passing tests under
  `.venv-acceptance/bin/python -m pytest`.

## Interface comparison and decision

- Minimal-interface design: move the complete snapshot/freeze/verification/
  promotion state machine as one deep module; expose only the transaction and
  isolation error, with identity aliases in `task.py`.
- Extensibility design: eventually return a frozen-change capability object,
  but defer typestate and backend seams until a second implementation exists.
- Caller-first design: preserve the current constructor and phase methods so
  production callers change only their owner/import, making rollback precise.
- Selected hybrid: `minicode.workflow_workspace` owns the complete transaction
  state machine; `minicode.task_deadline` owns only the shared exception and
  default; context timeout parsing/checkpointing and commit-state-unknown
  escalation remain in `task.py`.
- Explicitly rejected for this change: façade callbacks, Protocol/ABC layers,
  registries, filesystem/Git/lock adapters, helper-proxy compatibility, and a
  simultaneous `freeze() -> FrozenChange` API redesign.
- Red proof: `tests/test_workflow_workspace_module.py` fails during collection
  because `minicode.workflow_workspace` does not exist before extraction.

## Extraction evidence

- `minicode.workflow_workspace` now owns the complete transaction and exposes
  only `WorkflowIsolationError` and `WorkflowWorkspaceTransaction`.
- `minicode.task_deadline` owns the shared deadline class/default; task-level
  context parsing/checkpointing did not move.
- `task.py` retains identity aliases for the three previously imported private
  types and retains `_WorkflowCommitStateUnknown` in the scheduler layer.
- AST comparison against `HEAD` proves all non-extracted top-level `task.py`
  definitions are unchanged, and the extracted eight-node transaction cluster
  is AST-equivalent after the four intentional public-name substitutions.
- Seam + transaction tests: 38 passed. Workspace/scheduler integration,
  structured routing, and P2 regression tests: 69 passed.
- Import/error checks (`ruff --select E9,F,I`), `py_compile`, and
  `git diff --check` pass. The repository's broader Ruff rule set still reports
  pre-existing findings in the mechanically moved implementation and the
  remaining task monolith; those are not mixed into this behavior-neutral move.

## Independent review

- Architecture/code reviewer: approved with no P0-P3 findings; independently
  confirmed AST equivalence, alias identity, acyclic dependencies, and intact
  BaseException/cleanup precedence.
- Test/migration reviewer: found no stale helper references or false monkeypatch
  seams. It identified one valuable missing cross-module regression: scheduler
  projection of an unresolved parent promotion.
- Added that regression to structured routing. It forces commit to report an
  unknown parent state and proves the task boundary returns a mandatory-delivery
  `workflow_commit_state_unknown` result with `changesCommitted=null` and
  `commitState=unknown`; the test passes.
- Residual platform risk: Windows advisory locking remains covered by simulated
  tests rather than a native Windows run.
