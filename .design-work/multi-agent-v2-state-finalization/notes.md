# Notes: Workflow state and finalization

## Current ownership problem

- `_WorkflowRunSession.run()` initializes local workflow variables, copies roughly 47 values to session attributes before Scheduler execution, and copies a subset back to locals afterward.
- Scheduler callbacks mutate session attributes directly. The current mapping is complete, but every new field creates a silent synchronization hazard.
- The same `run()` method also performs trace projection, audit persistence, terminal rendering, observation emission, output compaction, and committed-truth fallback.
- Existing public behavior is strongly characterized by workflow routing, approval, audit, deadline, cancellation, output-cap, and cleanup tests.

## Compared seam designs

1. Minimal execution/sealed-run design: one mutable state, one frozen run record, one narrow finalization access object.
2. Extensible Ledger design: typed terminal dispositions, explicit ledger methods, and pluggable audit projectors.
3. Caller-first authority design: keep `run_workflow(...)` as the only caller-facing interface and hide execution/sealing entirely.

## Selected design

- Combine the minimal and caller-first designs.
- Keep `run_workflow(...)` unchanged and as the only public runner interface.
- Introduce one private `_WorkflowExecutionState`; Scheduler callbacks read and write only this object.
- After `CentralScheduler.execute()` returns and pending assignment writes are revoked, detach canonical JSON documents, phase outputs, immutable Scheduler facts, and artifact snapshots into a frozen finalization snapshot.
- Move trace, audit, terminal rendering, observation, compaction, and truthful fallback into `workflow_finalization.py`.
- Give finalization only three narrow capabilities: deadline checkpoint, audit persistence, and observation emission.
- Do not introduce generic terminal dispositions, an audit registry, public role interfaces, or a new Scheduler bridge in this phase.

## Invariants

- `CentralScheduler` remains the only owner of stage ordering and business transitions.
- Finalization cannot import or receive `run_subagent`, `DeterministicVerifier`, workspace transaction/frozen change, human approval, artifact write authority, prepare-commit, commit, or promotion capabilities.
- Snapshot documents are canonical JSON strings and phase results are scalar copies, so finalization cannot mutate execution state indirectly.
- Pre-promotion cancellation keeps propagating; post-promotion cancellation/deadline preserves committed truth with `_must_deliver=True`.
- Existing errors, markers, audit counts, `awaitUser`, and the 8,000-character authority cap remain unchanged.

## Implemented shape

- `_WorkflowRunSession` now has only immutable invocation configuration, a one-shot guard, and one `_WorkflowExecutionState` slot.
- `_WorkflowRunSession.run()` is reduced from roughly 909 lines to roughly 273 lines and ends immediately after Scheduler execution by sealing and finalizing.
- `workflow_finalization.py` owns frozen capture, trace/audit projection, all terminal rendering, observation facts, authority compaction, and committed-truth fallback.
- The frozen capture copies phase `ToolResult` scalars, canonicalizes nested role documents, tuples transitions/artifacts, and carries no execution capability.
- Artifact snapshot failure is represented as unavailable evidence so a successful trace can still be returned with an audit-failure status.
- Finalization tests run through the real seam without supplying any Worker, Verifier, Human, commit, or promotion capability.

## Review-driven resilience corrections

- The first post-Scheduler cancellation/deadline checkpoint now precedes artifact reads and rich snapshot capture.
- Direct snapshot construction validates closed immutable shapes; phase capture rejects `ToolResult` subclasses.
- If rich snapshot capture fails after promotion, a degraded frozen snapshot retains Scheduler facts and artifacts so final audit persistence is still attempted before returning a truthful `_must_deliver` failure.
- The session clears its state, context, transaction, child runner, and journal handles before calling finalization; use-after-seal fails explicitly.
- Uncommitted cancellation during audit persistence propagates unchanged. After commit, cancellation becomes a finalization status and cannot suppress the audit attempt or truthful result.
- Audit persistence and optional observation use separate process-level bounded runners. A hung observation cannot consume audit capacity; audit waits at most five seconds and observation waits at most one second per call.
- A timed-out in-process callback may finish late, so terminal wording says persistence was not confirmed. True process-level hard cancellation remains a future subprocess/RPC adapter concern.

## Verification

- Focused multi-Agent, Scheduler, audit, permission, verifier, transaction, and workspace suite: `498 passed`.
- Full repository suite outside the socket-restricted sandbox: `5058 passed, 2 skipped`.
- Repository production lint: `ruff check minicode/ --select=E,F --ignore=E501` passed.
- Touched-test lint, format check, compileall, and `git diff --check` passed.
