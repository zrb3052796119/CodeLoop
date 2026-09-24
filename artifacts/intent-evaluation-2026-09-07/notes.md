# Evaluation notes

- Working tree contains many pre-existing modifications, including intent_parser.py and new semantic-classifier files.
- Production parser exposes intent/action, ordered operations with polarity, entities, constraints, context diagnostics and optional semantic fallback.
- Semantic fallback documentation states it is opt-in and defaults to disabled. Actual current wiring/configuration is under independent inspection.
- 30 routine prompts will be labeled locally; another agent will author 20 boundary/context prompts without reading implementation or existing fixtures.

## Observations
- Actual `load_runtime_config('.')` confirms semantic classifier enabled=false. Production enters parse_intent from skill_routing_turn.py:145, and the harness calls the same IntentParser.parse implementation.
- All 50 questions and relevant source hashes were sealed in freeze.json before execution. No label changes after seeing output.
- First-pass primary score: 26/50; all pre-labeled checks: 23/50. This latter measure checks explicitly annotated requirements, not exact equality of every field.
- 333 existing tests passed in the targeted parser/runtime/constraint/semantic classifier scope. Regression results do not estimate fresh-prompt generalization.
- Potentially consequential false affirmative operations: C15 configuration location -> configure/update; C17 PR review -> debug/update; C32 canceled fix -> debug/update; C35 review deletion risks -> code/delete plus retracted test/create; C38 hypothetical file deletion -> code/delete.
- C40 (question vs explain) and C47 (code update vs refactor) need separate semantic-equivalence adjudication; original labels and results stay unchanged.
- Context C46 and C48 identify action types but omit referenced files; C50 does not recover document editing.
- These are advisory parse outputs, not proof that tools actually execute these operations. No sample action or model call was executed.

## Final review
- Two independent reviewers agree C40 and C47 are reasonable coarse-label alternatives; C33 is a separate gray-area diagnostic/explanation distinction. Original scores and hashes remain unchanged.
- Six representative failures were reproduced via public parse_intent and matched all detailed fields other than timestamp.
- Evaluation harness checks only annotated fields. It does not fully validate argument targets, span boundaries, excess entities or all extra operations; even passing cases are not guaranteed exact in every field.
- Final report and all 50 case rows are preserved in report.md and case-results.md.
