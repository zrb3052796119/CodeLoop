# MiniCode Dashboard Batch 6B-2A

## Outcome and truth ownership

Batch 6B-2A gives every synchronous Dashboard Chat request a durable,
workspace-scoped identity. A client-created `turn_<32 lowercase hex>` is claimed
before Session, Run, runtime, or Agent work begins. Reusing the same identity
cannot silently execute a second Agent within the supported single-Gateway
scope, and a refresh or lost HTTP response can be reconciled through one
read-only status request.

The three facts have deliberately separate owners:

| Fact | Authority | Purpose |
|---|---|---|
| Turn | `ConversationTurnStore` | request identity, fingerprint, state, and safe references |
| Session | Session base/deltas | authoritative user/assistant content and internal commit marker |
| Run | RunJournal | best-effort lifecycle/usage telemetry; never the request identity database |

The Turn Store never copies message or Assistant content. A completed response
is reconstructed from the Session marker and indexed Assistant message. A Run
may be unavailable without invalidating a truthful Session/Turn commit.

## Production call graph

```text
Chat Dock
  -> generate turnId with Web Crypto
  -> persist only workspaceId + turnId + targetSessionId in sessionStorage
  -> POST /api/v1/chat/turns
  -> strict minicode.web.chat_http boundary
  -> ConversationTurnService.turn()
       -> hash workspace + session/new marker + complete normalized message
       -> ConversationTurnStore.claim()
       -> accepted -> running
       -> load/create workspace-scoped Session; attach safe Session reference
       -> observe one source=gateway Run; attach runId or null
       -> construct one Agent runtime; execute once
       -> save user + assistant + exact internal turn marker together
       -> running -> completed
  -> synchronous success
  -> refresh Sessions/detail + Runs + Snapshot + Ops

Refresh or response loss
  -> exactly one GET /api/v1/chat/turns/{turnId}
  -> completed: recover content through Sessions API and refresh read stores
  -> accepted/running: show manual Check status; never poll or resend
  -> failed/interrupted/missing: fixed local state; never rerun
```

## Identity and fingerprint contract

`turnId` has one closed grammar: `turn_[0-9a-f]{32}`. The formal browser uses
`crypto.getRandomValues`; the compatible server fallback uses `secrets.token_hex`.
Types, whitespace, uppercase, path characters, wrong length, and non-strings are
rejected before runtime construction.

The stored request fingerprint is:

```text
sha256(
  "minicode.dashboard.chat-turn-fingerprint.v1\\0" +
  workspaceId + "\\0" +
  (sessionId or "<new-session>") + "\\0" +
  complete normalized message
)
```

Only `sha256:<64 lowercase hex>` is stored. The preimage, message, prompt, and
Assistant response are not retained in a Turn record.

## Turn Store contract

The per-workspace location is:

```text
<MINI_CODE_DIR>/dashboard/workspaces/<workspaceId>/turns/<turnId>.json
```

Schema version 1 contains only `turnId`, workspace ID, request fingerprint,
closed status, safe Session/Run references, timestamps, fixed error code, exact
commit indexes, and a private process-owner token. It excludes content,
transcripts, tool/Memory/Skill/MCP data, credentials, provider identity, raw
errors, and absolute paths. The owner token and fingerprint are never projected
by HTTP.

The state set is `accepted`, `running`, `completed`, `failed`, and
`interrupted`. Terminal states never transition back to running. Records are
strictly parsed with bool/int separation, fixed keys, closed enums and ID/time
grammars, and a 16 KiB limit. Reads use no-follow regular-file identity checks;
directory construction rejects symlink escape. Writes are `0600` same-directory
temporary files followed by flush, fsync, atomic replace, and best-effort
directory fsync.

Retention is bounded and best effort: at most 20,000 directory entries are
scanned during a new claim, the target is 10,000 records, terminal records older
than 90 days are eligible for removal, and temporary files older than one day
are eligible for cleanup. Active records are retained. A malformed target is a
fixed local failure, while unrelated records remain usable.

## Duplicate and crash semantics

For an existing `turnId`:

- different fingerprint returns `turn_id_conflict` before Agent/Run work;
- a live in-process accepted/running claim returns `turn_in_progress`;
- completed returns the authoritative Session-backed result without another
  runtime, Run, Agent call, or message copy;
- failed/interrupted returns the fixed original terminal failure and never
  reruns;
- an accepted/running record left by another owner reconciles to completed only
  when its referenced Session has a valid marker; otherwise it becomes
  interrupted.

The internal Session marker is saved atomically with the finished message list:

```json
{
  "schemaVersion": 1,
  "turnId": "turn_...",
  "userMessageIndex": 1,
  "assistantMessageIndex": 2
}
```

Loading validates exact roles and indexes, rejects duplicate/invalid markers,
and defaults old Sessions to an empty marker list. The marker is absent from the
Sessions API and DOM.

This resolves the critical `Session committed -> Turn completed write missing`
window: restart finds the exact marker and promotes the Turn without replay. If
Agent execution happened but Session did not commit, no authoritative result
exists; restart marks the Turn interrupted. Time and content similarity are
never used to guess completion.

## HTTP contract

`POST /api/v1/chat/turns` remains synchronous and accepts only:

```json
{"message":"required text","sessionId":null,"turnId":"turn_<32 hex>"}
```

`turnId` is temporarily optional for Batch 6B-1 compatibility. Success adds
the validated/generated `turnId` to the existing versioned `read-write`
response. Strict content type/UTF-8/Content-Length/body/message limits,
duplicate-key rejection, unknown-field rejection, no query, and workspace
ownership remain unchanged.

`GET /api/v1/chat/turns/{turnId}` is versioned, `no-store`, read-only, rejects
queries and malformed IDs, and returns only:

```json
{
  "ok": true,
  "schemaVersion": 1,
  "mode": "read-only",
  "turnId": "turn_...",
  "status": "completed",
  "sessionId": "...",
  "created": true,
  "runId": "run_... or null",
  "createdAt": "...",
  "updatedAt": "...",
  "completedAt": "... or null",
  "errorCode": null,
  "resultAvailable": true
}
```

Missing/foreign records use the same fixed `turn_not_found` 404. New fixed
errors are `turn_id_conflict`, `turn_in_progress`, and `turn_interrupted`.
Validated errors may echo only the already validated `turnId`. There is no
Assistant content, fingerprint, owner, commit marker, or path in status output.
No DELETE, PATCH, cancel, or management route exists.

## Frontend recovery

The independent `chatStore` owns the active ID and target. Before fetch, the
formal client generates and stores only a version, workspace ID, active turn ID,
and target Session ID. It never stores message, Assistant content, draft, or
error text. The same ID survives the in-flight request and network-response
loss.

After Session/Snapshot initialization, refresh performs at most one active-turn
reconciliation. Completed clears the marker and refreshes Sessions/detail,
Runs, Snapshot, and Ops. Accepted/running retains the identity, disables another
send, and offers a manual Check status action. Failed/interrupted/missing clears
the active identity, presents fixed text, and retains the in-memory draft when
available. Status transport failure remains local to the Dock. There are no
timers, polling, automatic resend, SSE, WebSocket, streaming, or cancel control.

## At-most-once scope and limits

The accurate guarantee is: for cooperating concurrent requests in one Gateway
process, one `turnId` claims at most one Agent execution; a durably completed
Session marker prevents execution after Gateway restart. This is not a provider
exactly-once guarantee. A provider may have accepted work immediately before a
process failure while MiniCode lacks a Session commit; the only honest state is
interrupted, and MiniCode does not replay it automatically.

The Turn Store's in-memory claim lock is process-local. The existing Session
Store keeps its POSIX cross-process writer lock, but the Turn Store does not
claim multi-Gateway, multi-machine, NFS, distributed lease, fencing, heartbeat,
or indefinite-retention coordination. A retained terminal record and Session
marker define the restart guarantee; records removed by the documented
retention policy are no longer queryable by Turn ID.

## Certification

- Pre-edit: `2095 passed, 2 skipped, 3 warnings in 138.08s`.
- Turn Store/identity/Conversation focus: 34 passed; Chat HTTP/restart: 40
  passed; compatibility matrix: 133 passed.
- Dashboard Web: 62 passed; all Dashboard tests: 234 passed; installed-wheel
  packaging matrix: 9 passed.
- Baseline contract: 63 passed; semantic evaluator contract: 32 passed.
- Modified-file Ruff, `py_compile`, repository `compileall`, formal/prototype
  `node --check`, v15 verifier, and local HTTP smoke passed. A repository-wide
  Ruff audit still reports 82 pre-existing unrelated diagnostics.
- Official evaluator: 108 cases, 37 confirmed gaps, 0 remote calls; semantic
  behavior projection and per-case fingerprints remained pinned.
- Evaluator-after full suite: `2144 passed, 2 skipped, 3` existing benchmark
  marker warnings in `107.18s`.

At 1280×900, an isolated real Gateway plus deterministic fake Agent verified a
normal XSS-bearing turn, response-loss-by-refresh while running, one refresh
reconciliation, no polling, manual running/completed checks, Session recovery,
and fixed failed/interrupted behavior without resend. All eight main routes and
five Memory routes rendered. The three columns had no overlap or horizontal
overflow; the Dock accurately displayed `synchronous · recoverable · no live
updates`. Console warning/error count was zero, and no absolute path, fingerprint,
owner, secret, `[object Object]`, or executable XSS appeared.

## Deferred boundary

Batch 6B-2B may add explicit cancellation semantics using `turnId` as its stable
key, but must define races and truthful terminal behavior rather than mutate
completed/failed/interrupted records back to running. Batch 7 may consume the
same read-only Turn status for live presentation, but may not create another
identity store. Background queues, streaming, push transports, provider
deduplication, authentication, controls, and multi-user/distributed coordination
remain unimplemented.

