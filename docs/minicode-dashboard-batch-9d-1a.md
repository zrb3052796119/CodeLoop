# MiniCode Dashboard Batch 9D-1A

## Outcome and boundary

Batch 9D-1A is complete. The formal Dashboard now has a Waku-inspired,
warm-neutral visual system and a responsive three-column Shell covering
navigation, the main Page Header, Chat Dock, resizers, reopen controls and
shared primitives. The implementation deliberately does not redesign the
internal Runs, Sessions, Memory or other page structures; that work remains in
Batch 9D-1B and 9D-1C.

No backend, Store, HTTP action, REST/SSE schema, EventSource count, polling
fallback, deletion authority, approval authority, Agent Loop, Memory behavior,
pricing/cost or accepted semantic truth changed.

## Evidence-based design direction

The read-only Waku audit is in
`docs/minicode-dashboard-batch-9d-1a-waku-audit.md`. Its directly inspected
sources established a 208 px navigation, 380 px Dock, fine resizers, compact
typography, warm neutral surfaces, thin borders and overlay-only elevation. The
formal MiniCode system interprets those principles rather than copying Waku's
brand, routes or product copy.

The retained `minicode/web/dashboard_prototype/` remained untouched and
continues to be a disposable historical reference.

## Visual-system interface

`styles.css` now defines stable token groups for:

- page, navigation, main, Dock, elevated, overlay, hover and selected surfaces;
- primary, secondary, muted, disabled and inverse text;
- subtle, standard, strong and focus borders;
- live, warning/partial, unavailable, danger and info states, each with a
  companion surface;
- local display/body/monospace font stacks with Chinese fallbacks and tabular
  data treatment;
- bounded typography, spacing, shape, elevation, motion, layout and z-index
  scales.

Light and dark modes override the same semantic token names. Page-internal
legacy aliases map to the new tokens so later batches can migrate internals
without a second Shell contract. There are no network fonts, remote images,
framework assets or build-chain resources.

The `live` layout selector was removed. Status meaning now uses explicit
semantic classes and text, avoiding the prior collision between layout and
business state.

## Three-column Shell

### Navigation

The left column uses a compact MiniCode identity, subordinate local-console
description, stable route labels, quiet tabular counts, restrained active and
hover states, and a short Workspace status footer. All eight hash routes,
`data-view` hooks and existing count hooks are unchanged.

### Page Header and main

The main column owns the primary vertical scroll. Its compact kicker/title and
secondary live/metadata row keep connection state visible without dominating
the page. The skip link still targets the focusable `#view`, and semantic
`nav`, `main` and `aside` landmarks remain.

### Chat Dock

The Dock retains Draft, new/history Sessions, Assistant/Tool streaming,
Permission approval, cancel/send, retry/reconciliation and Session authority.
The old authority paragraph is replaced by a short visible statement plus an
accessible `details` disclosure containing all synchronous/SSE/polling,
connection-scoped stream, final Session and loopback approval facts.

Memory approval remains a separate Memory-page authority and was not folded
into Chat.

### Resizers and panel state

Both separators expose role, orientation, controlled panel and numeric width
ARIA. Mouse drag and Arrow Left/Right work; Shift changes the keyboard step
from 8 to 24 px. Widths persist through the existing local Shell preference
keys. Collapse/reopen state synchronizes `aria-expanded`, hidden state and
focus return. Escape closes the history menu first, then the responsive Dock.

The Shell breakpoints are 1100 px for Dock overlay and 640 px for navigation
overlay. At 480 px both panels start closed and remain reopenable; the Dock and
composer use the full viewport without horizontal overflow.

## Shared primitives

Buttons, destructive actions, inputs, selects, pills, counts, cards, section
headings, metadata, code/IDs, dividers, toast, dialog/backdrop and
loading/empty/error/retry blocks now share the semantic token contract.
Disabled controls have reduced emphasis and a non-interactive cursor. Danger,
warning, unavailable and live states retain visible text or markers in addition
to color.

The stacking contract is:

`Dock 30 < reopen 40 < toast 80 < dialog 120 < skip link 140`.

## Responsive and accessibility acceptance

Real isolated Gateway browser passes covered 1280×900, 1024×768, 700×900 and
480×900. Desktop retained three adjustable columns with no document overflow.
At medium width the Dock became an overlay; at narrow width navigation and Dock
were independently reopenable and the composer remained available.

The pass exercised:

- all eight main routes and all six current Memory routes;
- Light Overview/Runs/Sessions/Memory/Dock and Dark
  Overview/Runs/Memory/Dock;
- mouse and keyboard resize, collapse/reopen, Escape and focus return;
- unsent Draft preservation through responsive Dock close/reopen;
- a real long Session ID, long Run ID and long Project Memory;
- real Memory approval, Tool Permission and Data Health projections;
- the real deletion dialog at 1280 and 480 px;
- SSE reconnect/polling-fallback status and manual retry controls.

At 480 px the deletion dialog measured 464 px inside a 480 px viewport, exposed
all close/cancel/destructive controls, initially focused Close and returned
focus to the deletion opener. The Tool Permission panel measured 455 px and
kept both Allow/Deny controls plus the composer visible.

The in-app Browser does not expose reliable page-zoom emulation; its keyboard
zoom attempt did not change the CSS viewport. The 480 px pass exercises a
stricter effective layout than 200% of the 1024 px desktop view, while formal
focus/reduced-motion/landmark/keyboard contracts remain executable tests. This
is recorded as a tooling limitation rather than a claimed native 200% zoom run.

Dark-mode media emulation was also unavailable. Dark screenshots were obtained
by temporarily changing only the media condition to an always-true audit
condition, reloading, and immediately restoring
`prefers-color-scheme: dark`; the final v34 hash and tests prove the production
condition is restored.

Application console warning/error logs were empty. DOM scans found no
horizontal overflow, invisible action, `[object Object]`, secret, or
user-machine absolute path. The Browser connector emitted one unrelated
Statsig network timeout while the Gateway was intentionally offline; it did
not appear in the page console.

## Before and after evidence

Screenshots are kept outside the package under:

- `/tmp/minicode-9d1a-visual-evidence/before`
- `/tmp/minicode-9d1a-visual-evidence/after`

Representative evidence:

| State | Before | After |
| --- | --- | --- |
| 1280 Overview | `before/1280-overview.png` | `after/1280-installed-overview.png` |
| 1280 Memory | `before/1280-memory.png` | `after/1280-memory.png` |
| 700 default | `before/700-default.png` | `after/700-installed-default.png` |
| 480 default | `before/480-default.png` | `after/480-installed-default.png` |
| Permission | — | `after/1280-installed-permission-panel.png` |
| Deletion dialog | — | `after/480-installed-deletion-dialog.png` |

Before at 1280 measured nav 208, main 682 and Dock 380. The final default token
widths are nav 216 and Dock 388; the installed acceptance also proved persisted
252/426 resize values with a 590 px main and no overflow.

## TDD and verification

The new visual/Shell contract began at 19 failures and 8 passes, then reached
28 passes. It verifies token groups, shared light/dark semantics, no component
raw status colors, routes/hooks, landmarks/focus, Chat/Permission/deletion
hooks, one EventSource/no new polling, responsive overlays, stacking,
reduced-motion, focus-visible, no network assets and no absolute paths.

Final evidence:

- visual + Dashboard/Chat/Permission/Memory approval/deletion/SSE/Data Health/
  packaging matrix: `215 passed`;
- production baseline and v34 tests: `171 passed`;
- active-v34 semantic/baseline rerun after closing one stale v33 assertion:
  `172 passed`;
- Phase 2B: `28 passed`;
- full pytest: `2943 passed, 2 skipped, 3 existing warnings` twice;
- Ruff, targeted `py_compile`, full `compileall`, `node --check app.js` and
  `node --check cost-format.js`: passed;
- pyright and mypy: not installed.

The first pre-fix full attempt produced `2942 passed` and one failure because a
semantic certification test still equated the active hashes with v33. The
test—not production behavior—was corrected to preserve v1–v33 as history and
recognize v34 as active; both required full passes then succeeded.

## Wheel and installed-state proof

`python -m build --wheel` is unavailable in the current environment because
the installed `build` package has no `build.__main__`. The repository-compatible
build command was:

`python -m pip wheel . --no-deps --no-build-isolation`

The final wheel SHA-256 is
`b472c5a9bbbb1f195a10673c5ad8cedf9ea1520820d33c9257bb08bbeb2ac61a`.
An isolated Python 3.13 environment imported MiniCode from `site-packages`,
with the source tree absent from `sys.path`, and served all four formal assets
from an unrelated working directory. Asset hashes matched source exactly;
Content-Type and `Cache-Control: no-store` were correct. Health aliases,
structured unknown-API 404 and encoded/plain path-traversal rejection passed.
Runtime dependencies remain `[]`.

## Baseline and handoff

v34 details are in `docs/memory-retrieval-production-baseline-v34.md`.
Batch 9A-2, 9A-3, 9B and 9C remain deferred by user and are not complete.
Batch 9D-2 remains only a possible Dashboard Visual Release Candidate until
that release-hardening work resumes.

The next task is Batch 9D-1B. Its stable interface is the semantic token set,
Shell landmarks and IDs, route/count hooks, Page Header, Dock authority
disclosure, responsive panel API, z-index ordering and frozen Store/HTTP/action
behavior established here.
