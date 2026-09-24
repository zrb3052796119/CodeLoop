# Review evidence

- pyproject.toml packages root minicode; py-src and ts-src are not current production modules.
- AGENT_TYPES exposes explore, plan, legacy general, workflow. workflow is deterministic orchestration rather than a model-bearing agent.
- Current worktree has extensive pre-existing changes; review uses current files, not HEAD, and changes no source.
- Parallel review: role contracts, scheduler, runtime isolation. Root reviews parent integration, topology, ancillary agents, and validation.

## Verified findings

- Root offline scripted-model probe: committed child result `_must_deliver=True` causes immediate parent return (agent_loop.py:4146-4188) even with one pending async child and unsatisfied finalization barrier. Flags conflate preservation of authoritative commit output with completion of the user goal.
- Root probe also confirms final_response_text returns an old assistant response over a newer committed workflow tool result in multi-turn history (agent_runtime.py:358-365).
- Role reviewer found Adjudicator omitted from scheduler_read_worker at tools/task.py:1857; production dispatch explicitly sends plan/adjudicator at workflow_runner.py:3341/3354. It inherits generic permissions/Skill handling instead of Assignment-scoped read permissions. `_workflow_phase` still prevents memory injection.
- Adjudicator channel exposes conflict/assignment but not evidenceRefs from conflict claims. Needs frozen evidence read closure.
- Scheduler only advances/retries current stage; no Executor repair edge after verifier failure or review changes_required. Intentional current contract, classify as design limitation.
- Async spawn omits delegation circuit breaker implemented for synchronous run. Runtime reviewer reproduced.
- Concurrent workflow baseline checks only declared writes, not shared read dependencies; scheduler reviewer reproduced write skew using real workspace transactions. Trigger requires concurrent same-workspace workflows/callers.

## Verification

- 13 targeted test files: 311 passed, 4.73 seconds.
- 9 additional integration/role/result/routing test files: 179 passed, 13.42 seconds.
- Total: 490 passed. Offline tests and isolated probes only; no live model calls or efficiency benchmark performed.
- Lookup note: scope registry filename guessed once as workflow_write_scope_registry.py; that file does not exist. Resolve actual name via rg, no impact on findings.

- All delegated reviews completed and probe outputs inspected. Final findings and design priorities are in review.md.
