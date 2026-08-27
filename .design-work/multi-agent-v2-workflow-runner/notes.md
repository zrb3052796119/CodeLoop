# Notes: Workflow Phase Orchestration Split

## Baseline

- Starting commit: `f444a07` (`refactor(workflow): bind frozen change capability`).
- Previous final suite: 5,035 passed, 2 skipped.
- Unrelated working-tree changes were present before this phase and are out of
  scope.

## Dependency classification

- Scheduler/contracts/artifact projection: in-process.
- Workspace transaction, local journal, Git, clock, cancellation: local-
  substitutable.
- Human approval and child-agent execution are existing caller-owned ports;
  this refactor must not invent additional adapters.

## Findings

- `_run_workflow_phases` spans lines 1,821-4,499: 2,679 lines, 83 returns,
  33 `nonlocal` bindings, 355 assignments, 145 branch/try nodes, and 525 calls.
- Its nested phase handlers are: Worker 889 lines, Verifier 256, Human 86,
  commit 32, commit-ready persistence 103, transition recording 24, final
  result projection 318, terminal projection 60, plus audit/trace finalization.
- The cohesive workflow-only helper cluster begins at line 803. Every helper
  through `_run_workflow` is used only by that cluster, except for six general
  task helpers (`_task_timeout_seconds`, `_raise_task_deadline`,
  `_open_subagent_journal`, `_emit_subagent_observation`, and child `_run`).
- Existing tests invoke the public `task_tool` seam. They commonly monkeypatch
  `task_module._run`, which can remain compatible if the task caller passes
  the current child-run callable into the extracted module at invocation time.
- Class-method patches on Scheduler, Verifier, WorkspaceTransaction, and
  FrozenWorkflowChange remain effective across modules because they patch the
  defining class object. Four projection-injection cases and one output-cap
  case patch task-module aliases and will need migration to the defining module.
- Characterization baseline for structured routing, scheduler integration,
  and residual deadline behavior: 67 passed.

## Interface comparison

1. **Minimal complete runner** — one public `run_workflow(...) -> ToolResult`
   seam; transaction, scheduler handlers, evidence, promotion and finalization
   stay private. This maximizes module depth and removes about 3,900 lines from
   `task.py`.
2. **Caller-first complete runner** — the same complete seam plus a public
   runtime-limits value. This is migration-friendly, but exposes role turn
   limits that the caller does not need to reason about.
3. **Runner plus finalizer** — explicit run state and a second finalization
   module. This improves internal navigation, but its frozen handoff snapshot
   would expose many tightly coupled authority facts and increase first-move
   behavioral risk.

## Decision

Use the minimal complete runner, with the caller-first design's important
compatibility property: `task.py` passes the current `_run` callable on every
invocation. The runner owns all workflow state and finalization privately;
`task.py` retains validation, ID/deadline initialization, and the generic child
agent loop. Do not introduce a separate finalizer or public policy DTO in this
phase. A later internal refactor may turn the large closure into a private
one-shot session without changing this external seam.

Target interface:

```python
run_workflow(
    input_data,
    context,
    *,
    depth,
    subagent_id,
    run_subagent,
) -> ToolResult
```

The module must not import `minicode.tools.task`; `run_subagent` is the existing
production/test seam and prevents a reverse dependency.

## Implemented shape

- `task.py` fell from 5,551 to 1,464 lines and now delegates through the single
  `run_workflow(...)` seam.
- `workflow_runner.py` owns the complete transaction and contains a private,
  non-reentrant `_WorkflowRunSession` with explicit methods for Worker,
  Verifier, Human, commit-ready, promotion, transition recording, trace/audit
  finalization, and terminal result projection.
- The previous 2,679-line `_run_workflow_phases` is now a 19-line construction
  wrapper. The largest remaining coordinator method is the session's roughly
  900-line terminal routing/projection method; role execution and finalization
  are separately navigable methods.
- Shared agent type/depth policy moved to `subagent_types.py`; deadline,
  journal-open, and bounded observation helpers live with their natural
  contracts instead of being duplicated across task and workflow modules.
- Focused characterization plus architectural boundary tests: 72 passing
  after adding dynamic child-runner and pre-promotion cancellation regressions.
- Affected task/workflow integration set: 298 passing with loopback access.
- First full-suite checkpoint: 5,038 passing and 2 skipped. Final full suite
  after the two added tests: 5,040 passing and 2 skipped in 349.77 seconds.

## Review disposition

- Three independent reviewers reported no P0/P1 findings.
- Accepted P2 follow-ups: converge session locals/attributes into one state
  representation and deepen the roughly 900-line terminal coordinator.
- Addressed during this phase: split the 870-line Worker port into a 12-line
  closed dispatcher plus explicit Analyst, Executor and Reviewer handlers;
  add direct dynamic-runner and pre-promotion cancellation regressions; migrate
  the deadline clock patch to its defining module.
