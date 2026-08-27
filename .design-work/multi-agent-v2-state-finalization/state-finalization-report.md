# Workflow State and Finalization Report

## Outcome

The workflow runtime now has one mutable execution authority and one detached, read-only finalization boundary. `CentralScheduler` still owns routing and every business transition; the public `run_workflow(...)` seam is unchanged.

After Scheduler termination, the runner checkpoints cancellation/deadline, snapshots immutable authority facts, revokes every execution-capable session handle, and passes only `(snapshot, ports)` to finalization. The finalizer cannot invoke a Worker, Verifier, Human gate, transaction, commit, or promotion.

## Failure semantics

- Before promotion, cancellation propagates to the caller, including cancellation requested while final audit persistence is waiting.
- After promotion, cancellation, deadline, projection failure, artifact snapshot failure, or audit timeout returns a bounded truthful failure with `_must_deliver=True` and `changesCommitted=true`.
- Rich snapshot failure after promotion drops Worker-authored documents but retains Scheduler trace/artifact facts and still attempts durable audit persistence.
- Audit and telemetry callbacks have separate bounded capacity so optional telemetry cannot starve authoritative audit work.
- Thread-bounded callbacks cannot be forcibly killed in Python. A timeout therefore means persistence is unconfirmed, and a stronger subprocess/RPC boundary belongs to the final hard-failure phase if end-to-end killability is required.

## Evidence

- 13 new boundary/resilience regressions were added.
- Affected suite: `498 passed`.
- Full suite: `5058 passed, 2 skipped`.
- Ruff E/F, formatting, compileall, and whitespace validation passed.

## Handoff

This work intentionally stops before the final scenario matrix. The next stage should exercise real conflict, crash, timeout, forged-output, audit-replay, and recovery scenarios against the now-stable boundaries; it should not reopen role ownership or add more Agent types unless a failing scenario proves that necessary.
