# MiniCode Dashboard Batch 6A

## Outcome

Batch 6A establishes one durable, read-only truth path:

```text
TUI user submission
  -> background run_agent_turn result
  -> consume_finished_tty_turn() (at most once)
  -> commit_finished_tty_turn()
  -> SessionData snapshot + AutosaveManager.save_now()
  -> atomic base/delta/index files
  -> existing DashboardReadModel Sessions projection
  -> shared Sessions page + Conversation Dock store
```

The Dashboard still has no Session write route and cannot send an Agent message.
The right input and submit control are disabled and explicitly say
`Dashboard 发送功能尚未接入`.

## Original defects and TDD evidence

Before this batch, the background completion path only copied returned messages
into TUI arguments and reset `done`. `SessionData` was synchronized mainly by the
exit finalizer, so a successful turn was not durable until exit. The first tracer
failed at import because no finished-turn consumption/commit seam existed.

Base Session, delta, and index writers used direct `Path.write_text()`. Failure
injection then proved that the intended incremental branch was also unreachable:
the initial `_delta_save_count == 0` condition forced every successful save to be
full and reset the counter to zero. Atomic base, delta, index, retry, corruption,
and reader-concurrency tests were added before the corresponding production
changes.

The frontend RED contract found the confirmed mock Dock and selection defects:
mock `DATA.sessions`, `openMockSession()`, simulated replies, selection clearing
on Refresh, no initial latest selection, and no workspace-scoped reload recovery.

## Finished-turn commit contract

`consume_finished_tty_turn(args, state)` is the only main-loop consumption seam.
It checks and rechecks `agent_result.done` under the existing lock, copies a valid
final message list, clears `done` before persistence, and invokes the deep commit
module. A repeated observation therefore returns without another save or delta.

`commit_finished_tty_turn()` copies one coherent completed-turn view into the
owned Session:

- messages;
- transcript entries;
- history;
- permission summary;
- Skill and MCP summaries;
- metadata, updated time, previews, and message count through `save_session()`.

On normal completion, the returned `next_messages` are authoritative. On an
ordinary Agent failure, the existing user-only message list is committed and no
assistant message is invented. Existing interrupt/SystemExit propagation remains
unchanged; if the main loop can consume a `done` marker, it follows the same
user-only rule. A persistence failure does not replace or mutate the Agent result.
It keeps autosave dirty and exposes only `Session save deferred; will retry.`
The exit full save remains a final retry, not the normal durability mechanism.

## Persistence integrity and limit

Base Session JSON, numbered delta JSON, and `sessions_index.json` now use one
shared atomic writer:

1. create a unique temporary file in the destination directory;
2. write UTF-8 JSON;
3. flush and `fsync()` the temporary file;
4. atomically replace the target with `os.replace()`;
5. best-effort remove the temporary file.

Readers see the previous complete JSON or the next complete JSON. Replace failure
preserves the previous target and dirty state. Delta state now carries the same
history, permissions, Skill/MCP, updated time, and metadata snapshot as the
message offsets. Loading validates a delta before mutation, safely handles
overlap, skips corrupt or gapped deltas, and never reuses a corrupt sequence
number. Resume plus a later save does not duplicate earlier messages.

Batch 6A.1 deepens this contract with a bounded persistence generation. A full
base replace advances generation only after `os.replace()` succeeds. A delta is
applied only when it explicitly belongs to the base generation; legacy base and
delta files without the field are generation zero. Consequently, a retained old
delta cannot roll history, metadata, permissions, Skills, MCP state, messages, or
transcripts back after a successful full save. Cleanup is best-effort and retains
the next sequence above every remaining legally named delta.

Session files and the shared index currently guarantee in-process concurrency
safety only; independent writer processes sharing one HOME have no conflict
coordination. Dashboard/Gateway read-only processes are not write conflicts. One
process-local `RLock` covers each Session writer and the complete shared-index
read-modify-replace operation used by save, delete, and old-Session cleanup.
There is no cross-process file lock, leader election, compare-and-swap, or other
multi-process coordination.

## Existing Session API

No backend API or schema change was required. The implementation reuses:

- `GET /api/v1/sessions`;
- `GET /api/v1/sessions/{session_id}`;
- `schemaVersion: 1` and `mode: read-only`.

Existing workspace isolation, strict IDs, symlink/path bounds, file/delta/message
budgets, cursor binding, role filtering, redaction, HTML escaping, and local
corruption diagnostics remain intact. Conversation text still comes only from
the safe user/assistant projection of Session messages, never from TUI transcript.

## Sessions page and real Dock

The Sessions page and Dock now share `sessionsStore` and `sessionDetailStore`.
Initial load restores a minimal `sessionStorage` record containing only workspace
ID and opaque Session ID; otherwise it selects the current workspace's latest
Session. No message, path, token, credential, or API payload is stored. A foreign
workspace or missing ID falls back to latest, while an empty list clears the
selection.

Refresh preserves an existing selection and falls back only when that Session is
gone. Request IDs plus a selection revision prevent late list/detail responses
from overwriting a newer choice. Message pagination keeps server budgets and
deduplicates by safe message index.

The Waku three-column layout is unchanged. The Dock now shows real source state,
selected title/update time/visible count, safe user/assistant messages,
truncation, Load More, history, Refresh, Retry, and empty/error states. It contains
no mock Session dataset or simulated assistant behavior. Data reads occur only
on initial load, route/selection needs, and explicit Refresh/Retry; the remaining
timer only updates local time text.

Runs continue to use their existing safe `sessionId`. A linked TUI Run exposes
`查看 Session`, selects it, and navigates to Sessions. A Headless/Gateway Run with
`sessionId=null` says `未关联 Session` and creates no association.

## Certification

- Pre-change full suite: `1985 passed, 2 skipped, 3 warnings` after approved
  localhost binding; the restricted-sandbox failures were only `PermissionError`
  at `socket.bind()`.
- Focused Session/TUI/Dashboard/HTTP matrix: `224 passed`.
- Dashboard HTTP: `61 passed`; packaging: `9 passed`.
- Modified Python files: Ruff clean and `py_compile` clean.
- Repository-wide Ruff: 85 existing diagnostics in unrelated legacy files; none
  was concealed or expanded into this batch.
- `compileall -q minicode scripts tests`: passed.
- Both production JavaScript `node --check` commands: passed.
- Wheel isolation: passed outside source cwd with isolated HOME/Workspace,
  `PYTHONNOUSERSITE=1`, two real commit-seam turns, cross-process reload, installed
  Gateway Sessions API, and packaged static assets.
- Final full suites: `1996 passed, 2 skipped, 3 warnings` twice, in 82.90s and
  82.99s.
- Runtime dependencies remain `[]`.

The active production baseline remains `memory-retrieval-production-v13`:
23/23 protected files match and all v1-v13 manifest integrity flags are true.
Batch 6A did not modify protected `tui/input_handler.py` or any Memory Retrieval
execution boundary, so no v14 was created.

The official semantic evaluator passed 108 cases with 37 confirmed gaps,
`remote_calls=0`, and `evaluation_passed=true`. Accepted gold was unchanged
before/after: SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size `3033592`, mtime ns `1784135857000000000`.

## Browser acceptance

An isolated 1280×900 fixture created real Sessions through the public TUI commit
seam. Acceptance covered initial latest selection, two completed turns, shared
selection in both directions, reload recovery, Refresh preservation, missing-ID
fallback, a deliberately late stale response, first-detail failure plus Retry,
50+10 message pagination without duplicates, linked and unlinked Runs, eight main
routes, and five Memory subroutes.

The final measured columns were 208 / 682 / 380 pixels with no overlap and no
horizontal overflow. Console warning/error count was zero. Seven API responses
and the DOM contained no fixture secret, absolute injected path, hidden system
message, or `[object Object]`. The verified JPEG is
`artifacts/minicode-dashboard-batch-6a-sessions.jpg` (1280×900).

The temporary Gateway, tab, viewport override, isolated HOME/workspace, controls,
and fixture script were removed after verification.

## Batch 6A.1 recovery certification

Batch 6A.1 closes the retained-delta and shared-index recovery defects without
changing finished-turn, Session API v1, Gateway `/run`, or Dashboard design.
The original cleanup fault-injection RED rolled A/B/C history back to A/B after
the new base had replaced successfully. The Barrier RED left two complete base
files but only one shared-index entry. Both now have explicit regression tests,
along with legacy generation zero, invalid generation/timestamp/identity/state,
partial/all cleanup failure, safe next sequence, restart, atomic cleanup error
precedence, and save/delete races.

Final Batch 6A.1 evidence:

- Session/TTY/Dashboard delta focus: `107 passed`;
- Dashboard/HTTP/read-model regression: `144 passed` before final stabilization,
  with the complete final HTTP set also covered by both full suites;
- installed wheel/package/Gateway matrix: `9 passed`;
- two final full suites: `2036 passed, 2 skipped, 3 existing warnings` in
  83.00s and 82.97s;
- Ruff, `py_compile`, `compileall`, and both production JavaScript syntax checks
  passed;
- active v13 remains candidate-identical with 23/23 protected files and all
  v1-v13 integrity pins true; no v14 was created;
- official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true, zero remote
  calls; accepted gold SHA/size/mtime_ns remain
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b` /
  `3033592` / `1784135857000000000`;
- final 1280×900 browser regression rendered all eight main routes and five
  Memory routes, projected a real current-generation incremental delta in both
  Sessions and Dock, measured unchanged 208/682/380 columns, and found no
  overlap, horizontal overflow, console warning/error, absolute fixture path,
  or object-coercion leak.

Runtime dependencies remain `[]`. All isolated HOME/workspace directories,
Gateway listeners, browser tabs, and viewport overrides were removed.

## Explicitly deferred

Batch 6A adds no Dashboard chat, Session write API, Session management API,
SSE/WebSocket/polling, live token streaming, Run controls, MCP controls,
multi-process Session coordination, Batch 6B, or Batch 7 behavior.
