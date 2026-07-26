# MiniCode Dashboard Batch 8A-2.2.1

## Outcome

Batch 8A-2.2.1 passes. File review now classifies invisible controls before
`splitlines()` or any other transformation can erase them or turn them into a
plausible safe Diff. Dangerous content has one content-independent preview,
cannot be allowed, and is never written. Batch 8C-2 was not implemented and is
the next authorized batch.

## Root cause and final call graph

Python `str.splitlines()` treats VT, FF, NEL, U+2028, and U+2029 as boundaries.
The former producer and projector therefore removed some actual file code
points before classifying the review. The former projector also omitted bidi,
zero-width, BOM, and surrogate classes.

```text
write_file / edit_file / patch_file
  -> resolve_tool_path(...)
  -> apply_reviewed_file_change(workspace, resolved target, before, after)
       -> canonical workspace-relative target label (Batch 8A-2.2)
       -> precheck before + after with contains_unsafe_review_character()
       -> dangerous: fixed [REDACTED SENSITIVE REVIEW]
       -> safe: build normal unified Diff
  -> PermissionManager.ensure_edit(...)
  -> PermissionApprovalBroker projector
       -> precheck raw review before parsing
       -> validate headers, target, body, size and sensitive material
       -> dangerous: redacted=true, reviewable=false, choices=[deny_once]
  -> unchanged Permission REST / Dashboard card / TUI decision authority
```

The shared classifier allows tab and LF, and allows CR only when immediately
followed by LF. It rejects all other C0/C1 controls, U+200B–U+200F,
U+202A–U+202E, U+2060–U+206F (including currently unassigned U+2065), U+FEFF,
U+2028/U+2029, and Unicode category `Cs`. Existing ANSI, secret, private-key,
absolute-body-path, mismatch, redaction, and truncation guards remain deny-only.

## Production changes

- `minicode/file_review.py`: shared pre-transformation character classifier and
  fixed-marker Diff producer.
- `minicode/permission_approval.py`: raw-value precheck plus projector
  defense-in-depth using the same classifier and LF-only parsing.

No frontend, REST/SSE schema, Change Feed, Permission Store, Memory authority,
Memory pipeline/store, Session, Agent Loop, Conversation Turn state machine,
RunJournal schema, TUI, or dependency changed.

## RED and GREEN evidence

The first v28 public-seam RED produced 18 deterministic failures: 17 unsafe
splitline/format/zero-width values were incorrectly reviewable, while a lone
surrogate failed before a pending review could serialize. The extended range
RED found that category-only classification misses U+2065 because it is
currently `Cn`; the final contract therefore enforces the entire explicit
U+2060–U+206F range.

The final real Tool/Broker/HTTP matrix covers representative and boundary C0,
C1, splitline, format, bidi, zero-width, BOM, high/low surrogate, ANSI, and
every insertion/deletion position. It covers new and existing writes, edit
add/delete/replace-all, patch single/multiple replacements, forged broker
input, worker completion, cancel, timeout, close, and restart. Every dangerous
item is exactly:

```text
kind=edit
reviewable=false
choices=[deny_once]
review.redacted=true
review.diffPreview=[REDACTED SENSITIVE REVIEW]
```

Dangerous Allow returns HTTP 409 `permission_not_reviewable`; Deny returns 200
and wakes the waiting Tool; neither path writes. Direct prompt, event, serialized
payload, exception, and RunJournal assertions prove the raw code point and file
payload are absent.

Safe tab-indented Python/JavaScript, LF, CRLF, Chinese, Latin Unicode, emoji,
spaces/Unicode paths, no-final-newline, empty-to-nonempty, and multiline values
remain unredacted, reviewable, and expose both operation-scoped choices.

## Compatibility and package evidence

The complete Batch 8A-2.2 path matrix remains green: absolute, `/var` alias,
dot/safe-dot-dot, internal symlink, spaces, Chinese/Unicode, and leading-dash
paths normalize to one workspace-relative label; external/escaping symlinks,
forged/mismatched headers, body paths, secrets, private keys, and truncated
reviews fail closed.

Permission authority/HTTP/frontend, Chat/Cancel/Turn/Session/SSE/RunJournal,
TUI, lifecycle, health, Memory Approval, and package compatibility pass. The
installed-wheel real Gateway smoke uses the installed production modules and a
real `write_file`: a safe absolute workspace path remains allowable, an actual
U+202E body is fixed-marker deny-only, its Allow is 409, and Deny leaves the
file absent. Runtime dependencies remain `[]`.

Focused results:

- File-review normalization and real Tool/Broker/HTTP/lifecycle: 238 passed.
- Production-baseline and semantic-evaluator tests: 181 passed.
- Installed-wheel hardening smoke: passed.
- First final complete suite: 2,773 passed, 2 skipped, 3 existing warnings.
- Evaluator-after final complete suite: 2,773 passed, 2 skipped, 3 existing
  warnings.

Scoped Ruff, selected `py_compile`, full `compileall`, and formal JavaScript
`node --check` pass. `pyright` and `mypy` are not installed, so neither is
claimed.

## Frontend, semantic, and browser evidence

The formal frontend remains byte-identical:

- `index.html`: `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`
- `app.js`: `1508700d7d75d99f6a5c166172c89f761e81100bc6d89f6b2873731c1e747ccb`
- `styles.css`: `092dd3279f613f802a050276db833d386c30663e6277fb5152597d966149d3e8`
- `cost-format.js`: `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`

Accepted semantic gold stayed at SHA
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
3,033,592 bytes and mtime_ns 1,784,135,857,000,000,000. The official evaluator
passed 108 cases / 37 gaps / Phase 3B true / zero remote calls.

At 1280×900, an isolated real Gateway, real broker, real PermissionManager,
real `write_file`, and controlled provider-free runtime proved safe Allow writes
exactly once with relative headers. VT, NEL, U+2028/U+2029, bidi, zero-width,
and BOM cards displayed only the fixed marker and Reject; raw characters never
entered the DOM and no target was written. Cancel retired the card. Restart
retired the old ID; its subsequent Allow returned 404 `permission_not_found`,
and a fresh post-restart approval remained usable.

All eight main routes and five Memory subroutes rendered without horizontal
overflow or navigation/main/Dock overlap; the approval card did not cover the
composer. The console contained zero warning/error entries. A final clean
Session DOM contained no local absolute path, case marker, Tool payload,
dangerous character, or `[object Object]`.

Production baseline details are in
`docs/memory-retrieval-production-baseline-v29.md`. Batch 8C-2: Memory Approval
Store + UI is the next authorized task.
