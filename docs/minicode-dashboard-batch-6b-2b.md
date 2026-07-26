# MiniCode Dashboard Batch 6B-2B

## Outcome

Batch 6B-2B adds honest cooperative cancellation to the durable Dashboard Chat
Turn introduced in Batch 6B-2A. A client can request cancellation while one
synchronous Turn is executing. MiniCode persists that request, signals the live
Agent through a process-local token, stops starting new Model/Tool work at safe
checkpoints, and prevents a cancelled Turn from committing Assistant content.

This is cooperative cancellation, not forced termination. A Provider request or
Tool call already in progress may finish before MiniCode regains control. An
external side effect that already occurred cannot be rolled back. The UI and HTTP
contracts state these limits and never claim immediate stop or rollback.

Truth remains split across three authorities:

| Fact | Authority | Cancellation role |
|---|---|---|
| Turn | `ConversationTurnStore` | durable request, state, cancellation and commit-race authority |
| Session | Session base/deltas | sole authority for user/assistant content and commit marker |
| Run | RunJournal | best-effort execution telemetry; never cancellation authority |

## Durable state machine

The closed status set is:

```text
accepted -> running
accepted/running -> cancel_requested -> cancelled
running -> committing -> completed
running/committing -> failed or interrupted where applicable
```

`completed`, `failed`, `interrupted`, and `cancelled` are terminal. No terminal
state returns to running. `cancel_requested` is not a terminal claim: it means the
durable request was accepted and the live execution has been signalled, but code
already inside a Provider/Tool call may still be returning. `cancelled` means the
execution reached a safe checkpoint and no Assistant commit occurred.

`request_cancel()` is idempotent. Repeating it for `cancel_requested` keeps the
same state and token and does not start, repeat, or retry any work. A request
against `committing` or a terminal state returns the authoritative state with
`cancellationAccepted=false`.

## Cancellation token and Agent checkpoints

`minicode.turn_cancellation` contains the intentionally small execution seam:

- `TurnCancellationToken.is_requested()` reads a thread-safe event;
- `request()` sets it idempotently;
- `raise_if_requested()` raises the private control-flow exception
  `TurnCancellationRequested`;
- `TurnCancellationRegistry` owns one live token per Turn Store claim;
- `raise_if_cancelled(None)` is an exact no-op.

The token contains only a validated Turn ID and event. It carries no cancellation
reason, user message, Session content, Provider identity, raw error, credential,
or path. Durable authority remains in the Turn Store; the token is process-local
notification only.

The optional token is threaded through `AgentTurnRuntime` and the existing Agent
Loop. Checkpoints cover the boundaries before and after Model calls, before and
after Tool execution, between concurrent Tool batches, retry/recovery boundaries,
and the final return to Conversation. Once a request is observed, no subsequent
Model or Tool work is started. Token-less Headless, TUI, classic CLI, and `/run`
retain their previous behavior because every new parameter defaults to `None`.

The cancellation exception is never converted into an ordinary Model/Tool retry,
fallback, or recovery. `KeyboardInterrupt` and `SystemExit` also retain their
existing control-flow identity.

## Atomic commit race

`ConversationTurnStore.begin_commit(turnId)` is the atomic authority boundary.
Under the Store lock it performs exactly one of two decisions:

- if durable state is `cancel_requested`, commit is refused and cancellation wins;
- if durable state is `running`, state becomes `committing` and completion owns
  the race.

Conversation checks the token, calls this gate, then saves the Session and exact
Turn commit marker. Once `committing` is durable, a late Cancel cannot overturn
the completion attempt. A successful Session save becomes `completed`; a Session
save failure becomes the appropriate fixed failure. If Session save succeeds but
the Turn completion write is lost, restart recovery finds the authoritative
Session marker and promotes the Turn to `completed` without replay.

If cancellation wins, Conversation does not save the finished user/assistant
message set, history update, permission summary, Skill/MCP summary, or commit
marker. It marks the Turn `cancelled` and raises the fixed domain error. Thus a
new-Session cancelled Turn creates no persisted Session content, and a cancelled
continued Turn leaves the existing Session content byte-authoritative.

## Run terminal semantics

RunJournal remains optional telemetry. When `TurnCancellationRequested` exits
the observed execution context, the linked Run is transitioned to
`interrupted` with fixed reason `execution_cancelled`. The Turn Store is still
the cancellation authority. A Journal write failure cannot turn a cancellation
into completion or alter Session truth.

## HTTP contract

The strict route is:

```text
POST /api/v1/chat/turns/{turnId}/cancel
Content-Type: application/json
Body: {}
```

The path accepts only `turn_<32 lowercase hex>`, no query string, and the body
must be exactly one empty JSON object under the existing bounded framing rules.
Malformed paths, duplicate JSON keys, unknown keys, invalid UTF-8/content type,
or invalid framing return the fixed 400 response before service execution.

A valid response is versioned and allowlisted:

```json
{
  "ok": true,
  "schemaVersion": 1,
  "mode": "read-write",
  "turnId": "turn_...",
  "status": "cancel_requested",
  "cancellationAccepted": true,
  "sessionId": null,
  "runId": "run_... or null",
  "updatedAt": "..."
}
```

Missing or foreign records return the same fixed `turn_not_found` 404. Storage
or service failure returns fixed `turn_failed` 500. The route exposes no
fingerprint, owner token, commit marker, message, cancellation reason, path, or
raw error. The original synchronous POST returns fixed `turn_cancelled` when the
cancelled outcome wins. GET Turn status remains the read-only source of durable
truth and now includes `cancel_requested`, `committing`, and `cancelled`.

## Restart reconciliation

A new Gateway process owns a new Turn Store owner ID and no live cancellation
token. On the first status/cancel/replay lookup of an abandoned active record:

- an authoritative Session marker wins and the record becomes `completed`;
- otherwise `cancel_requested` becomes `cancelled`;
- otherwise an abandoned `accepted`, `running`, or `committing` record becomes
  `interrupted`.

This reconciliation is based on durable state plus the exact Session marker,
never time, HTTP response order, content similarity, RunJournal, or frontend
guessing. A completed record whose referenced Session result is missing reports
`resultAvailable=false`; the UI says the result is unavailable and does not
misreport cancellation.

## Frontend behavior

The independent Chat store adds `cancelling`, `cancel_requested`, `committing`,
`cancelled`, and `completed_unavailable` presentation phases plus a separate
`operationGeneration`. Cancel is available only for one active cancellable Turn.
The first click immediately disables the control and sends one strict request;
repeated clicks cannot submit another cancellation operation.

`requestGeneration` invalidates the original POST presentation path, while
`operationGeneration` protects status/cancel operations. A late original POST
cannot overwrite a newer Cancel decision. If commit already won, the UI performs
one explicit status reconciliation when the stale success arrives. It never uses
HTTP response arrival order as final authority.

The draft remains available after cancellation, but MiniCode never resends it.
An explicit user send creates a fresh Turn ID. The Dock label is exactly:

```text
synchronous · recoverable · cancellable · no live updates
```

There is still no timer, automatic polling, automatic resend, SSE, WebSocket,
streaming, background worker, thread kill, signal-based thread termination, or
unsafe asynchronous exception injection.

## Supported scope and limits

The supported product scope is one local Gateway Demo process plus durable
restart reconciliation. It does not claim multi-Gateway, distributed, provider
exactly-once, cross-machine, NFS, lease, fencing, heartbeat, or external
transaction coordination. An already-sent Provider request may complete and an
already-executed Tool side effect remains external reality. MiniCode only
guarantees that after it observes cancellation at a safe checkpoint it starts no
new Model/Tool work and does not commit Assistant content for that Turn.

## Certification

- Pre-edit full suite: `2144 passed, 2 skipped, 3 warnings in 106.69s` after the
  identical localhost-dependent suite was rerun with loopback permission.
- Original REDs: Store 5 missing-transition failures, token module 2 import
  failures, Agent 6 missing-token/checkpoint failures, plus Conversation/HTTP/
  frontend cancellation gaps.
- Focused GREEN: token/Store 20; Agent 44; Conversation matrix 40; cancellation
  matrix 47; Chat HTTP 54; compatibility 157; Dashboard/read model 194;
  packaging/installed wheel 9.
- Touched-file Ruff, `py_compile`, full `compileall -q minicode scripts tests`,
  and both production JavaScript `node --check` commands passed. A read-only
  repository-wide Ruff scan still reports 82 unrelated pre-existing findings.
- Active v16 matches its deterministic candidate and all 33 protected files;
  every v1-v16 manifest pin passes.
- Official semantic evaluator: 108 cases, 37 confirmed gaps, zero remote calls,
  `evaluation_passed=true`; behavior projection and per-case fingerprints are
  unchanged.
- Accepted semantic gold remains SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3,033,592`, mtime ns `1784135857000000000`.
- Evaluator-after final full suite: `2202 passed, 2 skipped, 3` existing
  benchmark-marker warnings in `118.19s`.
- Browser acceptance used an isolated real Gateway and deterministic fake Agent.
  It covered normal completion, Model/Tool cancellation, both deterministic
  commit races, restart, completed-result recovery/unavailability, explicit
  resend, all eight main and five Memory routes, escaped XSS, no overlap or
  horizontal overflow, and zero console warnings/errors.
- Browser tabs were finalized after the viewport override was reset. The test
  Gateway was stopped, port 18765 had no listener, and the task-owned temporary
  Harness, HOME, workspace, semantic reports, and bytecode were removed.
- Runtime dependencies remain `[]`.

## Stable boundary for Batch 7

Batch 7 may consume the existing durable Turn ID, strict POST, strict Cancel POST,
read-only status GET, Session marker authority, and status vocabulary. It may add
presentation or transport around those facts, but must not create a second Turn
identity/cancellation authority, infer terminal status from response order, or
weaken the `committing` boundary. Polling, push transport, streaming, background
jobs, authentication, and multi-user/distributed control remain future work.
