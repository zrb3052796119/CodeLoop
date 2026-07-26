# MiniCode Dashboard Batch 8C-1.1

## Result and scope

Batch 8C-1.1 hardens the existing Memory approval read path.  The public
`MemoryApprovalAuthority` and HTTP schemas are unchanged, but
`snapshot()`, `revision()`, and the real pending-approval GET no longer
construct `MemoryManager` and therefore never create the MiniCode root, the
cooperative lock, migration output, recovery backups, audit records, or
temporary files.

Only `minicode/memory_approval.py` changes in production.  The formal
Dashboard bundle, Permission/File Review, Retrieval, Injection, Session,
Agent behavior, SSE, and the Batch 8C-2 UI are unchanged.

## Root cause and deep seam

The original read graph was:

```text
snapshot / revision / pending GET
  -> MemoryApprovalAuthority._manager()
  -> MemoryManager.__init__()
  -> MemoryStoreCoordinator.transaction()
  -> create MINI_CODE_DIR + memory-store.lock
  -> load / migrate / recover / audit / atomic save as needed
```

That made an apparently read-only request a writer even for an empty store.
It could also persist legacy interpretation, write a corrupt-file backup,
recover from Markdown, clean invalid records, and rewrite audit/state files.

The authority now owns a private, bounded read seam:

```text
snapshot / revision / pending GET
  -> _read_pending_entries()
  -> fixed user/project/local roots
  -> dir-fd + no-follow bounded regular-file reads
  -> strict JSON or fallback Markdown parse
  -> typed in-memory legacy/safety/approval interpretation
  -> duplicate/audit/hash validation
  -> existing bounded public projection + revision
```

The seam has no coordinator, manager, persistence, migration, recovery,
cleanup, audit, delete, rename, or temporary-file capability.  Source files
are capped at 2 MiB and 1,000 entries; the established public limits remain
20 items, 8 KiB preview, 12 KiB item, and 128 KiB snapshot.

## Filesystem and corruption contract

Missing roots and files mean an empty scope and do not create anything.
Existing valid current stores are read without recreating a removed lock and
without changing file bytes, size, or nanosecond mtime.  Historical records
missing `approval_policy`, approval status, or approval hash are interpreted
deterministically in memory so that their revision agrees with the decision
loader, but the compatibility state is never saved by GET.

Malformed JSON, invalid entries, duplicate IDs across scopes, approval-hash
mismatch, and malformed approval audit fail closed with the fixed
`memory_approval_unavailable` error.  They do not write `.bak`, recover,
migrate, clean, or audit.  Valid `MEMORY.md` fallback is projected without
generating structured state.

The configured MiniCode root, each scope root, and each authority file must be
the expected non-symlink type.  Reads open the directory and child with
`O_NOFOLLOW` where available, compare directory device/inode before use, use
`O_NONBLOCK`, reject non-regular files including FIFOs, and enforce the byte
cap while reading.  This closes symlink, replacement-race, device, directory,
and blocking-special-file paths without exposing content.

## Decision authority remains write-coordinated

`decide()` deliberately retains the existing authoritative graph:

```text
process RLock -> POSIX flock -> reload changed authority
  -> resolve ID/scope -> rebuild and compare review revision
  -> typed approve/reject -> approval audit -> atomic state save
```

The GET-produced `memoryreviewrev_*` is accepted by a subsequent POST for
legacy and fallback records.  A content or state change remains a fixed 409
`memory_review_stale`.  Same-decision retries remain idempotent and opposite
terminal decisions remain conflicts.  A multiprocess test holds the writer
flock while changing a pending item: the read snapshot neither waits for nor
acquires that lock, observes the complete old atomic file, and observes the
new revision after commit.  The writer still owns stale fencing.

## HTTP and package compatibility

The real loopback `GET /api/v1/memory/approvals/pending` remains schema version
1, read-only, no-store JSON with the same bounded fields and fixed error
vocabulary.  The strict POST body, Content-Type/Accept/Origin validation,
loopback-only write boundary, workspace isolation, ID rules, and response
schema are unchanged.  Gateway health, Chat, Permission, Session, Change Feed,
SSE, and existing decision behavior remain covered by the compatibility and
installed-wheel tests.

The wheel contains `minicode/memory_approval.py` and the packaged Dashboard
assets.  An isolated install with an isolated HOME can call empty
`snapshot()/revision()` without creating `.mini-code`; the package suite also
covers real empty/legacy GET, approve/reject POST, and the existing endpoints.
Runtime dependencies remain `[]`.

## Verification evidence

The pre-edit suite passed `2500 passed, 2 skipped, 3 warnings`.  Deterministic
REDs on v26 showed that an empty snapshot created `home/`, `.mini-code/`, and
`memory-store.lock`, and that reading a current store recreated a deliberately
removed lock.

Final focused results include 305 Memory policy/Retrieval/Injection/Pipeline/
curator/reflection tests, 66 approval authority/HTTP/cross-process tests, 365
Gateway/Chat/Permission/Session/SSE compatibility tests, 139 production
baseline tests, and 9 package/wheel tests.  The two final full suites passed
`2525 passed, 2 skipped, 3 warnings` in 171.02s and 169.03s.  The three warnings
are the existing unregistered benchmark markers.

Scoped Ruff, targeted `py_compile`, full `compileall`, and every formal
JavaScript `node --check` pass.  pyright and mypy are not installed.  The
official semantic evaluator passes 108 cases with 37 confirmed gaps, Phase 3B
true, and zero remote calls.  Accepted semantic gold remains byte/stat
identical.

No new browser visual run is claimed because this batch prohibits frontend
changes.  The formal index, app.js, styles.css, and cost formatter are
byte-identical to the recorded starting hashes.

## Batch 8C-2 handoff

Batch 8C-2 may consume the unchanged pending GET, decision POST,
`memoryapprovalrev_*`, `memoryreviewrev_*`, `reviewable`, `choices`, fixed
errors, and existing `resources.memory` invalidation.  It must not duplicate
the read parser, persistence authority, scope resolution, safety scan, or
decision transaction.

The earlier Tool approval UI that exposes only Reject belongs to the separate
Permission/File Review boundary.  It is not changed or resolved here.
