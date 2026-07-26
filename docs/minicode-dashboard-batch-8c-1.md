# MiniCode Dashboard Batch 8C-1

## Result and scope

Batch 8C-1 establishes a persistent Memory approval authority, a cooperative
local write transaction, and strict loopback HTTP contracts. It deliberately
does not implement the Dashboard approval UI. It does not reuse
`PermissionApprovalBroker`, create a second Memory store or RunJournal event,
or change Retrieval ranking, Prompt formatting, SSE schema, or the formal
frontend bundle.

## Approval policy

`MemoryApprovalPolicy` is the durable creation policy:

- `USER_EXPLICIT`: a safe `# ...` or `/memory add ...` remains approved;
  suspicious content is pending and unsafe content is rejected.
- `USER_REVIEW_REQUIRED`: safe and suspicious automatic content is pending;
  unsafe content is rejected.

`MemoryPipeline.write()`, legacy reflection persistence, curator insights and
automatic compression rewrites use the review-required policy. The policy is
an explicit enum argument and is never inferred from an untrusted `source`
string. Historical entries without the new serialized field default to
`USER_EXPLICIT`; their existing approval state is not migrated.

Pending is durable state in the existing user/project/local `memory.json`.
`MemoryEntry.is_active` requires approved, non-unsafe, active, unlocked and
non-archival state. Search, canonical Retrieval, context rendering, injection,
Working Memory and promotion therefore cannot consume a pending candidate.

## Authority interface

The stable backend seam for Batch 8C-2 is:

```python
MemoryApprovalAuthority(workspace)
MemoryApprovalAuthority.snapshot() -> dict[str, object]
MemoryApprovalAuthority.revision() -> str
MemoryApprovalAuthority.decide(
    *,
    memory_id: str,
    decision: Literal["approve", "reject"],
    review_revision: str,
) -> MemoryApprovalDecision
```

`MemoryManager.decide_pending_entry()` is the typed core mutation seam shared
by HTTP and the compatible TUI approve/reject commands. Existing human-readable
manager methods and TUI messages remain available; HTTP never parses those
messages as state.

## Pending HTTP schema

`GET /api/v1/memory/approvals/pending` returns schema version 1:

```json
{
  "schemaVersion": 1,
  "generatedAt": "UTC timestamp",
  "mode": "read-only",
  "source": {
    "status": "live",
    "updatedAt": "UTC timestamp",
    "message": null
  },
  "revision": "memoryapprovalrev_<64 lowercase hex>",
  "items": [],
  "diagnostics": []
}
```

At most 20 items are returned. Preview bytes are limited to 8 KiB, one item to
12 KiB and the complete snapshot to 128 KiB. The snapshot revision covers all
pending items, including items beyond the visible page limit.

Each item contains only ID, authoritative scope, a fixed scope kind, bounded
category/tier/source, creation time, low-cardinality risk and safety status,
the bounded review, choices, and `memoryreviewrev_*`. Metadata, provenance,
task/trace/transcript/Prompt/Tool data, audit records, internal hashes,
credentials and local paths are absent.

Secret or absolute-path detection produces a fixed redacted placeholder.
Unsafe content produces a fixed unsafe placeholder. Any redacted, truncated,
incomplete, archival, locked, hash-mismatched or otherwise unreviewable item is
deny-only with `choices=["reject"]`. A final serialized scan repeats the
secret/path defense.

## Decision HTTP schema

`POST /api/v1/memory/approvals/{memory_id}/decision` accepts exactly:

```json
{
  "decision": "approve",
  "reviewRevision": "memoryreviewrev_<64 lowercase hex>"
}
```

`decision` is `approve` or `reject`. Success is schema version 1 and includes
`memoryId`, fixed terminal `status`, `decision`, `decisionAccepted`, and UTC
`updatedAt`. A same-decision retry returns the terminal fact with
`decisionAccepted=false`; an opposite terminal decision is a conflict.

The fixed error vocabulary is:

```text
invalid_request              400
invalid_memory_id            400
invalid_decision             400
invalid_review_revision      400
memory_approval_not_found    404
memory_review_stale          409
memory_already_decided       409
memory_not_reviewable        409
memory_write_conflict        409
memory_store_busy            423
memory_approval_failed       500
memory_approval_unavailable  503
```

Both routes are no-store UTF-8 JSON. The adapter rejects query parameters,
duplicate JSON keys, extra fields, invalid types, a body over 1 KiB, invalid or
duplicate transport headers, non-JSON Content-Type and unacceptable Accept.
The write route requires a loopback-bound server and same-origin validation
when Origin is present. It emits no CORS headers and accepts no workspace path.

## Review revision and stale fencing

`memoryreviewrev_*` is a SHA-256 of a canonical projection containing the
projection version, Memory ID, scope, approval status, lifecycle status, safety
status, internal approval-content hash and a fresh content hash. It contains no
wall clock and reveals none of those internal hashes directly.

Decision acquires the shared transaction, reloads authority, resolves the ID
through `MemoryManager`, checks it still belongs to user/project/local scope,
rebuilds the safe projection and compares the revision in constant time. A
content or status change makes both stale approve and stale reject return 409;
the changed candidate remains non-injectable.

Usage/retrieval/injection counters and timestamps are intentionally outside the
existing approval-content hash, so ordinary accounting does not stale a review.

## Scope and persistence

Project and Local roots are derived only from the Gateway workspace. User is
the current user's global scope and is labeled `user/global`. HTTP cannot
choose another workspace or scope. Scope roots and authority files are checked
for symlinks before use, and IDs are validated and resolved through the loaded
manager rather than converted into paths. Project/Local pending entries do not
cross workspaces; User pending entries do.

The approval audit remains the existing per-scope audit. Successful Dashboard
decisions use actor `dashboard_user` and fixed reasons `dashboard_approved` or
`dashboard_rejected`, recording previous/final approval and lifecycle, safety,
the internal content hash and timestamp. It records no HTTP headers, Origin,
IP, preview, Prompt, trace, transcript or credentials. Audit is saved before
the Memory state file, so a later state-write failure cannot leave an approved
Memory without a durable audit; HTTP reports failure rather than success.

## Cooperative local transaction

All durable Memory mutation paths use `MemoryStoreCoordinator`:

```text
process RLock → POSIX flock → reload changed authority → validate → mutate
→ atomic audit/state saves → unlock/close
```

The default timeout is five monotonic seconds. The persistent
`<MINI_CODE_DIR>/memory-store.lock` is a regular, zero-byte `0600` file opened
with `O_CLOEXEC` and `O_NOFOLLOW` where supported and checked against its opened
inode. It is never deleted and stores no PID, path or payload. Busy and stale
conditions return fixed typed errors; decisions are not automatically retried.

This is cooperative locking for MiniCode processes on a local macOS/Linux
filesystem. It does not claim Windows, NFS, multi-host or distributed safety.
A reader sees a complete old or new atomically replaced JSON file; adjacent
multi-file commits may be observed at different instants.

## Realtime and Batch 8C-2 handoff

The existing `resources.memory` Change Feed revision already observes
candidate creation, content changes, approve, reject and restore because those
operations rewrite authoritative Memory files. No `memoryApprovals` resource,
new EventSource, polling loop or SSE schema change was added.

Batch 8C-2 should consume only the authority GET/POST schemas above and reload
pending state when the existing Memory resource revision changes. It must not
reimplement persistence, safety scanning, scope resolution or decisions.

## Verification summary

The original REDs reproduced automatic safe reflection becoming approved,
missing authority/routes, stale-manager overwrite/approval and deterministic
spawned-process lost updates. Core, HTTP and spawned-process tests now cover
policy states, persistence, retrieval/injection exclusion, revision fencing,
deny-only projection, audit, scope isolation, symlink/path rejection,
idempotence/conflict, lock timeout and no-lost-update behavior.

The installed wheel includes all three new modules and serves/decides Memory
approval through a real loopback Gateway. Existing health, Chat, Permission,
Session, Change Feed and SSE paths remain in their compatibility matrices. The
formal HTML/CSS/JavaScript files were not modified.

Final evidence is `2500 passed, 2 skipped, 3 existing warnings` twice (167.48s
and 167.73s), with the official evaluator between them: 108 cases, 37 confirmed
gaps, Phase 3B gate true, zero remote calls and `evaluation_passed=true`.
Scoped Ruff, `py_compile`, complete `compileall`, and both formal JavaScript
syntax checks pass. pyright and mypy were unavailable. The accepted gold keeps
its exact SHA, size and nanosecond mtime.

Known boundaries are intentional: the lock is local POSIX coordination only;
readers may observe adjacent stages of a multi-file commit; the snapshot is
bounded to 20 visible items; and a curator cycle holds the cooperative lock
while it performs its mutation cycle, so another writer can receive the fixed
busy error after five seconds. No limitation allows pending content to become
active or bypass revision/safety checks.
