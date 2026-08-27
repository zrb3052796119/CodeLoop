# Task Plan: Multi-Agent V2 Stage-One Repairs

## Goal

Repair the confirmed correctness and reliability gaps without changing the V2
workflow contract, then verify and commit only this repair set.

## Scope

- case-alias-safe forbidden-path enforcement;
- cross-platform parent-promotion locking;
- bounded Conflict Coordinator ports;
- typed sub-agent cancellation and complete concurrent-batch accounting.

Large `task.py`/Audit refactors and main-flow Conflict integration are out of
scope for this stage.

## Phases

- [x] Phase 1: Reproduce findings and choose module seams
- [x] Phase 2: Fix forbidden-path identity with one RED/GREEN slice
- [x] Phase 3: Reuse the cross-platform advisory-lock module
- [x] Phase 4: Bound Conflict Coordinator port execution
- [x] Phase 5: Replace string cancellation protocol and repair batch finalization
- [x] Phase 6: Close independent-review counterexamples, then run focused,
  affected, and full verification
- [x] Phase 7: Final independent review and conventional commit

## Interface Decisions

- Allow-scope containment remains filesystem-exact; case folding must never
  grant access on a case-sensitive filesystem.
- Forbidden-path matching is conservative and case-insensitive, matching the
  existing Scheduler policy without expanding authority.
- Host callbacks need a bounded runner seam; a deadline check after a blocking
  call is not a timeout.
- Human-readable error text is presentation only. Cancellation authority must
  be carried by a closed typed field.
- Already-started concurrent tool calls must receive a traceable terminal
  outcome before turn cancellation escapes.

## Errors Encountered

- Independent review found and converted the following edge cases into
  regression tests: alias/TMPDIR lock splitting, Windows path aliases,
  resolver-erased path evidence, cleanup exceptions replacing primary commit
  state, observer/authority capacity coupling, late fatal signals becoming
  timeouts, all-workers-hung cancellation, partial executor submission,
  cleanup exceptions replacing cancellation or finalized results, and journal
  quotas dropping required terminal evidence.
- The first full-suite run was sandboxed and could not bind loopback sockets,
  producing 127 failures and 125 setup errors. The identical command was rerun
  with local socket access and passed with 5,014 tests and 2 skips.

## Status

**Complete** — independently reviewed, fully verified, and prepared as one
conventional repair commit. The commit hash is reported in the task handoff.
