# MiniCode Dashboard Batch 8A-2.2

## Outcome

Batch 8A-2.2 passes. A real workspace-local absolute path supplied to
`write_file`, `edit_file`, or `patch_file` now produces one stable POSIX
workspace-relative Diff label. Safe reviews expose both operation-scoped
Allow-once and Deny-once; aliases cannot leak their original absolute spelling.
Batch 8C-2 was not implemented and may now resume.

## Final call graph

```text
write_file / edit_file / patch_file
  -> resolve_tool_path(context, original input, "write")
  -> resolved target
  -> apply_reviewed_file_change(context, original input, resolved target, content)
       -> resolve context.cwd strictly
       -> resolve target and prove target.relative_to(workspace)
       -> validate canonical POSIX label
       -> build_unified_diff(relative label, before, after)
  -> PermissionManager.ensure_edit(resolved target, diff)
  -> PermissionApprovalBroker
       -> require exact --- a/<targetPath> / +++ b/<targetPath> headers
       -> inspect the unchanged body for sensitive/absolute/control content
  -> pending REST -> existing Dashboard Permission card
```

The original Model/Tool input remains an internal argument for compatibility
and never enters the public Diff label. No regex tries to reconstruct a target
from an untrusted complete Diff.

## Canonical label and fail-closed rules

The label is `resolved_target.relative_to(resolved_workspace).as_posix()`.
It must be non-empty, relative, use `/`, contain no empty, `.` or `..` segment,
contain no `file://`, and contain no Unicode control/format/surrogate category.
Missing/non-directory Workspaces, external targets, escaping symlinks,
unresolvable targets, newline/NUL/control filenames, and forged or mismatched
Diff headers fail closed with fixed path-free output.

Only the two labels are normalized. Diff body bytes are not rewritten into an
allowable form. Workspace, HOME, other local absolute paths (including a path
at the start of an added/deleted Diff line), Bearer/API credentials, credential
URLs, private keys, ANSI/control bytes, and over-budget previews remain
redacted or truncated, unreviewable, and deny-only.

## Production changes

- `minicode/file_review.py`: shared resolved Workspace-relative label boundary
  used by all three real file Tools.
- `minicode/permission_approval.py`: strict header/target consistency plus
  complete body fail-closed classification.

The second file was required by RED evidence: after the producer-only fix,
other absolute paths, ANSI/control content, and private keys in changed content
were still reviewable. No frontend, REST schema, SSE schema, state machine,
Agent Loop, Session, RunJournal schema, Memory authority/pipeline/store, TUI,
Headless, or dependency change was made.

## Test evidence

The first RED against v27 produced four failures and one compatible relative
case. Absolute write/edit/patch reviews were deny-only; a macOS `/var` versus
`/private/var` alias could remain allowable while exposing the alias. A second
producer-only RED produced three failures for an unrelated absolute path and
ANSI/control body. Private-key and added-line-leading absolute-path REDs then
fixed the remaining projector gaps.

The final real-Tool matrix covers relative, absolute, dot, safe `..`, canonical
alias, new/existing/empty/no-final-newline/CRLF behavior, spaces, Unicode,
Chinese, leading dash, nested paths, edit replace-all, and multi-replacement
patch. External absolute paths, unsafe `..`, escaping symlinks, unavailable
Workspaces, and control filenames cannot become allowable reviews.

Side-effect tests prove no write before Allow, exactly one write after Allow,
no write after Deny/Cancel/timeout/close/restart, and independent Permission IDs
for repeated operations. No session/global Allow cache is written. Existing
TUI choices and caches are unchanged.

Focused results:

- Final Tool/Permission/HTTP/frontend matrix: 237 passed.
- Baseline plus semantic tests: 179 passed.
- Installed-wheel suite: 9 passed.
- Existing Permission authority/HTTP/frontend matrix: 174 passed.
- Existing Chat/Cancel/Turn/Session/SSE/RunJournal/lifecycle matrix: 247 passed.
- First final complete suite: 2,572 passed, 2 skipped, 3 existing warnings.
- Evaluator-after final complete suite: 2,572 passed, 2 skipped, 3 existing
  warnings.

The warnings are only the three pre-existing unregistered `benchmark` markers.
A transient Phase 2B timing gate failed while run under restricted scheduling;
the unchanged test passed under the same localhost permission profile as the
complete suite, and both final complete suites passed. No timing threshold or
evaluator code was changed.

## HTTP, wheel, and frontend compatibility

Real loopback pending GET returns `kind=edit`, relative `targetPath`, complete
unredacted safe Diff, and `allow_once`/`deny_once`. Real decision POST wakes
only the matching Tool; stale IDs, wrong Turns, opposite/duplicate decisions,
cancel, timeout, terminal Turns, restart, no-store, Origin, Content-Type,
query rejection, and fixed safe errors retain their existing contracts.

The wheel contains both production files. Its isolated, source-tree-external
smoke uses the installed real Gateway and `write_file`: absolute input becomes
a relative review, Allow writes once, Deny does not write, and a sensitive body
has no Allow. Health, Chat, Cancel, Status, SSE, Memory Approval, static assets,
Permission GET/POST, and package imports also pass. Runtime dependencies remain
`[]`.

The formal frontend files are byte-identical before and after:

- `index.html`: `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`
- `app.js`: `1508700d7d75d99f6a5c166172c89f761e81100bc6d89f6b2873731c1e747ccb`
- `styles.css`: `092dd3279f613f802a050276db833d386c30663e6277fb5152597d966149d3e8`
- `cost-format.js`: `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`

Existing frontend validators and Allow guards therefore remain fail-closed.

## Browser acceptance

An isolated real Gateway, real broker, real `PermissionManager`, real
`write_file`, and controlled provider-free runtime were exercised in the
1280×900 in-app browser. The safe absolute-input card displayed
`code/hello.py`, relative `a/code/hello.py` and `b/code/hello.py` headers, and
both actions. The file did not exist before approval; Allow wrote exactly
`print("hello")\n`, the Turn continued, and the card retired.

Deny left its target absent and surfaced a real failed Turn. A sensitive API
key body displayed only the fixed redacted review and Reject; the secret and
absolute paths never entered the DOM. Cancel immediately retired its pending
card and wrote nothing. A process restart did not revive the old pending item,
and a fresh post-restart request completed normally.

All eight main routes and all five Memory subroutes rendered. At every route,
document width equalled the 1280 px viewport, navigation/main/Dock rectangles
did not overlap, and the Permission card did not overlap the composer. Page
console warning/error count was zero. DOM scans found no `/Users/`, `/private/`,
test secret, or `[object Object]`.

## Static and cleanup evidence

Scoped Ruff, selected `py_compile`, complete `compileall`, and both formal
JavaScript `node --check` commands pass. `pyright` and `mypy` are not installed,
so neither is claimed. The isolated Gateway processes, browser tab, viewport
override, HOME, Workspace, files, and helper script were removed.

Production baseline details are in
`docs/memory-retrieval-production-baseline-v28.md`. The next authorized batch
is Batch 8C-2: Memory Approval Store + UI.
