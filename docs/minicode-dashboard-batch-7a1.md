# MiniCode Dashboard Batch 7A.1

## Outcome and boundary

Batch 7A.1 adds a versioned, read-only Server-Sent Events transport at
`GET /api/v1/events`. It emits only opaque resource invalidations derived from
the existing `DashboardChangeFeed`; Run, Session, Turn, Memory, Skill, MCP, and
REST projections remain authoritative.

The formal Dashboard deliberately continues to poll `GET /api/v1/changes`.
There is no production `EventSource`, token stream, permission UI, write API,
watcher, durable event database, distributed coordinator, or Batch 7B behavior.

```text
persisted authorities
        |
DashboardChangeFeed.snapshot()     (one bounded metadata sampler)
        |
DashboardEventStream               (one process epoch + ring of 256)
        |
GET /api/v1/events                 (at most 8 subscribers)
        |
test-only EventSource / HTTP client

formal app.js ---> GET /api/v1/changes ---> existing REST reloads
```

## Composition and lifecycle

`minicode.web.event_stream.DashboardEventStream` is the single transport owner.
The Gateway constructs one `DashboardChangeFeed`, passes that same object to one
event stream, injects both into `ThreadingHTTPServer`, starts the sampler before
serving, and closes the stream before closing the server.

The daemon sampler establishes one initial baseline and emits nothing for it.
Every later sample compares only the closed `(status, revision)` pair for Runs,
Sessions, Turns, Memory, Skills, and Connections. One or more differences become
one merged `resources.changed` event in that fixed order. Snapshot failures are
contained locally and are never serialized. `close()` is idempotent, stops the
sampler, and immediately wakes all waiting subscribers.

## HTTP contract

`GET /api/v1/events` accepts no query fields. An absent `Accept` header is
allowed; an explicitly incompatible value returns fixed JSON 406. Invalid,
duplicate, or overlong `Last-Event-ID` headers return fixed JSON 400 before an
SSE subscriber is opened. A missing/closed stream returns 503
`events_unavailable`; a full client budget returns 503 `stream_busy`.

A successful response has:

- `Content-Type: text/event-stream; charset=utf-8`
- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`
- no `Content-Length` and no CORS header

Writes have a five-second socket timeout. Disconnects, resets, and timeouts close
only that subscriber and never stop the shared sampler or other clients.

## Event schema v1

Every business frame is UTF-8, single-line compact JSON, at most 4 KiB, and uses
an ID of the form `evt_<32 lowercase hex epoch>_<16 lowercase hex sequence>`.
The epoch is generated once per `DashboardEventStream` instance and changes on
Gateway restart.

`stream.ready` is sent for a new connection:

```json
{"schemaVersion":1,"type":"stream.ready","streamId":"stream_<epoch>","generatedAt":"<UTC>","retryMs":2000}
```

`resources.changed` is the only retained event and the only event that advances
the sequence:

```json
{"schemaVersion":1,"type":"resources.changed","generatedAt":"<UTC>","resources":[{"name":"runs","status":"live","revision":"rev_<64 lowercase hex>"}]}
```

`stream.reset` tells a client to discard transport history and reload all six
REST authorities. Its reason is exactly `stream_restarted` or
`replay_unavailable`:

```json
{"schemaVersion":1,"type":"stream.reset","generatedAt":"<UTC>","reason":"replay_unavailable","resources":["runs","sessions","turns","memory","skills","connections"]}
```

Heartbeat is only the comment `: heartbeat` followed by a blank line. It carries
no timestamp or data, does not advance sequence, does not enter the ring, and
does not alter `Last-Event-ID`.

## Cursor and replay rules

- No cursor: send `stream.ready`, then wait for live events.
- Current epoch and retained cursor: replay retained events strictly after the
  cursor, in sequence order, without repeating the cursor itself.
- Current sequence: wait; heartbeat may demonstrate liveness.
- Cursor older than retained history or ahead of current sequence: send
  `stream.reset(reason=replay_unavailable)` and continue from current.
- Cursor from another epoch: send
  `stream.reset(reason=stream_restarted)` and continue from current.
- Malformed cursor: return JSON 400 before SSE headers and never echo it.

Delivery is process-local replay with at-least-once recovery while the required
event remains in the ring. It is not a durable audit log and cannot query files
or history by cursor.

## Resource and security budgets

- one sampler per Gateway, regardless of subscribers;
- 256 retained `resources.changed` events;
- at most 8 concurrent subscribers;
- 15-second heartbeat;
- 2-second retry hint and feed-directed 1–10 second sample interval;
- 4 KiB maximum business frame and 64-character cursor limit;
- five-second write timeout;
- one condition-protected shared state and per-subscriber cursors.

The ring contains only the six fixed resource names, status, opaque revisions,
timestamps, fixed reasons, and stream identifiers. It contains no prompt,
message, response, Memory/Skill body, Tool I/O, MCP command/configuration/secret,
Session/Run/Turn identifier, machine path, client metadata, or exception text.
A slow subscriber cannot block sampling; after ring overflow it receives a reset.

## Cross-process and compatibility behavior

Tests mutate the real RunJournal, ConversationTurnStore, Session store, legal
Memory files, legal Skill summaries, and MCP configuration from independent
Python processes. The existing Change Feed observes their persisted metadata,
and the shared event stream emits only the corresponding resource invalidation.
Two clients receive the same retained event ID without creating another sampler.

`/health`, `/api/v1/changes`, Chat, Cancel, Status, `/run`, static assets, and
unknown-API behavior are unchanged. Installed-wheel smoke covers SSE ready,
changed delivery, disconnect, `Last-Event-ID` replay, and all those existing
routes outside the source tree. Runtime dependencies remain empty.

## Batch 7B seam

A later Batch 7B may create one formal `EventSource('/api/v1/events')` and map
`resources.changed`/`stream.reset` to the existing
`refreshChangedResources(resourceNames)` authority reload seam. It must retain
REST as truth, one scheduler/connection owner, current stale-response fencing,
visibility behavior, and polling fallback. Batch 7A.1 does not activate that
consumer.

## Acceptance evidence

- Event Stream/HTTP/cross-process/Change Feed/formal polling/Dashboard/Chat/
  Cancel/Status/Turn/packaging matrix: 244 passed.
- Final full regression passed twice around the official evaluator:
  `2296 passed, 2 skipped, 3 warnings` in 133.84s and 133.58s. The three warnings
  are unchanged unregistered benchmark markers.
- Official semantic evaluation: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls, and `evaluation_passed=true`. Accepted gold remained SHA
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns 1784135857000000000.
- Active v19: 36/36 protected files, deterministic candidate equality, exact
  two changed + one added + zero removed lineage, and every v1–v19 pin valid.
- Scoped Ruff, explicit `py_compile`, complete `compileall`, and all production
  JavaScript `node --check` passed. Repository-wide Ruff remained the exact 686
  pre-existing findings. The offline wheel contained all Python/static assets;
  installed-wheel smoke passed in both full suites. Dependencies remain `[]`.
- Isolated 1280×900 browser: all eight main routes and five Memory routes
  rendered; columns measured 208/682/380 px; horizontal overflow and console
  warning/error count were zero; no absolute path, secret, or `[object Object]`
  appeared. A real external-process Run changed the nav count and rendered via
  the unchanged formal Change Feed polling controller without reload.
- The in-app Browser evaluation sandbox does not expose a constructible native
  `EventSource`. Per the acceptance fallback, a standard-library client against
  the same real Gateway proved ready, two-client identical event IDs, three
  monotonic changed events, Last-Event-ID ordered replay, future reset, old-epoch
  reset, and content-free heartbeat. Existing deterministic HTTP tests cover
  expired-ring reset and slow-client overflow.
- Gateway loss retained rendered data and showed reconnecting; restart recovered
  to live, and a prior process cursor received `stream_restarted`. Task-owned
  listeners, processes, browser viewport/tabs, homes, reports, wheels, and scripts
  were removed. The user's independent Gateway remained running and untouched.

Batch 7A.1 implements no Batch 7B, 7C, or 8A capability.
