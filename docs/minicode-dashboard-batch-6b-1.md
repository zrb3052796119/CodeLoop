# MiniCode Dashboard Batch 6B-1

## Outcome

Batch 6B-1 adds one synchronous, Session-backed Dashboard conversation turn.
The formal right-hand Chat Dock can create a Session or continue a selected
Session, execute the real Agent composition once, record one linked
`source=gateway` Run, commit the finished turn, and then refresh the existing
read-side views. `/run` remains the legacy Headless compatibility endpoint and
still records a Gateway Run with `sessionId=null`.

No streaming, polling, background jobs, cancellation, authentication, Memory
algorithm changes, Session lock redesign, management writes, or Batch 6B-2 work
is included. Runtime dependencies remain empty.

## Production call graph

```text
Chat Dock
  -> POST /api/v1/chat/turns
  -> minicode.web.chat_http (strict bounded transport)
  -> ConversationTurnService.turn()
       -> load/create workspace-scoped Session
       -> observe_run(source=gateway, session_id=<real Session>)
       -> create_agent_turn_runtime()
       -> AgentTurnRuntime.execute() -> run_agent_turn()
       -> one Session save after Agent execution
       -> assistant.completed + completed Run
  -> success envelope
  -> refresh Sessions + Session detail + Runs + Snapshot + Ops
```

`minicode.agent_runtime` is the shared stable composition seam used by both
Headless and Dashboard Chat. It owns configuration, model, tools, permissions,
MemoryManager, capability/intent/Skill routing, system prompt, and the call to
the existing Agent Loop. Gateway and HTTP modules remain thin composition and
transport layers.

## HTTP contract

`POST /api/v1/chat/turns` accepts only UTF-8 `application/json`, a single valid
decimal Content-Length no larger than 65,536 bytes, no query string, no
duplicate JSON keys, and exactly these fields:

```json
{"message":"required non-empty text, max 32000 chars","sessionId":null}
```

`sessionId` may be omitted/null for a new Session or use the closed local ID
grammar for an existing Session. Unknown fields, duplicate keys, NUL, malformed
JSON/UTF-8, wrong charset/content type, bad length, empty/oversized message, and
invalid IDs fail before any runtime construction.

Success is returned only after the Session commit:

```json
{
  "ok": true,
  "schemaVersion": 1,
  "mode": "read-write",
  "sessionId": "...",
  "created": true,
  "assistant": {"role": "assistant", "content": "..."},
  "updatedAt": "...",
  "runId": "run_... or null"
}
```

Errors use fixed safe JSON envelopes and no raw exception text:

| HTTP | code | rule |
|---|---|---|
| 400 | `invalid_request` | transport/body validation failed |
| 404 | `session_not_found` | missing or foreign-workspace Session |
| 409 | `session_conflict` | one acquired-lock stale-revision failure; no retry/merge/rerun |
| 503 | `session_busy` | bounded Session flock contention |
| 503 | `runtime_unavailable` | Agent runtime could not be constructed |
| 500 | `turn_failed` | Agent/no-assistant/commit or other turn failure |

## Transaction semantics

One Web message creates exactly one observed Run and invokes Agent execution at
most once. The Session flock is never held while Agent work runs. A continued
turn loads current state, prepares the current system prompt safely, appends one
user message, executes, verifies a new non-empty assistant, and attempts one
save. A stale save returns 409 while preserving the winning Session; it never
replays the model call.

An ordinary Agent failure or missing assistant performs a best-effort truthful
user-only commit, emits no fake assistant, marks the Run failed, and returns the
fixed `turn_failed` response. A failed required success commit cannot return
`ok`. RunJournal degradation does not prevent a valid Session commit and yields
`runId=null`. Permission turn cleanup and tool disposal are isolated and always
attempted.

Foreign-workspace Sessions are indistinguishable from missing Sessions. The
HTTP contract, Session read model, and DOM renderer expose no absolute path,
raw failure secret, tool payload, system prompt, or hidden transcript.

## Frontend state and safety

The Dock owns an independent in-memory `chatStore` with phase, request
generation, draft, new/existing target mode, last Session, and fixed error. It
disables input/submission while one request is active and ignores stale response
generations. It never automatically retries or resends.

Success clears the draft, selects the committed Session, and explicitly
refreshes Sessions/detail, Runs, Snapshot, and Ops. Conflict refreshes Session
state but retains the draft and requires manual resend. Not-found refreshes and
falls back when selection disappears. 500/503 retain the draft. Only
workspace/Session selection IDs and panel widths use browser storage; Chat
draft/message/content never do. All message/error content uses escaped text.

## Certification

- Service/HTTP/Session/Run/Headless/TUI/Dashboard/MCP focused matrix: 222 passed
  after updating the Gateway composition fixture.
- Conversation service: 11 passed, including real spawned stale conflict and
  real lock busy; strict Chat HTTP/restart: 24 passed.
- Installed-wheel packaging and Gateway/static/Chat smoke: 9 passed.
- Production baseline tests: 54 passed; active v14 protects 26 files with exact
  v13→v14 changed/added sets, candidate equality, and v1–v14 integrity true.
- Ruff, modified-file `py_compile`, repository `compileall`, and both formal
  JavaScript `node --check` commands passed.
- Offline evaluator: 108 cases, 37 confirmed gaps, zero remote calls,
  `evaluation_passed=true`; accepted gold SHA/size/mtime remained
  `5629d6...fdd3b` / `3033592` / `1784135857000000000`.
- Evaluator-after final full suite: `2095 passed, 2 skipped, 3` existing
  unregistered-benchmark-marker warnings in `98.01s`.

At 1280×900, the deterministic isolated browser fixture verified empty-workspace
creation, same-Session continuation, submitting disablement, Agent failure,
manual recovery, a real 409 stale conflict, Gateway restart recovery, history
switching, a second Session, all eight main routes, all five Memory routes, and
Run-to-Session navigation. Measured columns were 208 / 682 / 380 px with no
overlap or horizontal overflow. Console warning/error count was zero. XSS input
rendered only as text; no fixture secret, absolute path, or `[object Object]`
appeared. The browser tab, viewport override, fake Gateway, HOME, workspace, and
temporary data were cleaned.

## Deliberate limits for Batch 6B-2

Batch 6B-2 may design streaming/token progress, cancellation, background or
idempotent job semantics, and any additional authenticated write/control
surface. It must not infer those features from this synchronous endpoint. This
batch supplies only the reusable Agent runtime seam, Conversation Turn Service,
strict synchronous Chat contract, linked Run/Session truth, and explicit
read-store refresh boundary.
