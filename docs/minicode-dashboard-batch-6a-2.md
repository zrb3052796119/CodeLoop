# MiniCode Dashboard Batch 6A.2

## Outcome and scope

Batch 6A.2 coordinates Session storage writes made by multiple local MiniCode
processes that share one `MINI_CODE_DIR`. The typical supported case is a TUI
process and a Gateway process on the same macOS/Linux machine and local
filesystem.

The Dashboard remains read-only. This batch adds no Session HTTP write route,
Dashboard Chat, polling, SSE/WebSocket, Run/MCP control, Agent Loop change,
Memory behavior, TUI interaction change, database, or runtime dependency.

## Deep transaction module

`minicode.session_store.session_store_transaction(data_dir)` is the only
cross-process coordination interface. It hides lock-target creation and
validation, bounded waiting, advisory-lock acquisition, and release. Session
callers retain their existing signatures and return values.

The lock path is calculated on every acquisition:

```text
<current MINI_CODE_DIR>/session-store.lock
```

It is not captured from the real user HOME at module import. Tests and isolated
installs that select another `MINI_CODE_DIR` therefore lock only that directory.

The module uses the standard-library POSIX `fcntl.flock()` interface with an
exclusive, nonblocking advisory lock. The file is opened read/write with create,
`O_CLOEXEC`, and `O_NOFOLLOW` when available. Creation mode is `0600`, existing
files are restricted to `0600`, and `fstat` plus `lstat` require the descriptor
and path to identify the same regular file. Symlinks, directories, FIFOs,
permission failures, and unsafe target replacements fail with the fixed
low-information `SessionStoreLockError`.

MiniCode never writes a prompt, message, credential, workspace path, PID,
ownership claim, payload, or other content to the lock file. A MiniCode-created
lock file is empty. Release performs unlock and close only. The lock file is
never deleted: its continued existence after every transaction and process exit
is expected and prevents separate processes from locking different inodes.

## Acquisition order and transaction coverage

Every Session writer uses exactly this order:

```text
process-local threading.RLock
  -> cross-process session_store_transaction / flock
    -> authoritative state check and complete storage transaction
```

The public `save_session()`, `delete_session()`, and
`cleanup_old_sessions()` functions each acquire the cross-process lock once and
call already-locked internal helpers. No internal helper reacquires flock and no
reverse ordering exists.

The exclusive lock covers more than each `os.replace()`. It covers:

- the acquired-lock base/delta revision scan;
- full Session serialization and atomic base replacement;
- persistence generation advancement;
- legal delta-tail scan, next-sequence selection, and atomic delta replacement;
- best-effort post-full-save delta cleanup and safe retained sequence;
- every `sessions_index.json` load, mutation, and atomic replacement;
- Session file/delta/index deletion;
- old-Session selection and the complete cleanup loop.

Thus two different-Session saves cannot lose a shared-index entry, and save and
delete cannot commit from independently stale index snapshots.

## Timeout and failure semantics

Lock waiting uses a monotonic deadline, repeated `LOCK_NB` attempts, and short
bounded waits. The default timeout is five seconds. Exhaustion raises
`SessionStoreBusyError("session store is busy")`; wall-clock time, lock-file
mtime, PID ownership, heartbeat, lease, and stale-file deletion are not used.
Tests can inject monotonic and wait functions, and the real-process timeout test
uses a short configured timeout rather than a long sleep.

`KeyboardInterrupt` and `SystemExit` are not converted and preserve object
identity. The operating system releases the advisory lock if a holder exits
without running Python cleanup. `AutosaveManager.save_now()` retains its existing
failure isolation: a busy/conflict/lock error returns `False`, keeps dirty state,
and allows a later retry without replacing the Agent result or exception.

## Same-Session stale writer policy

Flock serializes writers but cannot make a stale in-memory Session current.
Each `SessionData` therefore carries an internal storage revision made of:

- whether an authoritative base existed when it was created/loaded/saved;
- the bounded persistence generation;
- the next sequence above every legally named delta file.

After acquiring flock, `save_session()` rereads the authoritative base
generation and current legal delta tail. The disk revision must exactly match
the caller revision. A mismatch raises
`SessionWriteConflictError("session write conflict")` before metadata mutation,
base/delta/index writes, or cleanup.

The policy is safe rejection, not automatic merge. A stale writer cannot reuse a
delta filename, overwrite another turn, roll metadata/history/permissions/
Skills/MCP state back, or force a full save from its old snapshot. To continue,
the process must reload the latest Session, apply its new turn to that state, and
save the new revision. Legacy generation-zero bases remain supported because
base presence is tracked separately from generation number.

## Reader consistency boundary

`load_session()`, `list_sessions()`, Dashboard Sessions, and Dashboard Session
Detail remain lock-free. Existing same-directory temporary files plus
`os.replace()` ensure a reader sees the previous complete JSON file or the next
complete JSON file, never half a JSON file.

The collection of base, delta, and index files is not a multi-file atomic
snapshot transaction. During a writer commit, an unlocked reader may briefly see
adjacent commit stages—for example, a new base before its updated index. It must
not see a torn target file. Dashboard remains bounded, redacted, workspace
isolated, role filtered, and schema-v1 read-only.

## Supported environment

This is a cooperative local POSIX lock for macOS/Linux. Every Session writer must
use the same transaction module and `MINI_CODE_DIR`. Windows, NFS and other
network filesystems, multiple machines, non-cooperating writers, distributed
locks, high availability, leader election, heartbeat, PID ownership, and leases
are not supported.

## Original RED evidence

Two true spawned-process tests failed before production changes:

1. two different new Session writers were held after both loaded the same empty
   index; both bases survived, but the final index retained only one ID;
2. two processes loaded the same generation-1/tail-0 Session; the first wrote its
   turn, while the second returned success, reused `delta_0000.json`, and
   overwrote the first turn.

The original result was `2 failed in 0.23s`. With the transaction and revision
precondition, those same tests pass: different Sessions retain both index entries
and the second same-Session writer receives `SessionWriteConflictError`.

## Changed files

- `minicode/session_store.py` — deep POSIX advisory-lock transaction module and
  low-information storage exceptions;
- `minicode/session.py` — fixed writer ordering, acquired-lock revision check,
  internal base-presence revision state, and complete transaction composition;
- `tests/test_session_cross_process.py` — real-process concurrency, conflict,
  timeout, crash, safety, visibility, cleanup, and retry coverage;
- `tests/test_packaging.py` — wheel content and installed two-process Session plus
  Gateway/API/static smoke;
- `docs/minicode-dashboard-batch-6a-2.md`, `implementation_notes.md`,
  `task_plan.md`, and `notes.md` — isolated design and certification record.

No production HTML, CSS, or JavaScript file changed. `pyproject.toml` still has
`dependencies = []`.

## Certification

- Pre-edit approved full suite: `2036 passed, 2 skipped, 3 warnings in 84.15s`.
  The restricted attempt's 48 failures and 16 errors were all denied localhost
  binds, not product failures.
- Cross-process/lock suite: `16 passed`; complete Session/TUI/Dashboard/HTTP
  focus: `199 passed in 29.47s`.
- Modified-file Ruff and `py_compile`: passed. `compileall -q minicode scripts
  tests`: passed. Both production JavaScript `node --check` commands passed.
- Full packaging suite: `9 passed`. The wheel contains
  `minicode/session_store.py`; an isolated install outside source cwd ran two
  synchronized Python Session writers, retained both index entries, then passed
  installed Gateway `/run`, `/health`, Sessions API, and static-resource smoke.
- Default production verifier: active v13, `candidateMatches=true`, current
  protected source 23/23, and every v1-v13 manifest-integrity flag true. No v14
  or manifest modification was needed.
- Semantic evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  `remote_calls=0`, and `evaluation_passed=true`. Accepted gold remained
  byte-identical before/after: SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size `3033592`, mtime ns `1784135857000000000`.
- Evaluator-after final full suite: `2052 passed, 2 skipped, 3 warnings in
  85.01s`. The warnings are the existing unregistered benchmark markers.
- Production HTML/CSS/JS hashes and sizes are byte-identical to Batch 6A.1.
  Because this batch has no UI changes, browser visual acceptance was not
  repeated; no viewport, tab, or console result is claimed.
- Runtime dependencies remain `[]`. Test subprocesses, isolated installs,
  temporary HOME/workspaces, synchronization files, and Gateway listeners were
  bounded and cleaned. No workspace lock file or temporary atomic-write file
  remains.

## Explicitly deferred

Batch 6B and Dashboard Chat remain unimplemented. There is no Dashboard Session
write interface, automatic stale-turn merge, push/polling transport, Run/MCP
control, database, or distributed coordination.
