# Multi-Agent V2 Stage-One Repair Report

Status: complete

## Delivered Repairs

- Path authority now has one shared lexical identity policy. Allow scopes stay
  filesystem-exact; deny scopes compare conservatively across case aliases.
  Windows ADS, reserved devices, illegal/control characters, and trailing-dot
  or trailing-space aliases are rejected before resolution and checked again
  afterwards.
- Parent promotion uses the repository's POSIX/Windows advisory-lock backend,
  a source-directory identity key, and a stable per-user namespace. Setup,
  acquire, release, and close failures fail closed without double release or
  replacing a more authoritative commit/cancellation exception.
- Conflict authority ports run behind bounded, reusable Scheduler-owned gates.
  Authority and observation capacity are independent; deadlines use remaining
  durations across clock domains; late results are discarded; fatal,
  cancellation, and registered abort signals preserve their identity.
- Concurrent tool cancellation uses a typed termination reason and a closed
  reducer with `finalized result > fatal signal > cancellation > ordinary
  failure`. It detaches uncooperative reads without blocking, accounts only
  genuinely completed work, balances partial provider turns, and records one
  content-free terminal disposition per dispatched cancelled call.
- Sub-agent terminal evidence has an opaque call reference, closed schema,
  separate count/byte reserve, and bounded file reads. More than 1,000 model
  tool calls fail closed before any dispatch.

## Verification

- Independent reviews: no remaining blocker or important finding in path/lock,
  conflict, or cancellation scope.
- Focused path/lock: 246 passed.
- Focused conflict: 75 passed.
- Focused cancellation/journals: 111 passed; extended related set: 245 passed.
- Combined affected set: 601 passed.
- Full repository: 5,014 passed, 2 skipped, 0 failed. The first sandboxed run
  was separately classified as an environment failure because loopback socket
  binding was denied; the identical unsandboxed test command passed.
- New modules pass full Ruff. The repair set passes Ruff `F/E9`, scoped import
  ordering, Python compilation, and `git diff --check`. Existing repository
  lint debt outside these gates was not rewritten.

## Residual Risks and Deferred Work

- Python cannot forcibly terminate an already-running in-process callback.
  Conflict and read-tool runners therefore bound leaked capacity and mark
  detached work with `executionMayContinue=true`; stronger isolation still
  requires a subprocess or RPC runner.
- Windows alias and lock behavior is covered by host-independent policy tests
  and simulated backend tests, but this repair was not executed on a native
  Windows host.
- Direct callers that instantiate `ConflictCoordinator` without a runner keep
  the legacy synchronous compatibility mode. `CentralScheduler` is bounded by
  default.
- Splitting the large `task.py` workflow state machine and consolidating audit,
  trace, and digest registries remain the next maintainability stage.

Commit identity: this report is part of the conventional repair changeset; the
resulting hash is recorded in the handoff after commit creation.
