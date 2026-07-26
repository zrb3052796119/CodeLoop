# MiniCode Dashboard Batch 8D-2

## Outcome and boundary

Batch 8D-2 gives the formal Dashboard two explicit management actions on top
of the certified v31 authorities:

1. delete one complete saved Conversation;
2. delete one current-Workspace Project Memory entry.

The production delta is limited to `app.js` and `styles.css`. `index.html`,
every v31 backend authority/schema/store, Agent Loop, Retrieval/Reflection,
TUI, Change Feed schema and runtime dependency remain unchanged. No Batch 9
behavior is present.

## Original UI gap and RED

The v31 Gateway exposed four strict GET/POST deletion routes, but the formal
Dashboard had no deletion store, validator, entry point, confirmation,
revision handling, tombstone or cross-page reconciliation. The first two
production-script tests failed before implementation because the deletion
validators and UI contract did not exist.

The final formal test executes the real validator and transport slices from
`app.js`; it does not maintain a parallel implementation.

## State machines

`conversationDeletionStore` and `projectMemoryDeletionStore` are independent
volatile stores. Each owns phase, kind, target ID, validated preview/result,
fixed safe error, separate request/action generations, opener,
`outcomeUnconfirmed`, stale notice, local-busy state and collection convergence.
Neither revision nor action state enters an existing data store,
`localStorage`, or `sessionStorage`.

The normal path is:

```text
idle -> loading-preview -> review -> submitting -> reconciling -> completed
```

Fail-closed branches are `busy`, `partial`, `stale`, `unconfirmed` and `error`.
Closing or switching targets invalidates both generations. A submitting dialog
cannot be dismissed as though the server operation were cancelled.

## Strict validators

Conversation preview/result and Project Memory preview/result require exact
top-level and nested keys, integer `schemaVersion === 1`, `mode=read-write`,
the requested kind and target, a 64-lowercase-hex `delrev_`, bounded ISO time,
bounded arrays, fixed diagnostic/blocker codes, and finite non-negative safe
integer counts no larger than one million. Boolean counts, extra/missing keys,
unknown enums, target/revision mismatch and over-budget values reject the whole
payload.

Conversation counts are exactly Session/Turn/Run. Project counts are exactly
entry/approval-audit/backlink, and Project target metadata uses the backend's
closed category, tier, lifecycle and approval enums with `scope=project`.
Invalid payload fields are never rendered. Raw server messages are ignored in
favor of fixed local code mappings.

## Conversation interaction and convergence

The selected Session detail header owns the text button `删除会话`; list rows
remain ordinary selection buttons. Every opening performs a fresh GET. The
dialog shows only Session ID, safe status/time, fixed explanations and
Session/terminal-Turn/terminal-Run counts. It never copies title, message,
preview, prompt, Run title, Tool data or a path.

Only a validated ready preview with one Session and no blockers/diagnostics
offers `删除会话及关联记录`. Busy offers GET-only status checking. Partial states
show remaining counts and require the explicit
`重新确认并继续清理` path, which GETs again before another user confirmation.

After `completed` or `already_absent`, a short in-memory tombstone and
generation increments fence old Session list/detail, Run list/detail and
runtime trace completions. The visible target disappears immediately, then
Sessions, Runs, snapshot and required Turn authority are reread. The
tombstone clears only after REST confirms absence. Matching selection storage
is cleared; unrelated Workspace storage is untouched. A Dock continuing the
target switches to new-Session mode. The unsent draft is retained byte-for-byte
and is never submitted or replayed.

## Project Memory interaction and convergence

Only Project-scope rows contain the small text action `删除`; User and Local
rows contain no destructive control. The dialog consumes only Memory ID,
scope, category, tier, lifecycle, approval and the three safe counts. It never
copies content, content hash, approval reason, provenance, related content or a
path.

Completion tombstones the ID, fences Memory and Memory Approval request/action
generations, removes stale selected/acting approval identity without making a
decision, preserves the current Memory filters, and rereads Memory, pending
approvals and snapshot. Old GET/POST/SSE completion cannot republish the ID;
the tombstone clears only after both collections confirm absence.

## Stale, conflict, partial and lost response

- `deletion_revision_stale` never reuses the POST. One safe preview GET runs
  and the changed scope requires a new confirmation.
- Busy/write conflict/store unavailable keep fixed safe state and expose only
  GET-based checking.
- A partial result clears the old preview, renders deleted/remaining counts and
  cannot automatically continue.
- A network error after POST sets `outcomeUnconfirmed`; the only recovery
  action is a preview GET. A 404 alone is not success—real collections must
  converge first.
- Invalid payloads are discarded wholesale and cannot trigger local cleanup.

Existing `resources.sessions` and `resources.memory` invalidations may refresh
an open preview. The application still has exactly one EventSource and no new
timer, polling loop, WebSocket or resource. No SSE/polling/error path can call
POST.

## Accessibility and Waku presentation

The shared dynamic surface has `role=dialog`, `aria-modal=true`, associated
title/description IDs, live status, focus entry, executable Tab/Shift+Tab
boundary wrapping, non-submitting Esc, and focus return to the opener. If the
target disappears, the nearest stable page region receives the handoff.

The low-saturation danger treatment retains the existing paper, fine-border,
mono-metadata Waku shell. At 700 px the dock starts collapsed, the dialog is a
single column, IDs wrap, all actions are full-width, visual and DOM/Tab order
match, and the backdrop does not expand document width. Reduced-motion removes
dialog/backdrop animation.

## Verification

- Initial full baseline: `2845 passed, 2 skipped, 3 warnings`.
- Final broad Dashboard/deletion/Chat/Cancel/Permission/Memory/packaging
  focused matrix: 574 passed.
- Final deletion/Web/v32/semantic focused rerun after the keyboard-order
  hardening: 267 passed.
- Final complete suites on the frozen production state passed `2855 passed, 2
  skipped, 3 existing warnings` in 188.35s and 188.25s, with the official
  evaluator between them.
- Production baseline plus semantic evaluator tests: 193 passed.
- Static gates: Ruff, targeted `py_compile`, full `compileall -q minicode
  scripts tests`, and every formal JavaScript `node --check` passed. pyright
  and mypy were not installed.
- Official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true, zero remote
  calls. Reports were written only to a task temp directory.

The final wheel is
`minicode_py-0.1.0-py3-none-any.whl`, SHA-256
`b7e5ccd3304d552fc9c2d9d38d93bd92090877b84baf57fde8c737371b0ae838`.
Archive inspection found the four formal web assets and no prototype or
task-only browser fixture. An isolated install served the exact final app/CSS
hashes and completed both real GET->POST deletions; Sessions, Runs and Memory
collections removed only the targets while adjacent, unlinked, User and Local
fixtures remained.

## Browser acceptance

An isolated HOME, Workspace and real Gateway supplied four Sessions, adjacent
and unlinked Runs, terminal/busy/partial Turns, two linked Project Memories,
approval audit, and User/Local entries.

At 1280x900 all eight main routes and all six Memory routes rendered. Real
Conversation deletion removed its Session/Turn/Run, kept adjacent/unlinked
records, switched the Dock to new mode and preserved the unsent draft. Busy
had no destructive action; partial required explicit continuation. Real
Project deletion removed the entry, audit and backlink while preserving the
neighbor, User and Local entries.

At 700x900 the final dialog had no horizontal overflow, a wrapped ID, distinct
close/action controls and visual footer order matching DOM/Tab order. Initial
focus, Esc close and opener focus return were exercised in Browser; executable
formal DOM tests cover both Tab boundary directions. The in-app Browser cannot
reliably synthesize a dropped HTTP response, so stale/lost-response/restart
and no-auto-POST cases are covered by deterministic production-script and
backend tests rather than claimed as browser simulations.

Both browser passes reported zero application console warning/error. Dialog
and final DOM scans found no raw body copy, private absolute path,
`[object Object]`, or unescaped server error.

## Production lineage and handoff

v32 is `memory-retrieval-production-v32`, parent v31, with exact two-changed,
zero-added, zero-removed lineage. Its manifest SHA is
`9680f6f4bb61d3489a98fd63cff01d99f6a5af2c98891befbfb6c513fc023fb1`.
Every v1-v32 pin and all 54 current files match. v31 remains byte-identical.

The accepted semantic gold remains SHA
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size 3,033,592 and mtime_ns 1784135857000000000. Dependencies remain `[]`.

Batch 8D is complete. Batch 9A-1 may rely on the four v31 routes, strict
preview/result schemas, fresh-revision rule, fixed safe errors, existing
sessions/runs/turns/memory invalidations, and the v32 GET-only reconciliation
behavior. It must not persist revisions, auto-retry POST, infer success from
local removal/404, add another EventSource, or change the certified authorities.
