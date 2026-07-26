# MiniCode Dashboard Batch 9D-1B

## Outcome and boundary

Batch 9D-1B is complete. The user-selected **A / Agent Observatory** direction
is now the production hierarchy for Overview, with the same editorial visual
language applied to Runs, Sessions and all Memory subroutes. The v34
three-column Shell, responsive panel contract and authority-sensitive DOM hooks
remain intact.

This batch changes presentation only. It adds no backend route, Store, write
path, timer, poller, EventSource, dependency or mock fallback. Agent Loop,
Session/Run/Memory persistence, Chat, approval/deletion authority, REST/SSE
schemas, semantic evaluator and performance policy are unchanged.

## Agent Observatory interface

Overview owns a volatile, read-only `observatoryStore` projection over the
existing APIs:

- `GET /api/v1/runs?limit=6` supplies the latest retained work ledger;
- the existing Run detail route supplies the selected Run's safe summary and
  at most 50 retained events;
- the existing `runs` SSE invalidation refreshes Overview while visible and
  otherwise invalidates the existing Runs Store;
- no new polling fallback or second EventSource was introduced.

The page renders one continuous hierarchy:

1. Workspace status band with real Session, Memory, Skill, Run and MCP facts;
2. Current Run focus with persisted status, event, cost, tool and source facts;
3. six-event Activity trace;
4. retained historical Signals;
5. five-item Recent Work ledger linked to the existing Runs detail authority;
6. an explicit disclosure of read-only, partial and redaction boundaries.

Unavailable data remains unavailable rather than becoming a fabricated zero or
mock value. Prompt, message and Tool input/output payloads are not displayed.

## Core pages and compatibility fixes

Runs retains its real filters, selection, Run detail, linked Session and
existing loading/error/empty behavior. Sessions retains its REST-authoritative
list/detail and deletion controls. Memory retains Overview, Scopes, Retrieval,
Injection, Lifecycle and Approval subroutes plus all existing approval and
deletion actions.

The shared `core-page` wrappers, route kickers/decks and scoped grids apply the
same hierarchy without changing IDs or handlers. Browser acceptance exposed
and fixed two presentation-consumer seams:

- `#memory` now normalizes to the Overview subroute instead of passing `null`;
- Memory/Lifecycle re-renders when its already-existing Ops projection arrives.

Neither fix changes the producing Store or backend contract.

## Responsive and visual acceptance

The graphite navigation rail, warm paper main canvas and cool Session Dock
remain distinct at desktop width. The Observatory uses its own 900 px stacking
breakpoint so the main column does not overflow while the rail is still open.
At 375 px the page is single-column; the navigation reopen control is hidden
only while the full-width Dock overlay is open, preventing it from covering the
Dock title.

Real in-app Browser checks covered 1920, 1280, 768 and 375 px, all eight primary
routes and all six Memory routes. Final 1280×900 acceptance confirmed:

- the three-column Agent Observatory composition;
- Current Run, Activity, Signals and Recent Work all populated from real
  retained projections;
- no document horizontal overflow;
- no stuck loading state;
- empty application console warning/error logs.

The Browser host emitted an unrelated Statsig network timeout; it did not
appear in the Dashboard page console.

## TDD and verification

The dedicated Observatory contract began with five failures and reached seven
passes. It protects the selected hierarchy, real API reuse, existing SSE-only
invalidation, frozen action handlers, responsive breakpoints, Memory default
rendering and theme/surface agreement.

Final evidence:

- Observatory + visual-system focused matrix: `35 passed`;
- frontend action/SSE compatibility matrix: `75 passed`;
- Dashboard web + visual + Observatory matrix: `100 passed`;
- production baseline v1–v35 + semantic evaluator tests: `209 passed`;
- default Phase 2B evaluator: `28 passed`; combined Phase 2B suite:
  `36 passed`;
- full pytest: `2956 passed, 2 skipped, 3 existing benchmark-mark warnings`;
- active v35 verifier: 56/56 files, candidate/current true, v1–v35 integrity
  true, exact three changed/zero added/zero removed;
- official evaluator: 108 cases, 37 confirmed gaps, Phase 3B true, zero remote
  calls and passed;
- Ruff, full `compileall`, `node --check app.js` and
  `node --check cost-format.js`: passed.

## Wheel and installed-state proof

`python -m pip wheel . --no-deps --no-build-isolation` produced
`minicode_py-0.1.0-py3-none-any.whl` with SHA-256
`f028318b6ebf2c7286faef45e6d79fb5a86f857910b95f77b1f705f270970516`.

An isolated Python 3.13 environment imported MiniCode from `site-packages`,
outside the source working directory. All four formal static resources were
present and matched source hashes exactly. Its Gateway served `/`,
`/assets/app.js`, `/health` and `/api/v1/health` with the expected Content-Type
and `Cache-Control: no-store`; an unknown API returned structured JSON 404 and
an encoded `..` asset traversal returned 400. Runtime dependencies remain
`[]`.

## Baseline and handoff

The v35 evidence is in
`docs/memory-retrieval-production-baseline-v35.md`. Batch 9D-1C may reuse:

- the semantic token and three-column Shell interfaces frozen by v34;
- `core-page`, route kicker/deck and Observatory section primitives;
- all existing route IDs, data/action hooks and Store authority boundaries;
- the single EventSource plus REST-authoritative invalidation model.

Batch 9D-1C should visually unify Skills, Connections, Ops and System without
expanding their business contracts.
