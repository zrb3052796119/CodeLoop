# Analysis-boundary regression after the first frozen evaluation

The initial phase-4 evaluation exposed a regression in U10, a factual report followed by `。先帮我分析…` and a separate prohibition. The new unconditional analysis boundary split the report from its requested analysis. Neither resulting positive clause had a surviving rule operation, and the semantic fallback deliberately refuses two unresolved clauses. The combined request had been eligible for one fallback call before phase 4.

The final correction keeps factual reports attached to the analysis they supply a subject for. The new `先[请/帮我/麻烦]分析` boundary is used only when the preceding clause matches the existing hypothetical-prefix grammar. T09 and related hypothetical-action cases still separate their discussed action from the affirmative analysis. No semantic admission policy or helper changed in this correction.

Validation:

- New test file: `tests/test_intent_analysis_boundary_regression_phase4.py` (12 cases: three factual report/analysis regressions, three hypothetical analysis controls, three denied-action controls, and three real command/target controls).
- The 12 new tests plus the preceding 86 phase-4 tests and phase-2 entity tests passed together: 144 passed.
- `analysis-boundary-proof.json`: shared final production 12 pass; real frozen `evaluated-candidate` 3 fail, 9 pass. The three failures assert that one factual report-plus-analysis request reaches the semantic adapter; the buggy version makes zero calls. There are no import, syntax, or collection failures. Both sides use identical test bytes.
- Test SHA-256: `f24e7baf4aa80888324011ae001b58c50c33d3ee1359b9bdff6ec695fac324de`.
- Critical Ruff selectors E9/F63/F7/F82 pass.
- The exact production delta from the evaluated candidate is retained in `analysis-boundary-fix.patch`.

Claim limits:

This is a post-evaluation repair using a newly exposed case as development evidence. The original blind results belong to the earlier frozen candidate and must remain unchanged. Tests with fixed read proposals establish restored parser behavior, not improved live-model accuracy on unseen prompts.

The pre-phase-4 semantic path already admitted an erroneous `CODE/UPDATE` or `CODE/CREATE` proposal for U10's factual-report-plus-analysis frame. Restoring that path restores this existing limitation too. The narrowly recognized discussion/question gate does not cover every factual background followed by an analysis request. This correction must not be described as proving universal read-only proposal safety.
