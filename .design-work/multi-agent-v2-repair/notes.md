# Notes: Multi-Agent V2 Stage-One Repairs

## Confirmed Findings

- `multi_agent_contracts._paths_overlap` is case-sensitive.
- Runtime declared-forbidden containment uses `Path.relative_to`, also
  case-sensitive even when the host filesystem resolves aliases to the same
  object.
- `_WorkflowWorkspaceTransaction` uses `fcntl` directly and silently degrades
  to a process-local lock when unavailable, despite the repository already
  providing `minicode.advisory_lock` for POSIX and Windows.
- Conflict ports are synchronous and only check the deadline before and after
  the callback; a callback that never returns can block forever.
- Async task cancellation is inferred from an output-string prefix.
- A cancellation raised while collecting a concurrent batch can bypass result
  accounting for sibling calls, while executor shutdown still waits.

## Chosen Seams

- A small shared path-policy leaf module will distinguish exact allow
  containment from conservative forbidden overlap.
- Existing `minicode.advisory_lock` is the lock adapter; no second platform
  abstraction will be introduced.
- Conflict callback bounding belongs behind one internal port runner rather
  than in each route.
- `ToolResult` remains the shared result interface and gains a closed internal
  termination marker; display strings remain backward compatible.

## Verification Ledger

- Path policy RED: case aliases, Windows ADS/device/trailing-name aliases, raw
  path-list whitespace, and resolver-erased aliases crossed at least one policy
  layer. GREEN: contracts and runtime use one conservative deny identity,
  validate before and after resolution, and 246 focused tests pass.
- Promotion lock RED: non-POSIX fallback, alias-specific lock identities, and
  cleanup collisions could weaken serialization or replace the primary commit
  state. GREEN: one cross-platform backend, stable source identity/namespace,
  close-once cleanup, and `primary > release > close` exception authority.
- Typed cancellation RED: lifecycle control parsed presentation text and a
  cancelled batch could wait forever, lose completed sibling accounting, or
  drop terminal evidence. GREEN: the closed enum/reducer owns authority,
  partial dispatch and cleanup are reduced deterministically, and 111 core
  cancellation/journal tests pass (245 across the extended related set).
- Conflict port RED: a never-returning callback blocked forever; late fatal or
  registered abort signals could be rewritten as timeouts; observer capacity
  could interfere with authority. GREEN: CentralScheduler uses long-lived,
  independent bounded runners; late ordinary failures time out while fatal and
  abort signals retain identity; 75 focused tests pass.
- Combined affected-module regression: 601 passed.
- Full repository verification with loopback socket access: 5,014 passed,
  2 skipped, 0 failed.
- Static verification: new modules pass full Ruff; the repair set passes
  Ruff `F/E9`, all files except the repository's pre-existing unsorted
  `agent_loop.py` import block pass `I`; source compilation and diff checks pass.
