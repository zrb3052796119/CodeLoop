# MiniCode Dashboard Batch 7B

Batch 7B makes the existing versioned SSE transport the formal Dashboard
invalidation primary and retains the Batch 7A Change Feed controller as a
fallback. Business data continues to come only from the existing REST loaders.

## Frontend composition

`createRealtimeRefreshController()` owns the only formal
`EventSource('/api/v1/events')`, the existing polling controller adapter,
visibility handling, reconnect fencing, and the public live-status state.
Both transports enqueue into one `createResourceRefreshQueue()`; its sole
business callback is the existing `refreshChangedResources(resourceNames)`.

The queue merges names in the fixed Runs, Sessions, Turns, Memory, Skills, and
Connections order. A full request supersedes targeted pending work, at most one
drain executes, work arriving during a drain is retained for the next drain,
and refresh failures do not wedge later work.

## Event contract

The client accepts only `stream.ready`, `resources.changed`, and `stream.reset`.
It rejects payloads larger than 4 KiB before parsing, non-exact object schemas,
invalid v1 timestamps, malformed stream/event/revision identifiers, invalid
status or reset values, unordered/duplicate/unknown resource names, and invalid
retry bounds. The 64-bit hexadecimal sequence is compared with `BigInt`.

- `stream.ready`: establish epoch/sequence and enqueue one full REST resync.
- `resources.changed`: ignore duplicate/stale sequence; enqueue validated names,
  or a full resync if the sequence has a gap.
- `stream.reset`: accept the new epoch/sequence and enqueue one full resync.

Malformed protocol data closes that source, preserves visible business data,
starts polling fallback, and schedules one bounded replacement. Native
EventSource reconnect keeps the same object so browser Last-Event-ID behavior
is preserved. If the browser declares the native source permanently `CLOSED`
after a terminal HTTP failure, the coordinator releases it and uses that same
single bounded replacement path; `CONNECTING` sources are retained. Hidden
pages close transports and resume with a fresh source.

## Authority and scope

SSE data is never placed in a business store, DOM, Web Storage, Chat action, or
console. Chat submit/cancel/status, durable Turn identity, Session persistence,
RunJournal, Memory, Skills, MCP, and all backend SSE schemas remain unchanged.
There is no token streaming, WebSocket, permission flow, new event type, or new
runtime dependency in this batch.

The production freeze for this change is v20. Batch 7C and Batch 8A remain
separate future work.

## Acceptance record

- Focused final matrix: 228 tests passed. The complete suite passed twice after
  the evaluator: 2,305 passed, 2 skipped, with only the three existing benchmark
  marker warnings.
- The official semantic evaluator remained 108 cases / 37 confirmed gaps /
  Phase 3B true / zero remote calls / pass. The accepted gold SHA, size, and
  mtime stayed byte-for-byte unchanged.
- The offline wheel contains the final HTML/CSS/JavaScript and existing SSE,
  Change Feed, Chat, Turn, Session, Run, and static routes; isolated installed
  smoke passed.
- At 1280×900 the browser held exactly one SSE connection, produced no periodic
  polling while healthy, updated external Run/Timeline/Session/Turn state,
  retained draft/focus/selection/current choices, fell back to polling when SSE
  was unavailable, and returned to SSE with polling stopped. All 8 main routes
  and 5 Memory routes rendered without overflow or console warnings/errors.
