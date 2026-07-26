# MiniCode Dashboard Batch 8A-2.1

## Outcome

Batch 8A-2.1 closes the two frontend fail-closed gaps found during Batch 8A-2
acceptance. The formal browser now rejects hidden or contradictory permission
reviews independently of server booleans, and permanently retires a Turn's old
permission actions before any terminal path clears the active Turn identity.

The only production source changed is
`minicode/web/static/assets/app.js`. PermissionApprovalBroker remains pending
and decision authority, PermissionManager remains the Tool permission judge,
pending GET remains current-process truth, decision POST remains write truth,
and schema-v2 Change Feed/SSE remains content-free invalidation. No backend
schema, permission semantics, persistence, polling loop, EventSource, runtime
dependency, Batch 8B feature, or Batch 9 feature was added.

## Original defects and RED evidence

The review RED executed the formal bundle with a forged command item whose
`commandPreview` was the fixed `[REDACTED SENSITIVE REVIEW]` placeholder while
`reviewable`, `complete`, `truncated`, `redacted`, and choices falsely described
an ordinary safe review. The old `canAllowPermission()` path returned the item
instead of `null`, so the targeted pytest failed at the hidden-placeholder
assertion.

A final independent review added the same RED for an edit `diffPreview` equal
to the fixed placeholder. It failed before the last minimal tightening and now
passes, proving the shared rule covers both preview variants rather than one
test fixture.

The terminal RED established an active Turn with an allowable pending item,
entered cancellation, then invoked the existing cancellation finish path. Once
`finishCancelledTurn()` cleared `activeTurnId`, the retained item no longer
matched the active Turn and the old `permissionActionAvailable()` returned
`true`. The deterministic executable-bundle harness failed `true !== false`.
Companion harnesses fixed the stale pending GET, stale decision POST, and fresh
other-Turn recovery races before production code changed.

## Review contract hardening

`permissionReviewConsistent(item)` is the single pure consistency boundary used
by both payload validation and the independent Allow guard:

- `path` is always non-reviewable and deny-only;
- `edit` and `command` may be reviewable only when the review is complete,
  untruncated, unredacted, present, typed, and within the existing limits;
- the fixed hidden placeholder is never allowable;
- reviewable items have exactly `allow_once, deny_once`, in that order;
- non-reviewable items have exactly `deny_once`;
- unknown, missing, hidden, incomplete, truncated, redacted, or internally
  contradictory contracts are rejected;
- `canAllowPermission()` re-runs the consistency boundary, so bypassing the
  payload validator still cannot expose Allow;
- normal complete safe edit and command requests remain allowable.

The renderer retains the existing deny-only explanation and Waku-style layout.
There was no HTML or CSS redesign.

## Terminal permission retirement

`retirePermissionTurn(turnId)` is the sole terminal permission cleanup entry.
Before Chat clears or replaces its active Turn, it performs this order:

1. add the Turn ID to an in-memory retired-Turn tombstone set;
2. increment pending-request and decision-action generations;
3. clear acting state and immediately remove that Turn's local pending items;
4. start one authoritative pending GET reconciliation;
5. filter the retired Turn from every subsequent local Store publication.

The helper is invoked for direct Cancel terminal responses, Cancel 404,
cancelled/failed/interrupted cancellation outcomes, status 404, status
cancelled/failed/interrupted, completed with or without an available committed
Session, NDJSON terminal errors, JSON terminal errors, and JSON success. A
nonterminal `turn_in_progress` response is deliberately not retired.

The pending request ID fence makes an already-running old GET unable to publish
after retirement. The action-generation fence makes a late old decision POST
unable to clear or replace the newer Store state. Neither retirement nor
reconciliation sends a permission decision or replays a Chat request. A fresh
authority GET may immediately expose a different Turn's still-legal pending
item, while the retired Turn remains suppressed for the page lifetime.

## Exact file sets

Production changed:

- `minicode/web/static/assets/app.js`

Tests and certification support changed:

- `tests/test_dashboard_permission_frontend.py`
- `tests/test_dashboard_web.py`
- `tests/test_dashboard_chat_stream_frontend.py`
- `tests/test_memory_retrieval_production_baseline.py`
- `tests/test_memory_retrieval_semantic_gap_evaluator.py`
- `scripts/memory_retrieval_production_baseline.py`
- `scripts/generate_memory_retrieval_production_baseline.py`
- `tests/fixtures/memory_retrieval_production_freeze/v25.json`
- this document, the v25 baseline document, `implementation_notes.md`,
  `task_plan.md`, and `notes.md`

Production frozen and byte-identical to v24 include `index.html`, `styles.css`,
`cost-format.js`, Gateway, permission authority/HTTP/event contracts,
PermissionManager, Conversation, Change Feed, Event Stream, HTTP composition,
Agent Loop, RunJournal, Memory, Session, Skill, MCP, TUI, and Headless sources.

## Verification evidence

- untouched baseline: 2,437 passed, 2 skipped, 3 existing benchmark-marker
  warnings;
- Permission frontend matrix: 100 passed;
- Chat/Cancel/Turn matrix: 150 passed;
- Change Feed/SSE/live-refresh matrix: 46 passed;
- Dashboard Web/HTTP/packaging matrix: 76 passed;
- production baseline tests: 130 passed;
- scoped Ruff, selected `py_compile`, complete `compileall`, and both formal
  JavaScript `node --check` checks pass;
- an offline no-dependency wheel was built, its packaged `app.js` SHA matched
  source, it installed into an isolated target, and the installed Gateway
  static/permission/Chat/Cancel/Status/SSE smoke passed;
- first complete suite: 2,445 passed, 2 skipped, 3 existing warnings in
  191.28 seconds;
- official semantic evaluator: 108 cases, 37 confirmed gaps, Phase 3B true,
  zero remote calls, and pass;
- second complete suite: 2,445 passed, 2 skipped, 3 existing warnings in
  191.56 seconds.

At 1280x900, the real in-app browser against an isolated real Gateway proved
safe edit and command Allow/Deny, path/hidden/truncated deny-only rendering,
immediate old-Turn removal at Cancel response, no old-Turn revival after active
identity cleared, fresh other-Turn recovery, empty-authority restart clearing,
no horizontal overflow or permission/composer overlap, zero console
warning/error, and no seeded secret, HOME, absolute path, Tool input/output, or
`[object Object]` disclosure.

Batch 8A is formally closed. Batch 8B and Batch 9 were not entered.
