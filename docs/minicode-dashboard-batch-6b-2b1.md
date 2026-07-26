# MiniCode Dashboard Batch 6B-2B.1

## Outcome

Batch 6B-2B.1 closes the deterministic cancellation race between a durable
`accepted` Turn and the original request's `mark_running()` transition. It also
lets the existing manual status action recover `cancel_requested` and
`committing` UI states. It adds no polling, push transport, background work, or
Batch 7 control surface.

## Original race and deterministic RED

The original request could persist `accepted`, pause before `mark_running()`,
and lose authority to a concurrent Cancel that persisted `cancel_requested`.
The old `mark_running()` then raised a generic Store error, so the original POST
became `turn_failed`/`ConversationTurnFailed` and the Store remained
`cancel_requested` until a later reconciliation.

A deterministic Event-gated tracer reproduced exactly that order. It proved
that Cancel returned accepted, Runtime was never constructed, and the original
request incorrectly produced `(ConversationTurnFailed, cancel_requested)`.
The same authority inversion was reproduced when cancellation raced with a
Runtime factory failure.

## Typed Store decisions

`ConversationTurnStore` now owns both atomic decisions:

- `TurnStartDecision(record, execution_started)` changes `accepted` to
  `running`, or changes a winning `cancel_requested` directly to `cancelled`
  and refuses execution.
- `TurnFailureDecision(record, failure_recorded)` records a real failure only
  when no pre-commit cancellation already owns the outcome. A winning
  `cancel_requested` becomes `cancelled` and cannot be overwritten by
  `failed`.

`ConversationTurnService` consumes these typed values. It neither matches
exception strings nor performs a second racy read to infer cancellation. A
non-start decision raises `ConversationTurnCancelled`, which the existing HTTP
mapping returns as structured `turn_cancelled` 409.

The authority rules are therefore:

```text
accepted + cancel first       -> cancel_requested -> cancelled
accepted + start first        -> running
running + cancel before gate  -> cancel_requested -> cancelled
running + begin_commit first  -> committing -> completed/failed
terminal + later operation    -> same terminal state
```

When cancel owns the pre-commit state, subsequent Runtime, Model, Tool, Session,
or error-handling work cannot turn the Turn into completed or failed. Without a
cancel request, Runtime construction and execution failures retain the original
failed behavior.

## Side-effect boundary

The accepted-boundary HTTP test uses the real `ThreadingHTTPServer`, strict
Gateway route, deterministic Store gate, and injected service. It proves:

- Cancel returns 200 with `cancellationAccepted=true`;
- the original POST returns 409 with `code=turn_cancelled`;
- the durable Turn is immediately `cancelled`, without a later GET;
- Runtime factory/Model/Tool execution count is zero;
- no Session or Assistant content is committed;
- no false completed Run is created;
- responses and persisted records expose no prompt, assistant body, exception,
  credential, owner token, or machine path.

Additional deterministic tests cover Session creation, Runtime/Model/Tool
failures, both cancel/commit linearization orders, repeated Cancel, terminal
immutability, and restart reconciliation.

## Frontend manual recovery

The formal Waku Dashboard keeps the existing explicit `检查状态` action and now
shows it for `cancel_requested` and `committing`. It still makes no automatic
status request, retry, resend, or timer-driven update.

- `cancel_requested` cannot send another Cancel or message. Manual status can
  recover the authoritative `cancelled` result.
- `committing` cannot cancel or send another message. Manual status can recover
  the authoritative `completed` result and refresh the real Session.
- Existing request- and operation-generation guards prevent an old POST,
  Cancel, or status response from overwriting a newer Turn or Session.
- Transport loss retains the draft and active Turn identity; the user explicitly
  checks status after the Gateway returns.

## Certification

- Focused cancellation/Store/Conversation/Agent/HTTP/Dashboard/baseline matrix:
  273 tests passed.
- Session, RunJournal, Gateway, TUI, Memory, Skill, and MCP compatibility matrix:
  315 tests passed.
- Baseline tests: 83 passed; active semantic baseline contract: 84 passed.
- Isolated wheel build/install and installed Gateway smoke: 9 passed, including
  assets, health, Chat turn/cancel/status, Session, linked Run, and restart
  reconciliation.
- Authoritative full suites: `2218 passed, 2 skipped, 3 warnings` in 118.89s and
  119.05s. The warnings are the repository's existing benchmark markers.
- Scoped Ruff, explicit `py_compile`, full `compileall -q minicode scripts
  tests`, and `node --check` for both production JavaScript files passed.
  Repository-wide Ruff remains at 686 unrelated pre-existing findings.
- Runtime dependency list remains empty.

Active production baseline is v17. Its manifest SHA-256 is
`2ac1d7185488dd1008407e4711fc3777213dcc1cd405e104f44bf6ca20206857`.
The exact v16→v17 delta is the three changed production files
`minicode/conversation.py`, `minicode/conversation_turn_store.py`, and
`minicode/web/static/assets/app.js`; no file was added or removed from the
protected set, and every v1-v17 manifest pin passes.

The official semantic evaluator passed 108 cases with 37 confirmed gaps and
zero remote calls. Accepted gold remained SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size `3,033,592`, mtime ns `1784135857000000000`.

## Browser acceptance and cleanup

An isolated real Gateway and deterministic safe Runtime exercised the formal
page at 1280×900:

- normal Chat completion;
- accepted-boundary cancellation;
- `cancel_requested` manual recovery to `cancelled`;
- commit-wins recovery from `committing` to `completed`;
- lost original response with retained draft and manual status action;
- actual Gateway stop/start followed by abandoned-running reconciliation to
  `interrupted`.

The three columns measured 208, 682, and 380 px. Document and viewport width
were both 1280 px, so horizontal overflow was false. Page console warning/error
logs were empty. The DOM contained no `Bearer`, `/Users/`, `/private/`, fixture
system prompt, `[object Object]`, secret, or exposed absolute workspace path.

The viewport override was reset and browser tabs finalized. The temporary
Gateway was stopped, port 18765 was checked for no listener, and the isolated
fixture script, HOME/workspace, evaluator output, and wheel/install artifacts
were removed.

## Scope left unchanged

This batch does not enter Batch 7. It adds no polling, SSE, WebSocket, long
polling, queue, asynchronous job system, Session write endpoint, historical
backfill, Memory/Skill/MCP algorithm change, pricing change, or third-party
runtime dependency. Batch 7 may consume the existing Turn ID, Cancel POST,
status GET, Session marker, and state vocabulary, but must not introduce a
second authority or infer truth from response arrival order.
