# MiniCode Dashboard Batch 8C-2

## Result and scope

The Dashboard now consumes the existing persistent Memory approval authority at
`#memory/approvals`. The production change is frontend-only: `app.js` and
`styles.css`. Batch 8C-1/8C-1.1 remain the sole persistence, safety, revision,
locking, audit, and HTTP authorities.

No Memory edit/delete, automatic approval, permission-policy merge, new REST
business API, EventSource, polling loop, WebSocket, database, daemon, MCP
control, remote administration, Agent Loop, MemoryPipeline, Retrieval,
Injection, Session, Chat, Tool Permission, File Review, RunJournal, TUI, or
Gateway `/run` behavior is introduced or changed.

## Frontend call graph

```text
#memory/approvals
  -> loadMemoryApprovals()
  -> GET /api/v1/memory/approvals/pending
  -> exact bounded validators
  -> independent volatile memoryApprovalStore
  -> renderMemoryApprovals()

Approve / Reject
  -> decideMemoryApproval(memoryId, reviewRevision, decision)
  -> one POST /api/v1/memory/approvals/{id}/decision
  -> exact decision-result validator
  -> GET pending authority reconciliation
  -> Approve only: existing Memory REST + Dashboard snapshot refresh

existing EventSource('/api/v1/events')
  -> existing resources.memory
  -> existing Memory REST refresh/invalidation
  -> coalesced pending-approval authority refresh/invalidation
```

## Store and safety contract

The store owns only `phase`, `items`, `revision`, `diagnostics`, `error`,
`requestId`, `actionGeneration`, `actingMemoryId`, `selectedMemoryId`, and
`lastUpdatedAt`. Its phases are idle/loading/live/empty/partial/error. GET and
POST completions have independent generations; the action identity is the exact
`memoryId + reviewRevision`. No approval content, revision, or result is stored
outside volatile page memory.

The pending validator rejects the complete payload if any exact key, type,
timestamp, ID, revision, enum, scope relation, risk relation, choice relation,
review flag, fixed hidden preview, or byte budget is invalid. Only complete,
unredacted, untruncated safe/suspicious reviews can be approved. Every other
valid bounded projection is Reject-only. All visible content is escaped.

## Decisions and reconciliation

Buttons are disabled while a read or action is in flight; duplicate clicks
cannot create a second POST. No response causes a local optimistic decision.
Success, including `decisionAccepted=false`, is followed by a fresh authority
GET. Stale/not-found/already-decided/not-reviewable/write-conflict and malformed
success responses also reconcile by GET without resending the decision. Busy
and connection loss preserve the safe review, show fixed messages, and require
manual authority refresh. Raw errors and server messages are never rendered.

## UI

Memory has six tabs: Overview, Scopes, Approvals, Retrieval, Injection, and
Lifecycle. The approval page shows `read-write · persistent approval`, loaded
pending count, manual refresh, the Retrieval/Injection activation boundary, a
compact master/detail queue, bounded scrolling preview, `批准并启用`/`拒绝`,
and Reject-only unsafe projections. It stacks to one column below 760px and
uses the existing global keyboard focus treatment and ARIA state conventions.

The Scopes handoff copy is:

`此页不提供编辑或删除；持久记忆审批在“待审批”子页完成。`

## Batch boundary

Batch 8C-2 consumes, but does not duplicate, the Batch 8C authority. v30 records
the exact frontend-only production delta. Final command, wheel, semantic-gold,
full-suite, and browser evidence is maintained in `implementation_notes.md` and
the task report.

