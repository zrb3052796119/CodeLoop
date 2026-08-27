# Task Plan: Multi-Agent final scenario validation

## Goal
Prove the centrally scheduled multi-Agent architecture behaves truthfully and fail-closed under realistic crashes, timeouts, conflicts, forged outputs, audit replay, and recovery, then report any remaining production risk.

## Phases
- [x] Phase 1: Freeze the change, test command, scenario matrix, and historical baseline
- [x] Phase 2: Map changed symbols to repository-native fixtures and existing fault tests
- [x] Phase 3: Add the smallest missing end-to-end scenario tests
- [x] Phase 4: Prove test signal against the pre-finalization revision and isolated semantic faults
- [x] Phase 5: Run focused and full verification, review failures, and fix only proven defects
- [x] Phase 6: Produce the final validation report and commit scoped changes

## Key Questions
1. Do all failures preserve Scheduler authority, committed truth, and durable traceability?
2. Can any Worker prose, forged artifact, hung port, or conflict outcome bypass a gate?
3. Do the new tests actually reject old or deliberately broken behavior?
4. Which timeout guarantees are process-local bounds versus true hard cancellation?

## Decisions Made
- Use `56d8cd8` as the fixed revision and `f8ae6fd` as the pre-finalization comparison baseline.
- Reuse production entry points and existing workflow fixtures; do not introduce a parallel test harness.
- Keep semantic probes isolated from the working tree.
- Preserve unrelated context-compaction changes already present in the workspace.

## Errors Encountered
- Test-stack auto-detection included unrelated dirty context-compaction files; explicit final-validation targets override that broad working-tree classification.

## Status
**Complete** - Final scenario validation, evidence review, and scoped hand-off are finished.
