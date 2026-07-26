# MiniCode Dashboard Batch 8A-1 Permission Approval Authority

## Outcome

The loopback Gateway now owns one process-local, Workspace-scoped permission
approval broker. A real Dashboard Chat Tool can block inside the existing
`PermissionManager`, appear through a strict pending endpoint, and resume only
after a matching one-operation decision. This batch adds no approval UI,
persistent permission, remote approval, polling, SSE mapping, or runtime
dependency.

## Final call graph

```text
POST Dashboard Chat
  -> ConversationTurnService (existing Turn/Run/Session transaction)
    -> PermissionApprovalBroker.begin_turn(...)
      -> AgentTurnRuntime.execute(..., approval_session=...)
        -> AgentLoop Tool worker callback
          -> approval_session.tool_started(tool_name)
          -> real Tool
            -> PermissionManager.ensure_path_access / ensure_command / ensure_edit
              -> approval_session.prompt(structured request)
                -> pending broker record + permission.requested
                -> synchronous wait

GET /api/v1/permissions/pending
  -> broker.snapshot() safe bounded projection

POST /api/v1/permissions/{permission_id}/decision
  -> broker.decide(permission_id, turn_id, allow_once|deny_once)
    -> terminal transition + permission.decided + precise waiter wake
      -> internal allow_operation|deny_operation
        -> final operation/cancellation checkpoint
          -> protected filesystem or subprocess side effect
```

The Agent Loop copies the callback `ContextVar` context into its nested Tool
executor. Concurrent and same-name Tools therefore retain independent
operation identities; there is no global `current_tool`.

## Core authority

`minicode.permission_approval.PermissionApprovalBroker` exposes
`begin_turn()`, `snapshot()`, `decide()`, `revision()`, `cancel_turn()`, and
`close()`. Each returned `PermissionApprovalSession` exposes `prompt()`,
`tool_started()`, `tool_finished()`, operation checkpointing, and `close()`.
The core has no Web dependency.

Opaque identities use `permission_<32 lowercase hex>`,
`permissiontool_<32 lowercase hex>`, and `permissionrev_<32 lowercase hex>`.
Each pending record binds its broker, Turn, optional Run, real or `unknown`
Tool, Tool operation, permission kind, creation time, and expiry. Decisions
must match both permission and Turn IDs.

## State and linearization

The state machine permits only `pending` to one of `allowed`, `denied`,
`expired`, `cancelled`, or `closed`; terminal records are immutable. The first
valid decision returns `decisionAccepted=true`. An identical retry returns the
same terminal result with `decisionAccepted=false`; an opposite retry returns a
safe conflict. Late allow after timeout, cancel, or close cannot wake a Tool as
allowed.

The waiter uses a monotonic deadline, an event, and bounded cancellation
polling. It does not busy-spin or allocate a permanent thread. Default timeout
is 300 seconds and default pending capacity is 16. Terminal tombstones are
bounded to 256 entries and 600 seconds, and immediately discard review text,
command/diff content, and live session references. `close()` and Turn cancel
terminalize and wake every applicable pending request.

After allow, the protected Tool performs one final cancellation and operation
checkpoint immediately before filesystem mutation or subprocess creation. A
cancel visible at that boundary fails closed. Once the side effect has crossed
that boundary, existing cooperative cancellation correctly makes no rollback
promise.

## Operation-only PermissionManager decision

The public HTTP choices remain `allow_once` and `deny_once`, but the broker maps
them to internal `allow_operation` and `deny_operation`. Those decisions return
only from the currently blocked `ensure_*()` call. They never update
PermissionManager Turn/session sets, `permissions.json`, path prefixes, command
patterns, edit patterns, Session summaries, or future Turns. Existing TUI
choices and their historical cache/persistence semantics are unchanged;
Headless and non-loopback Gateway prompt-unavailable behavior remains
fail-closed.

## Review projection

PermissionManager supplies versioned structured review fields while retaining
its legacy TUI fields. The shared broker validator rejects bool-as-version,
wrong types, extra or incomplete review fields, invalid paths, and unsafe
objects.

- Edit projection contains only a Workspace-relative target and bounded unified
  diff preview.
- Command projection contains a bounded preview, Workspace-relative cwd, and a
  safe reason.
- Path projection contains intent and an outside-Workspace indicator, never an
  absolute local path.

Budgets are 32 KiB per diff, 4 KiB per command, 40 KiB per item, 128 KiB per
snapshot, and 16 pending items. Truncation, redaction, external paths,
incomplete contracts, or uncertain projection set `reviewable=false` and
remove `allow_once`. Sensitive assignments/tokens are replaced as a whole;
HOME, Workspace absolute paths, environment/configuration, transcripts,
outputs, exceptions, and credentials are not returned.

## HTTP contract

`GET /api/v1/permissions/pending` is query-free and returns a strict no-store,
read-only envelope with stable ordering, broker revision, and only the approved
pending item fields. `POST /api/v1/permissions/{permission_id}/decision`
requires strict `application/json`, an exact `{turnId, decision}` object, no
duplicate or extra keys, a body no larger than 1 KiB, no query, valid opaque
IDs, and `allow_once` or `deny_once`. Its response never includes review text.

If `Origin` is present it must exactly match the loopback Host and port. No CORS
allow-origin header is emitted. Broker composition is enabled only for
`127.0.0.1`, `::1`, or localhost resolving exclusively to loopback. Remote
binds retain prompt-unavailable Chat and return fixed 503 unavailable envelopes.
Unknown permission API paths retain structured 404 behavior. Invalid,
not-found, Turn-mismatch, conflict, expired, cancelled, unavailable, and
not-reviewable cases use fixed safe error envelopes.

## Safe Run events

The shared `minicode.permission_event_contract` is used by the broker,
RunJournal, and Run Detail ReadModel. `permission.requested` may contain only
version, opaque permission ID, kind, safe Tool name, opaque Tool operation ID,
and reviewable. `permission.decided` may contain only version, opaque ID, and
the fixed low-cardinality decision kind. Paths, diffs, commands, arguments,
reasons, prompt/review text, Tool content, feedback, credentials, and exception
text are rejected. Emission is best-effort and cannot alter an approval result.

## Certification

Real PermissionManager/Tool/Gateway tests prove that edit and command side
effects do not occur before allow, occur once after allow, and occur zero times
after deny, timeout, cancel, close, unsafe review, or capacity failure. They
also cover same-Turn same-target reapproval, HTTP-thread wakeup, cancel races,
commit boundaries, tombstone bounds/TTL, concurrent same-name Tool isolation,
Session commit, and exact content-free Run event order.

The formal HTML, CSS, and JavaScript hashes remain the v21 values. The isolated
wheel contains the authority, event contract, HTTP adapter, and unchanged
static assets; installed HTTP smoke covers approval plus all retained Gateway
routes. Runtime dependencies remain empty.

## Batch 8A-2 handoff

The stable future seams are broker `revision()`, strict pending GET, strict
decision POST, and the two safe Run event types. Batch 8A-2 may connect revision
invalidation and implement a pending store and Allow/Deny UI. It must continue
to treat these backend authorities as the source of truth. None of that UI or
transport work is included here.
