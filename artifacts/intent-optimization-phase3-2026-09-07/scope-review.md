# Phase 3 adjacent negation scope and independent governor review

## Contract and diagnosis
Given an affirmative current command beside a separate prohibition or a completed non-use status, parsing must retain the command and its target while keeping actual prohibited actions negative.

H36 was not caused by retroactive `is_span_negated`: the comma splitter omitted `这次不用重构`, so a strong refactor pattern suppressed the vague initial review within one coarse clause. H13 retained a whole clause, so `不用` in the completed status governed the later deletion trigger. English `cache.py is no longer used, delete this file` had the same scope leak.

## Minimal integration
`scope-integration.patch` was prepared against the actual phase 3 snapshot and integrated by the parent. It adds explicit comma boundaries before independent Chinese non-necessity prohibitions, and a conservative completed-status exception to `is_span_negated`. The latter requires a subject with no prior action marker, completed status syntax, and a comma before the subsequent operation; action cancellation and present prohibition are not released. The original operation text stays available for the file target.

Production files were not edited by this reviewer. `scope-candidate/` is a disposable candidate used for diagnosis; it is not the proof baseline.

## Exact regression proof
- Mode: GENERATE, followed by AUDIT.
- Test: `tests/test_intent_scope_phase3.py` (33 cases).
- Actual baseline: `.repair-work/intent-phase3/baseline`.
- Current root: 33 passed.
- Identical final test bytes on actual baseline: 12 failed, 21 passed.
- Status: PROVEN, `scope-proof.json`.
- Inspected baseline failures: only target assertions about lost REVIEW or missing affirmed DELETE; no imports, fixture errors, or dependency failures.
- Protected behavior: seven current status/deletion commands including the original H13 file target; adjacent review plus true denied refactor/execution; earlier denial plus later command; explicit cancellations and choreographed negated sequences; six direct shared-negation controls.

## Independent root nominal governor review
Twenty fixed read/action/sequence probes were run against the actual baseline and current root, with no LLM calls and no holdout access. Evidence: `nominal-independent-probe.py`, `nominal-independent-baseline.json`, `nominal-independent-current.json`.

Two newly introduced command losses were reported to the parent:
1. `阅读 app.py 后直接更新函数。` loses affirmed CODE/UPDATE.
2. `解释 app.py 的逻辑后立刻修改函数。` loses affirmed CODE/UPDATE.

The reset regex only recognizes `后` immediately before the operation trigger; intervening direct/immediate adverbs leave the earlier read verb incorrectly governing the real following command. Parent was asked to retain explicit `后 + 直接/立即/立刻/马上` sequences and add controls. Recheck pending.

Four independent nominal-object probes correctly became read-only (deleting-files function, identifier generation function, renaming script, coordinated file/deletion-function object). Other probes were unchanged; this is not a claim that all baseline behavior was correct.

## Deferred pre-existing observations
The literal `sandbox_preview.py` can trigger an old substring REVIEW pattern through `review`, e.g. English `is no longer needed, remove this file`; the original H13 remains in the regression suite, while the adjacent-scope English controls use `cache.py` to isolate this task. `Review cache.py, refactoring is forbidden.` also exposes a pre-existing passive-forbiddance scope leak. Neither issue was silently expanded into this patch.
