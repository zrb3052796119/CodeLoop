# Phase 4: positive-request recall and local negation

Contract: An explicit independent positive request must remain affirmed when an adjacent prohibition governs a different action; a single negated action sequence must not gain write authority when split.

Changes are in `recall-integration.patch`, based on the actual pre-phase-4 snapshot. The candidate was isolated under `recall-candidate`; shared production was integrated by root.

- Clause boundaries now recognize adverbs inside `不用/不必…真的/实际/急着…` prohibitions, `不要调整`, independent `直接重构/优化/调整`, sentence-initial `先帮我分析`, and later `我还不准备/不打算…` declarations.
- Shared polarity recognizes `不准备/不打算`, so newly separated declarations remain negative.
- The existing whole-sequence guard extends `不需要先…` and `然后/接着/随后`, while a comma before a new request still resets authority.
- The existing hypothetical-effect question grammar accepts `哪些` alongside `什么`. Real conditional commands such as `如果测试通过就删除 cache.py` remain affirmative.

Validation:

- `tests/test_intent_positive_recall_phase4.py`: 30 current tests pass.
- Candidate plus existing negation, clause-routing, protected-literal and phase-3 scope tests: 115 pass.
- Critical Ruff selectors E9/F63/F7/F82 pass.
- `recall-proof.json` uses shared production (`--fixed-root .`), the actual `.repair-work/intent-phase4/baseline`, and identical test bytes: current 30 pass; baseline 15 fail and 15 pass. Failures are behavior assertions, with no import/collection/setup failures.
- Test SHA-256: `515d7bb57ee6dc728569c8557f2fffd47743b14b97737f47fb4e994f7a19a47d`.
- `recall-candidate-proof.json` is an earlier proof of only the isolated recall patch, distinct from the integrated result.

Scope and limitations:

The tests accept either a deterministic read result or a fixed read proposal; they do not require an avoidable model call. Restoring access to a semantic classifier is not evidence that a live model will return a correct intent. Sentence-role candidate injection and write-proposal restrictions are independently owned by the phase-4 request-role work.

A development probe exposed the pre-existing unresolved case `如果重构这个模块，不用实际重构。`: the bare conditional precursor may still produce an affirmative refactor. It has no complete question frame or explicit subsequent analysis and remains outside this bounded repair. It was reported to root and the independent reviewer, not silently counted as fixed.

The independent reviewer was also asked to probe erroneous write proposals on newly restored colloquial read fallback requests (`先聊聊这些开关对输出的影响…`, `这俩选项是咋配合起来的啊…`), since proving acceptance of a correct read proposal alone does not establish safety against incorrect model output.

## Integration regression correction

The first integrated full suite found one existing entity regression: broadening the sentence-leading `先` modifier across every action split a retracted quoted file request from a following `先说明` operation. That removed a file previously retained as the subject of the read operation. The correction in `recall-boundary-fix.patch` narrows only the newly introduced modifier path to `先[请/帮我/麻烦]分析`, retaining the original explanation/source binding. This avoids inventing broader cross-sentence inheritance of quoted file targets.

A snapshot of current integrated production plus that one-hunk correction passed all 30 recall tests, the phase-2 entity tests, and protected-literal tests: 98 passed. The previously failing `test_explained_retracted_literal_survives_top_level_entity_projection` passes, as do T09 and its two conditional-analysis variants. No test bytes changed.
