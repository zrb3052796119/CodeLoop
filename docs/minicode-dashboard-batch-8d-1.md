# MiniCode Dashboard Batch 8D-1

## Outcome and scope

Batch 8D-1 adds two backend-only, current-Workspace deletion authorities and
four loopback HTTP routes. It does not add Dashboard controls, browser state,
polling, EventSource connections, bulk deletion, User/Local Memory deletion,
active-work force deletion, undo, a database, or any Batch 9 behavior.

The formal frontend files are byte-identical to the pre-edit evidence. Runtime
dependencies remain empty.

## Original gap and RED

The Session helper removed only Session persistence and had no preview,
revision, Workspace authority, linked Turn/Run cleanup, partial recovery, or
HTTP contract. `MemoryManager.delete_entry()` removed one entry but retained
its approval audit records and `related_to` backlinks. The first Conversation
test failed because `minicode.conversation_deletion` did not exist; production
implementation followed that RED.

## Public authorities

`ConversationDeletionAuthority(workspace, data_dir=...).snapshot(session_id)`
returns a content-free preview. `delete(session_id, deletion_revision)` deletes
only the Workspace-owned Session and linked terminal Turns/Runs.

`ProjectMemoryDeletionAuthority(workspace, data_dir=...).snapshot(memory_id)`
returns a content-free Project-scope preview. `delete(memory_id,
deletion_revision)` deletes exactly one Project entry, every approval audit
record for its ID, and all Project backlinks to that ID.

Both previews use `schemaVersion: 1`, `mode: read-write`, fixed kind/target,
status, `delrev_<64 lowercase hex>`, bounded counts, fixed-code blockers and
fixed-code diagnostics. Neither schema exposes Session messages, Memory
content/hash/reason/provenance, Run payloads, titles, prompts, paths, stat
values, lock owners, exceptions, or credentials.

## Call graph

```text
GET/POST route
  -> data_management_http (strict parse + safe error map)
    -> ConversationDeletionAuthority
       -> DeletionLedger
       -> Session deletion snapshot / Session store transaction
       -> ConversationTurnStore deletion snapshot / terminal delete
       -> RunJournal deletion snapshot / terminal delete
    -> ProjectMemoryDeletionAuthority
       -> bounded no-write Memory reader
       -> DeletionLedger
       -> MemoryManager coordinated Project writer
          -> MemoryFile index rebuild + atomic Project save
          -> approval audit atomic save
```

The HTTP module never constructs or deletes filesystem paths and never decides
ownership or computes a revision.

## deletionRevision

Fresh revisions are deterministic SHA-256 values over canonical safe state.
Conversation state includes Session base/index/delta presence and generation,
Turn ID/status/update/run reference, Run ID/status/update/sequence, diagnostics,
and absence of a fence. Project Memory state includes opaque-ID-bound target,
audit and backlink fingerprints plus strict public enums; fingerprints are
only inputs to the outer revision and are never returned separately.

POST accepts exactly the revision returned by GET. A change before the
linearization point produces `deletion_revision_stale`. Once a content-free
fence exists, its original revision is the stable retry token for that partial
operation. A short completed receipt lets a duplicate/lost-response retry
return `already_absent`; its ten-minute TTL prevents a permanent tombstone.

## Conversation ordering and locking

The deterministic order is:

1. under the deletion ledger process RLock and cross-process flock, rescan and
   atomically create the Session-scoped fence;
2. release the ledger lock and rescan linked records;
3. delete terminal Turns;
4. delete terminal Runs;
5. delete Session deltas, base and shared index last under the Session process
   RLock and Session-store flock;
6. verify all representations are absent;
7. under the ledger lock, atomically write a bounded receipt and clear fence.

The writers capable of expanding the target set acquire ledger RLock/flock
before Session save, Turn `attach_session`, or linked Run `create_run`. The
fence is therefore the Conversation linearization point. Active, committing,
cancelling, queued/running, writer-owned, corrupt, unsafe or scan-limited
records block before deletion. No claim is made that three stores form one
filesystem transaction.

## Project Memory ordering and locking

GET uses the existing bounded no-write approval reader and creates no lock,
directory, marker, index, backup, migration or mtime change. POST creates the
opaque fence, then enters the existing Memory RLock-to-flock Project writer and
re-reads the authoritative revision. In memory it removes matching audit
records, strips all backlinks, deletes the entry and rebuilds indexes. It
atomically saves Project Memory, then the cleaned audit, verifies from disk,
and completes the receipt while the Memory lock is still held. User, Local and
unrelated Project entries are untouched.

`MemoryManager(readonly_load=True)` is the narrow internal seam used here so
the destructive authority can validate before any legacy migration/recovery
write. The default constructor behavior is unchanged.

## Partial, restart and lost response

Every step is repeatable and missing data means that step is already complete.
An exception after the fence returns `partial` with fixed
`deletion_retry_required` and bounded remaining counts; it never restores
deleted content or reports false completion. A restarted Gateway observes the
fence and remaining representations and accepts the stable retry revision. A
crash after the data commit but before receipt leaves a zero-remaining partial
fence; retry completes it. A completed response lost by the client is answered
from the finite receipt as `already_absent`. An unknown/cross-Workspace/forged
ID without a matching receipt remains 404.

## HTTP contract

```text
GET  /api/v1/sessions/{session_id}/deletion
POST /api/v1/sessions/{session_id}/deletion
GET  /api/v1/memory/project/{memory_id}/deletion
POST /api/v1/memory/project/{memory_id}/deletion
```

GET rejects query parameters, is no-store UTF-8 JSON and is read-only. POST is
loopback-only, rejects query parameters and method override, enforces same
loopback origin when Origin is present, accepts only `application/json`, limits
the body to 1 KiB, rejects duplicate JSON keys, and permits exactly one string
field named `deletionRevision`. There is no CORS and no automatic retry.

Safe mappings are 400 for invalid request/ID/revision, 404 for unknown target,
409 for stale/busy/write conflict, 503 for busy/unavailable stores, and 500 for
fixed `deletion_failed`. All responses use `Cache-Control: no-store`.

## Change Feed and existing read models

Session base/delta/index, Turn records and Run directories were already
observed. The Memory collector now also observes the content-free stat of
`approval_audit.json`, closing the only invalidation gap. No EventSource schema
or connection changed. Existing Sessions, Runs and Memory read models naturally
converge because deletion mutates their existing authoritative stores; no
read-model side effect was added.

## Production lineage and immutable assets

v31 is `memory-retrieval-production-v31`, parent v30, with reason code
`dashboard_data_deletion_authority`. It changes exactly:

- `minicode/conversation_turn_store.py`
- `minicode/gateway.py`
- `minicode/memory.py`
- `minicode/run_journal.py`
- `minicode/session.py`
- `minicode/web/change_feed.py`
- `minicode/web/http.py`

It adds exactly:

- `minicode/conversation_deletion.py`
- `minicode/deletion_store.py`
- `minicode/project_memory_deletion.py`
- `minicode/web/data_management_http.py`

There are no removed files. v1-v30 manifests and pins remain byte-identical.
The final v31 manifest SHA-256 is
`d0ea9a10ccd45d6f8e7807f92acfc38afce801f22e8be0967897653aed82fbae`.

The accepted semantic gold remains SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size `3033592`, mtime_ns `1784135857000000000`. The official evaluator passes
108 cases, 37 confirmed gaps, Phase 3B gate true and zero remote calls.

## Verification and handoff

Final focused authority/HTTP/Change Feed/baseline/packaging certification passed
555 tests. Two post-evaluator, post-fix full suites passed `2845 passed, 2
skipped, 3 warnings` in 187.31s and 187.91s; the warnings are the three existing
unregistered benchmark marks. Ruff, py_compile, compileall and every production
JavaScript `node --check` passed. pyright and mypy were not installed.

The final wheel SHA-256 is
`d52d98d3c6eb124eb24661bf85b7bb3c91271970e4cbd9f9e33d2af4c71b6726`.
An isolated installation imported from `/tmp`, served all four routes through a
real loopback Gateway, deleted one real Session/Turn/Run graph and one real
Project Memory, and showed the old Sessions/Runs/Memory APIs converged. Both
health routes and a stubbed `/run` remained compatible. Temporary processes,
ports, fixtures, installed tree, wheel and evaluator reports were removed after
certification.

Deterministic Event/Barrier tests cover fenced cross-process Session save,
two-process same-target Project deletion, process exit after partial Turn
cleanup and a completed POST whose response is deliberately dropped. The
two-deleter RED found one final receipt-handoff race; the waiting Project
authority now observes the winner's finite receipt and returns
`already_absent`.

Batch 8D-2 may rely on the four routes, strict schemas, fresh revision rule,
the `completed/partial/already_absent` POST status union, fixed error codes and
existing sessions/runs/turns/memory SSE invalidation. It must not persist a
revision, auto-retry POST, or infer deletion success locally.
