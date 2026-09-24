# Phase 3 fresh sealed holdout authorship

- Author: independent sub-agent `/root/phase3_fresh_holdout`.
- Frozen at (UTC): 2026-09-07T08:46:34.755631+00:00.
- Scoring contract: `phase2-action-groups-v1`; alternatives within each required operation group are OR, groups are AND.
- Holdout file: `holdout.json` (UTF-8 JSON array).
- Holdout SHA256: `72b912439196384499b50a8d135650f5a5a95e14f9b73b6d5775854452840622`.
- Distribution: 50 cases; 40 Chinese, 10 English; 30 read-only, 20 actual write or execution requests; 12 cases with nonempty conversation context.
- IDs: T01 through T50, unique and contiguous.

## Independence and freeze declaration

The author read only `.repair-work/intent-phase2/score_holdout.py`, `.repair-work/intent-phase2/score_loader.py`, and the original `score` function extracted from `artifacts/intent-evaluation-2026-09-07/run_evaluation.py`. No production parser source, existing question sets, tests, evaluation results, reports, or other agents' work were inspected. The coordinator confirmed only the context object shape and received only schema/distribution information before completion.

All questions and annotations were authored before any parser or model output for these cases was visible. No parser, external model, or evaluation run was invoked. The cases are frozen on creation and must not be relabeled in response to evaluation outputs. Both questions and rationales were kept sealed from the coordinator during authorship; only final hashes and distribution are reported.

## Annotation choices and self-check

The holdout mixes ordinary development requests with descriptions, quotations, hypothetical actions, negation, contrast, references to prior turns, and compound requests. Positive controls require retaining real implementation, configuration, documentation, file-operation, and test-execution actions. Read-only cases prohibit affirmative mutating or execution actions. Expected intent alternatives express reasonable semantic neighbors, and required operation groups preserve individual requested actions without requiring all synonymous intent labels.

Optional exact-entity assertions are left empty because this holdout evaluates intent and action semantics, not the unobserved entity-container naming convention. One unambiguous search-then-explain case asserts intent order with its rationale. Other cases avoid overconstraining order when synonymous intent labels would make the scorer's simple ordered-label list unsuitable. Every case contains every scoring field, an explicit context shape, language, category, and rationale.

Before writing, a deterministic local structural check verified case count, unique contiguous IDs, all fields, legal intent and action enums, nonempty operation groups, group-choice shape, context-turn shape, language totals, read-only/write-execution totals, nonempty-context count, and basic contradiction checks. This validation did not instantiate or execute the intent parser. The JSON SHA256 was computed from the exact UTF-8 bytes written. The authorship file's SHA256 is returned separately so it can be frozen by the evaluation coordinator without a circular self-hash.
