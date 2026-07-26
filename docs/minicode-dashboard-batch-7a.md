# MiniCode Dashboard Batch 7A

## Outcome

Batch 7A adds one read-only Change Feed and one browser Live Refresh Controller.
Existing REST projections, Session persistence, RunJournal, Turn Store, Memory,
Skills, and MCP state remain authoritative. A revision means only “reload this
authority”; it is never a second data model and contains no persisted content.

The transport is a visible-page `GET /api/v1/changes` request at the server's
default two-second interval. It is ordinary bounded polling: there is no SSE,
WebSocket, long polling, filesystem watcher, background worker, queue, token
streaming, or automatic Chat resend.

## Backend interface

`DashboardChangeFeed.snapshot()` is the sole observation interface. The
Gateway injects its resolved Workspace, MiniCode data root, clock, and
process-local MCP current-state adapter. The HTTP layer only enforces the
query-free route and serializes the result with `Cache-Control: no-store`.

The closed response contains:

- schema version 1, `mode=read-only`, `generatedAt`, and `pollAfterMs`;
- exactly Runs, Sessions, Turns, Memory, Skills, and Connections;
- a fixed status and one opaque deterministic `rev_<64 lowercase hex>` per
  resource;
- only fixed, low-cardinality diagnostics.

Revisions are salted by resolved Workspace and hash only bounded metadata facts.
Persisted Run, Session, Turn, Memory, Skill, and MCP configuration bodies are not
opened by the feed. Directory enumeration and stat calls share a 25,000-entry
budget. Directories are opened no-follow, escaping or broken symlinks are
ignored with `partial`, and budget exhaustion has an enumeration-order-independent
revision. One resource failure cannot poison the other five.

Run and Turn roots are natively Workspace-scoped. Session persistence is global
and cannot be attributed to a Workspace without reading message-bearing JSON,
so the feed deliberately uses conservative stat-only invalidation and marks
Sessions `partial` whenever legal Session metadata exists. The existing Session
REST projection performs the authoritative Workspace filter. This is a known,
explicit accuracy boundary rather than a global-activity claim.

Connections combine fixed MCP config file stats with a canonical process-state
snapshot. Volatile `checkedAt` and `updatedAt` fields do not affect revision.
The Gateway reuses the bounded configuration reader only to derive the current
Workspace's opaque server keys, then calls the registry's pre-probe scoped
snapshot. Other Workspace instances are not probed. Commands, arguments,
environment values, credentials, server keys, and configuration names are never
returned by the Change Feed.

## Frontend controller

`createLiveRefreshController()` owns the only data-refresh scheduler. Its
dependencies are injected so virtual-time tests exercise the real controller.

- The first valid response establishes a baseline and performs no reload.
- Later status or revision changes are coalesced by resource and dispatched to
  existing REST loaders before the next Change Feed request.
- At most one request or resource refresh cycle is active.
- Hidden pages cancel the timer, abort the active request, and show
  `已暂停（页面不可见）`; visibility resumes with an immediate request.
- Transport/schema failures retry after 2, 4, 8, 16, then at most 30 seconds;
  success resets the sequence.
- AbortController plus generation fencing prevents old responses from changing
  stores after visibility or lifecycle changes.

Auto refresh preserves the current Run/Session selection and message draft.
Focus, selection, and scroll are restored only when rendering reset them; a user
focus or scroll action that occurs while a request is active wins. Inactive stores
are invalidated for their next route rather than creating per-route timers.
Snapshot, Runs, Sessions, Memory, Skills, Connections, Ops, and runtime trace
continue to use their existing request-generation guards.

Turns are special: a Turns revision may call the existing status GET only when
there is an active durable Turn. It never calls the submit function. A terminal
status fences the original POST generation before applying the authoritative
result, so a late response cannot duplicate completion. A completion reuses the
existing Session/Run/Snapshot/Ops refresh; same-batch resource changes are
coalesced.

## Scope boundary and Batch 7B seam

Batch 7A adds no write or control API, authentication, Run cancellation UI,
MCP process control, Memory/Skill mutation, historical backfill, provider
streaming, watcher, or distributed coordination.

Batch 7B may consume the stable `/api/v1/changes` schema and the internal
`refreshChangedResources(resourceNames)` dispatcher. It must keep existing REST
responses as authority, preserve the single scheduler and generation fencing,
and add any new resource only by advancing the closed schema and immutable
production baseline. It must not infer Agent truth from revision timing.

## Acceptance evidence

- Final focused live-refresh controller: 4 passed; the behavior tests use a
  virtual scheduler and a fake DOM rather than probabilistic sleeps.
- Final full regression passed twice around the official evaluator:
  `2252 passed, 2 skipped, 3 warnings` in 162.85s and 163.03s. The three warnings
  are the unchanged unregistered benchmark markers.
- Installed-wheel smoke built and installed the package outside the source tree,
  served `/api/v1/changes` and static assets, and retained existing health,
  read, Chat, Cancel, Status, and `/run` behavior.
- Active v18 protects 35 files. Exact v17→v18 lineage is five changed files
  (`gateway.py`, `web/http.py`, `app.js`, `styles.css`, `index.html`) and two
  newly protected files (`web/change_feed.py`, `web/read_model.py`), with no
  removal. Manifest SHA-256 is
  `515d3cacd96365bc09bfb608df59ff1bfcc4b0c10cff1d1e4e114cb8ef6ecee5`;
  candidate, current files, and every v1–v18 pin match.
- The official evaluator passed 108 cases with 37 confirmed gaps, Phase 3B gate
  true, zero remote calls, determinism/security true, and
  `evaluation_passed=true`. Accepted gold remained SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  3,033,592 bytes, mtime_ns `1784135857000000000`.
- Scoped Ruff, explicit `py_compile`, complete `compileall`, and every formal
  JavaScript `node --check` pass. Repository-wide Ruff remains the exact 686
  pre-existing findings recorded before edits. Runtime dependencies remain `[]`.
- In an isolated real Gateway at 1280×900, external-process Session/Run writes
  appeared in about three seconds. An open Run Detail advanced from two to four
  Timeline rows, then five rows and completed, without page reload. Gateway loss
  retained data and showed reconnecting; restart recovered automatically and
  retained the Dock draft. All eight main routes and five Memory routes rendered,
  columns measured 208/682/380 px, horizontal overflow was false, console
  warning/error count was zero, and DOM/API checks found no secret, absolute
  machine path, or `[object Object]`.
- The in-app Browser could not make the controlled page genuinely hidden when a
  second automation tab opened, so visibility pause/resume is certified by the
  deterministic controller test rather than a visual claim. A live Provider Chat
  submission was not made from the production Gateway to avoid an external model
  call; installed-wheel fake-runtime and complete Chat/Turn suites certify that
  boundary, including terminal fencing and no resend.
- Temporary listeners, processes, browser tabs/viewport, isolated homes,
  workspaces, wheel/evaluator reports, and test artifacts were cleaned. The
  user's separately running Gateway and data were not modified or stopped.

Batch 7A does not implement any Batch 7B capability.
