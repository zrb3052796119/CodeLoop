# Task Plan: Batch 9D-1A.1 Three Visual Prototypes

## Goal

Create exactly three comparable, runnable static Dashboard visual directions
using one mock scenario, then certify their browser behavior without changing
production files, v34, packaging, tests, semantic truth or the Batch 9D roadmap.

## Phases

- [x] Phase 1: Record v34, production hashes, wheel boundary and current/Waku visual audit
- [x] Phase 2: Define the shared information contract and implement three structurally distinct variants
- [x] Phase 3: Add mock-only interactions, accessibility and responsive behavior
- [x] Phase 4: Run static/security checks and real local-HTTP browser inspection
- [x] Phase 5: Capture six comparable screenshots and write the evidence-based comparison
- [x] Phase 6: Reprove byte-identical production, v34 and wheel exclusion, then clean task resources

## Key Questions

1. Which structural difference makes each direction memorable without changing
   the underlying information?
2. Which direction best balances live Agent observability, Chat and approval
   authority at desktop and narrow widths?
3. Can every interaction remain in-memory and visibly marked as mock/prototype?
4. Can the entire Spike remain outside `minicode/`, the wheel and v34?

## Decisions Made

- Use the UI-prototype branch: one static route with `?variant=A|B|C` and a
  floating comparison switcher.
- Use the exact same DOM information contract and mock scenario for all three
  variants while allowing each variant to reorganize that content.
- Keep all state in memory; no fetch, EventSource, storage or external request.

## Errors Encountered

- None.

## Status

**Complete** — three comparable prototypes, six screenshots, browser evidence,
comparison report, frozen-boundary proof and task-resource cleanup are complete.
