# Workflow Phase Orchestration Split Report

Status: complete

## Outcome

The workflow orchestration has moved out of `minicode/tools/task.py` behind one
complete-run interface:

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

`task.py` validates the call, establishes the task ID/deadline, and dynamically
passes its current generic child runner. `workflow_runner.py` privately owns
workspace isolation, scheduler ports, role state, evidence, approval,
deterministic verification, commit-ready persistence, promotion, trace/audit,
and truthful terminal projection.

## Structure

- `task.py`: 5,551 -> about 1,463 lines.
- The former 2,679-line `_run_workflow_phases` closure is now a small
  construction wrapper around a non-reentrant `_WorkflowRunSession`.
- `run_worker` is a closed 12-line dispatcher. Analyst, Executor, Reviewer,
  Verifier, Human, prepare-commit, promotion, transition, and finalization each
  have explicit private methods.
- Shared definitions moved to their natural owners: agent types/depth policy,
  deadline calculation/checkpoint, journal opening, and bounded observation.
- `CentralScheduler` remains the only structured-v2 phase and commit state
  machine. Legacy `parallel_explore` remains an explicitly rejected exception
  for structured requests.

## Tests and compatibility

- Architecture tests require one runner public entry, no runner -> task reverse
  import, and no workflow runtime ownership in `task.py`.
- A direct dispatch test proves the current `task_module._run` is bound at call
  time, preserving the scripted child-runner seam.
- A new cancellation test proves a child control exception before promotion
  propagates and leaves the parent workspace unchanged.
- Existing projection/approval/transaction tests now patch or import from the
  defining module rather than obsolete `task.py` aliases.
- Focused workflow verification: 72 passing after the two added regressions.
- Affected integration verification: 298 passing before the final two tests;
  all previously observed failures were sandbox-only loopback restrictions.
- Final full verification: 5,040 passed, 2 skipped in 349.77 seconds.
- Ruff `E9/F/I`, `compileall`, and `git diff --check`: passing.

## Independent review

Three read-only reviewers found no P0/P1 issues. They independently confirmed
the one-way dependency, dynamic child seam, Scheduler authority, approval and
digest gates, commit-ready-before-promotion ordering, post-promotion truthful
fallbacks, and bounded audit/trace/result output.

Remaining P2 debt is explicit: the one-shot session currently copies phase
state from locals to `self` before Scheduler execution and copies authoritative
scalars back for terminal projection. The mapping is complete and tested, but
a later phase should make the session use one state representation throughout.
The session `run()` also remains roughly 900 lines; further deepening should
target the frozen post-Scheduler finalization seam, not expose more public role
interfaces or introduce a dynamic registry.
