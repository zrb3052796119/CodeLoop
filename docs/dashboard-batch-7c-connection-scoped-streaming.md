# MiniCode Dashboard Batch 7C: Connection-scoped Assistant and Tool Streaming

## Outcome

Dashboard Chat now requests `application/x-ndjson` and presents genuine provider
Assistant deltas plus redacted Tool lifecycle rows while the existing synchronous
request is running. This presentation is owned by that one HTTP connection. It
is never persisted, replayed, broadcast, logged, or used to construct the final
answer. The committed Session reread through REST remains the only conversation
authority.

The existing `application/json` Chat contract, one content-free global SSE,
Change Feed fallback, cancellation, Turn recovery, Session commit, RunJournal,
TUI, Headless, Memory, Skill, MCP, pricing, and permission behavior are retained.

## Initial audit and RED evidence

Before production edits, the isolated suite passed 2305 tests with 2 skips and
the 3 existing unregistered benchmark-marker warnings. Active v20 matched all 36
protected files; the official evaluator remained 108 cases / 37 confirmed gaps /
0 remote calls / pass.

The audit established these real seams:

- OpenAI calls `on_stream_chunk` only for `choices[0].delta.content`; Tool call
  arguments are accumulated separately.
- Anthropic calls the Assistant callback only for `text_delta`; `thinking_delta`
  uses a separate callback and is not connected.
- ModelSwitcher retries through the same `_model_next()` callback path, so it did
  not require a production change.
- serial and concurrent Tool callbacks can originate from different worker
  threads and finish out of order.
- the old `AgentTurnRuntime.execute()` exposed only Run observation callbacks;
  the old Chat HTTP boundary returned one JSON body after completion.
- refresh recovery could read durable Turn/Session state only and had no partial
  body to replay, which remains the required disconnect behavior.

The RED contracts first failed on the missing presentation argument, missing
pre-completion delta/Tool frames, missing NDJSON writer, and absent in-memory
frontend parser/store. The production implementation was then added through
those same seams.

## Final call graph

```text
Dashboard fetch POST /api/v1/chat/turns (Accept: application/x-ndjson)
  -> serve_chat_turn() validates the complete JSON request
  -> ChatStreamWriter sends ready and owns NDJSON framing/lock/budgets
  -> ConversationTurnService.turn(..., presentation=writer)
  -> AgentTurnRuntime.execute(..., presentation=writer)
       -> genuine provider on_assistant_stream_chunk -> assistant_delta()
       -> Tool callback -> RunObservation callback (durable fact)
                        -> presentation callback (connection-only UI)
  -> Conversation commits final Session and completes Turn/Run
  -> ChatStreamWriter sends metadata-only completed terminal
  -> frontend rereads Sessions/Runs/Overview/Ops REST
  -> committed Session replaces provisional DOM

GET /api/v1/events remains a separate, content-free invalidation stream.
```

## Presentation interface

`minicode.conversation_presentation.ConversationPresentation` contains only:

```python
assistant_delta(text)
tool_started(tool_name)
tool_finished(tool_name, is_error=...)
```

The three safe emit helpers catch `BaseException`. Run observation and
presentation are invoked and isolated independently, so either observer can
fail without changing Provider/Tool execution or the other observer. `None` is
an exact no-presentation path. Conversation signature inspection passes the new
argument only to runtimes that explicitly accept it, preserving legacy fakes.
Core modules do not import `minicode.web`.

## NDJSON v1 contract

All frames contain integer `schemaVersion: 1`, the validated `turnId`, and a
strictly increasing integer `sequence` starting at zero. Each encoded UTF-8 line
is at most 4096 bytes and uses an exact field allowlist.

Supported types are:

- `chat.stream.ready`
- `chat.assistant.delta` with non-empty `text`
- `chat.tool.started` with safe `toolName` and connection-local
  `toolstream_<32 hex>`
- `chat.tool.finished` with `success|error`, `paired`, and an ID only when paired
- `chat.stream.truncated` with `assistant|tools`
- `chat.turn.completed` with final Session/Run metadata but no answer body
- `chat.turn.error` with a fixed safe domain code

Assistant chunks are split on Python string/UTF-8-safe boundaries. The temporary
Assistant budget is 128 KiB and the Tool event budget is 512; each category emits
at most one truncation frame and terminal delivery is still attempted. One
`RLock` protects sequence assignment, same-name Tool FIFO pairing, budget state,
and complete-line writes. A write failure atomically detaches the writer; every
later emit becomes a no-op while the synchronous Agent request continues.

Valid NDJSON responses are HTTP 200 with
`application/x-ndjson; charset=utf-8`, `Cache-Control: no-store`, `nosniff`,
`X-Accel-Buffering: no`, no content length, connection close, per-frame flush,
and a bounded default five-second socket write timeout. Pre-header validation
still returns structured JSON 4xx. Post-header failure can emit only the safe
terminal frame.

## Assistant and Tool semantics

Only actual provider Assistant callbacks reach `assistant_delta`; final
Assistant messages, fallback/progress callbacks, Tool results, and thinking are
not reused as fake tokens. Providers without a stream callback can emit no
deltas and still complete normally through final Session REST.

Tool names must match `^[A-Za-z0-9_.:-]{1,128}$`; invalid values become
`unknown`. Starts allocate opaque connection-only IDs. Same-name finishes pair
under the writer lock in start FIFO order. A finish without a start is
`paired=false` and carries no fabricated ID; dangling starts are not repaired.
No input, output, duration, operation ID, path, command, URL, exception, or
credential enters the projection.

## Frontend authority and races

The formal app has one memory-only `chatStreamStore`. A fatal streaming
`TextDecoder`, bounded 8 KiB tail, 4 KiB line cap, exact validators, Turn and
generation fences, sequence duplicate/backward suppression, and sequence-gap
invalidation prevent partial stream data from becoming trusted state.

Rendering is coalesced with `requestAnimationFrame`. Assistant text is explicitly
provisional, Tool rows show only name/running/success/error, escaping uses the
existing `esc()` seam, near-bottom auto-follow respects user scroll, and only
phase changes use `aria-live`.

Completion is terminal-deduplicated across stream, Cancel, Status GET, and SSE
Turns invalidation. The provisional region is removed only after the committed
Session has been loaded. A failed reread retains truthful partial state. A stream
disconnect retains the active Turn marker and partial text but never POSTs
again; refresh restores only durable Turn status, never the partial body.
`cancel_requested` remains authoritative even if a late delta arrives, and the
UI states that already-started Tool side effects are not rolled back.

## Security and non-persistence proof

Tests and browser fixtures seed forbidden Tool input/output, a command, URL,
absolute path, and secrets. None appears in NDJSON, DOM, global SSE, Change Feed,
TurnStore, RunJournal, Session presentation fields, logs, diagnostics, or browser
storage. The only content-bearing stream field is provider Assistant delta text.
The global `/api/v1/events` transport remains content-free and the formal source
still constructs exactly one `EventSource('/api/v1/events')`.

## Changed production interfaces

- `minicode/conversation_presentation.py` — small core Protocol and no-throw
  presentation calls.
- `minicode/agent_runtime.py` — optional genuine Assistant/Tool callback
  composition.
- `minicode/conversation.py` — optional presentation propagation with legacy
  signature compatibility.
- `minicode/web/chat_stream.py` — strict, bounded, thread-safe NDJSON writer.
- `minicode/web/chat_http.py` — optional media negotiation and synchronous
  connection-scoped response.
- `minicode/web/static/assets/app.js` — strict parser, memory-only store,
  rendering, REST finalization, and race guards.
- `minicode/web/static/assets/styles.css` and `minicode/web/static/index.html` —
  restrained provisional UI and accurate capability copy.

`gateway.py`, Agent Loop, providers, Event Stream, Change Feed, persistence,
Memory, Skill, MCP, TUI, Headless, and permission code required no production
change.

## Certification and packaging

- v21 manifest SHA-256:
  `5a6422b0ae18649166e3e8d28c990a9736f457093f105db661f7ff4b40d8a8ff`.
- verifier: active v21, candidate equality, 38/38 current protected files,
  exact lineage, and v1-v21 manifest integrity all true.
- official evaluator: 108 cases, 37 confirmed gaps, Phase 3B gate true, remote
  calls 0, evaluation passed.
- accepted gold stayed SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns `1784135857000000000`.
- final wheel SHA-256:
  `68dba349533ef1206dfcbe85f5099855cebef35a063fd1306ae37d747a28059d`
  (882,256 bytes). An isolated install loaded the package outside the source
  tree and passed root/assets, both health routes, content-free SSE, Change Feed,
  JSON and NDJSON Chat, Status, Cancel, and `/run` smoke tests.
- `dependencies = []` remains unchanged. `python -m build` was unavailable in
  this environment, so the certified offline equivalent was
  `python -m pip wheel . --no-deps --no-build-isolation`.

## Verification

- focused formal Dashboard/live-refresh/frontend tests: 78 passed.
- production-baseline suite: 114 passed.
- final full suites: 2338 passed, 2 skipped, 3 existing benchmark warnings in
  137.53s, 137.44s, and the post-verifier/evaluator run in 138.49s.
- scoped Ruff, `py_compile`, full `compileall`, and every formal
  `node --check`: passed.
- repository-wide Ruff remained the same 686 historical findings and was not
  expanded into unrelated cleanup.

## Browser acceptance

An isolated real Gateway at 1280x900 used a controlled slow runtime but real
Conversation, Turn, Session, Run, REST, Change Feed, and SSE components.

- pre-completion samples were `Browser `, `Browser stream `, and
  `Browser stream complete.` with zero final rows; Tool rows visibly moved from
  running to both success and error.
- the terminal caused a Sessions REST reread; the provisional row disappeared
  and reload showed only the committed message.
- a forced response reset retained `Disconnected ` and displayed
  `连接已中断；临时内容不完整、未确认`; the durable Turn later converged to the
  committed Session without re-POST.
- cancellation visibly reached `cancel_requested`; a late `late ` delta arrived
  while that phase remained authoritative, then the durable terminal became
  cancelled with explicit non-rollback copy.
- after a clean fixture restart the route counter recorded exactly one
  `/api/v1/events`, zero `/api/v1/changes`, and one Chat POST; the page remained
  `实时（SSE）` through Chat and repeated SSE reconnects.
- all 8 main routes and 5 Memory routes rendered. Columns measured 208 / 682 /
  380 px, with no overlap or horizontal overflow.
- page Console warning/error count was zero. DOM scans found no seeded secret,
  Tool input/output, command, URL, absolute path, thinking/reasoning text, or
  `[object Object]`.

The in-app browser intentionally does not expose page Resource Timing to the
automation evaluator. The fixture's server-side path counter plus deterministic
frontend transport tests provide the one-SSE/zero-polling evidence instead of
claiming unavailable browser telemetry.

## Scope boundary

Batch 7C does not add permission approval, Allow once/Deny, MCP control,
WebSocket, background jobs, replay/persistence of partial text, thinking stream,
Tool input/output stream, TUI token streaming, database, dependency, or automatic
Turn resend. Dashboard file writes may still be refused; that remains Batch 8A.
