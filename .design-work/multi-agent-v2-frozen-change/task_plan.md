# Task Plan: Frozen Workflow Change Capability

## Goal

Replace the mutable transaction's implicit freeze/verify/promote ordering with
an explicit frozen-change capability, so only a scoped, digest-bound object can
produce review evidence, verification workspaces, or mutate the parent.

## Phases

- [x] Phase 1: Create persistent plan and freeze constraints
- [x] Phase 2: Inventory callers/tests and compare three interface designs
- [x] Phase 3: Add red seam and state-transition regressions
- [x] Phase 4: Implement the frozen-change capability
- [x] Phase 5: Migrate scheduler callers and replace legacy-interface tests
- [x] Phase 6: Independent review and focused/affected/full verification
- [x] Phase 7: Conventional commit and handoff

## Constraints

- The scheduler remains the sole owner of dispatch, approval, commit timing,
  audit/trace projection, and final user-visible state.
- The workspace module remains a leaf and may not import orchestration modules.
- Serialized change-set and diff-artifact documents, digest formulas, path
  scoping, deadline/cancellation behavior, and promotion truthfulness remain
  byte/field compatible.
- Freeze must bind patch bytes, target state, allowed paths, and cwd-relative
  interpretation exactly once.
- Verification and parent promotion must be unavailable before freeze by
  construction, not merely rejected by a mutable-state check.
- Parent promotion must still validate the approved digest and current patch.
- Local filesystem, Git, and advisory locks are local-substitutable; no new
  adapter or registry is justified.
- Existing unrelated working-tree changes must remain untouched and unstaged.

## Key Questions

1. Does the frozen object own verification cloning as well as promotion?
2. Should the change-set document be returned by `freeze()` or exposed as a
   read-only projection on the frozen capability?
3. Which legacy methods should be removed versus temporarily aliased without
   preserving illegal state transitions?
4. How do integration tests inject commit failures through the new owner seam?

## Errors Encountered

- The first expanded regression run had one test-only failure: the unknown
  promotion injector used the design-sketch field name `_transaction`, while
  the implemented capability stores its owner as `_owner`. Updated the white-
  box injection seam; production code and the other 111 tests were unaffected.
- One combined diagnostic command produced more output than the tool budget;
  reran status, legacy-entry search, diff validation, and compilation as
  separate bounded checks. All completed successfully.

## Status

**Complete** — the explicit frozen-change transition, caller migration,
adversarial independent review, focused/affected/full verification, and scoped
conventional commit are complete.
