# MiniCode Dashboard Batch 8A-2 Permission Approval UI

## Outcome

Batch 8A is closed. The existing loopback-only `PermissionApprovalBroker` is
now visible in the formal Dashboard as a compact, explicit Allow-once / Deny-once
panel. The broker, `PermissionManager`, pending GET, decision POST, Chat Turn,
Cancel, and Tool side-effect checkpoints remain the authority; the browser adds
presentation and stricter fail-closed validation only.

No remote approval, persistent pending state, automatic decision, Chat replay,
permission history manager, or Batch 8B control was added.

## One authority and one realtime path

The production call path is:

```text
Dashboard Chat -> ConversationTurnService -> AgentTurnRuntime
-> Agent Loop Tool worker -> PermissionManager
-> PermissionApprovalSession.prompt() -> PermissionApprovalBroker pending
-> broker revision -> DashboardChangeFeed permissions revision
-> DashboardEventStream -> the existing single EventSource
-> permissionStore -> pending GET -> explicit decision POST
-> waiting PermissionManager -> cancellation checkpoint -> Tool side effect
```

Gateway composition passes the same broker object to Conversation, permission
HTTP, and the Change Feed loader. The non-loopback composition still has no
broker and remains fail-closed. Shutdown closes approval waits before the event
stream and HTTP server.

## Change Feed and SSE schema v2

`permissions` is the seventh and final ordered resource after `runs`,
`sessions`, `turns`, `memory`, `skills`, and `connections`. Adding a member to a
strict resource set is a contract change, so `/api/v1/changes` and the
`stream.ready`, `resources.changed`, and `stream.reset` SSE payloads deliberately
use `schemaVersion: 2`. API URL version and payload schema version remain
separate concerns; cursor, epoch, replay, ring, heartbeat, future-cursor, old
epoch, and slow-client behavior are unchanged.

The optional Change Feed loader reads only `PermissionApprovalBroker.revision()`.
It validates the private `permissionrev_<32 hex>` token and emits a salted,
one-way `rev_<64 hex>` public value. Pending items, opaque IDs, Tool names,
reviews, commands, paths, reasons, choices, and decisions never enter Changes or
SSE. Missing authority is `unavailable`; invalid values and loader faults become
a fixed, resource-local diagnostic without contaminating the other six resources.

SSE remains content-free invalidation. Pending GET is the current-process list
authority and decision POST is the write authority. The frontend still creates
exactly one `EventSource('/api/v1/events')`; the existing Changes polling timer
is the only fallback timer. A permissions change performs only a pending GET.

## Ephemeral permission Store and strict validation

The single in-memory Store records phase, validated items, revision, safe error,
request generation, action generation, acting permission ID, and update time.
It never writes a review to localStorage, sessionStorage, IndexedDB, URL/hash,
console, telemetry, Session, or RunJournal. Initial load, visibility recovery,
SSE ready/reset, relevant invalidation, and polling changes all reconcile from
pending GET. Refresh can recover a pending request in the same Gateway process;
a Gateway restart cannot recover or approve an old request.

The pending validator requires the exact v1 REST envelope and exact item/review
unions, rejects bool-as-version, extra or missing fields, bad opaque IDs,
non-millisecond UTC timestamps, more than 16 items, unsafe relative paths, and
all byte-budget violations. Edit, command, and path reviews are distinguished.
Path is always deny-only. Redacted, truncated, incomplete, contradictory, or
unreviewable edit/command data is deny-only. The fixed sensitive-command review
renders a fixed explanation and no hidden command.

Allow is present only when both the backend declaration and the stricter browser
projection agree: `reviewable=true`, `allow_once` is declared, the union is a
known edit/command review, and it is complete, untruncated, and unredacted from a
successful current-generation GET. `PermissionManager` and the broker still
make the final decision.

## Approval panel and action fencing

The Waku three-column layout is unchanged. The Session dock order is Session
actions, a compact permission region, Chat log, then composer. Only the oldest
of up to 16 stable pending items is expanded and the position is shown as
`1 / N`; authority is reread after every outcome before the next item appears.
Diff and command values are escaped text in labelled code regions with bounded
wrapping/overflow. Opaque IDs are not primary copy and absolute paths are never
rendered.

Allow and Deny are native `type=button` controls with visible focus styling.
They are never a Chat form default. Each action captures permission ID, item
Turn ID, decision, and generation; it is single-flight, disables both actions,
strictly validates the response, ignores stale responses, and reconciles with a
GET. Lost responses are never auto-reposted. Retry performs GET only. The
decision uses `item.turnId`, so another Turn cannot be decided through the
active Chat identity.

Same-Turn pending keeps the composer disabled and retains the existing Chat
Cancel path. Cancel immediately removes/invalidates permission actions, broker
cancel wakes the Tool waiter, and late Allow remains a backend error. Other-Turn
pending is labelled separately and never cancels or rebinds the active Chat.
Assistant/Tool NDJSON remains presentation-only and final Sessions REST remains
the transcript authority. Historical content-free permission Run events are not
used to reconstruct actionable pending state.

## Safety, accessibility, and responsive behavior

Deterministic frontend tests cover strict envelopes/unions/budgets, safe edit and
command Allow, path/redacted/incomplete deny-only behavior, XSS strings, stable
queue order, next-item reconciliation, stale generations, double clicks,
conflicts, lost responses, initial/restart/reset/polling recovery, Chat draft and
selection retention, Enter behavior, Cancel fencing, and absence of browser
storage writes. Raw rejected objects and `[object Object]` are never rendered.

The permission region has a semantic heading and polite live status; errors use
an alert, diff/command regions have readable labels, buttons have real disabled
state, and pending arrival does not steal Chat focus or create a focus trap.
At 1280x900 and 430x900 the real application browser had no document overflow,
card/composer overlap, or three-column regression. Page console warnings/errors
were empty, and the DOM contained neither the isolated Workspace/HOME path nor
`[object Object]`.

## Real effects and certification

Real `ThreadingHTTPServer`, `ConversationTurnService`, `AgentTurnRuntime`,
`PermissionManager`, `write_file`, broker, and a controlled provider-free model
proved that Allow creates the 36-byte file exactly once; Deny and Cancel create
no file. Refresh restored the live pending card, and process restart cleared it
before SSE automatically returned live. Deterministic integration tests also
cover timeout, same-Turn second operations, deny-only commands, multiple pending
Turns, polling-only discovery, replay/reset, and late decisions.

Certification results:

- Permission/Change Feed/SSE/frontend focused matrix: 292 passed.
- Gateway/Conversation/RunJournal/Session compatibility: 321 passed.
- TUI/Headless/Memory/Skill/MCP/Pricing compatibility: 315 passed.
- production baseline suite: 126 passed.
- installed-wheel suite: 9 passed.
- final full suites: 2,437 passed twice, 2 skipped, and only the three existing
  unregistered benchmark-marker warnings.
- official semantic evaluator twice: 108 cases, 37 confirmed gaps, Phase 3B
  true, zero remote calls, and pass.
- scoped Ruff, explicit py_compile, full compileall, and every formal JavaScript
  `node --check` pass. pyright and mypy are not installed.
- runtime dependencies remain `[]`.

Active production certification is v24, documented in
`memory-retrieval-production-baseline-v24.md`. Batch 8B remains an optional
future local-management feature and is not part of this implementation.
