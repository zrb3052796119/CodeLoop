# Evidence notes

- implementation.md explicitly says RecoveryLessonAgent is not yet wired into production selection and real-model shadow evaluation is deferred.
- Review preserves all pre-existing source/test/artifact edits.
- Focus on actual code behavior, not reported pass counts alone.

## Parent reproduction findings

Focused suite: 142 passed in 1.80s (9 modules, original 7 plus recovery_misassociation_regression and recovery_lesson).

identity_probe.py uses current registered tool validators and the real trace emitter with synthetic ToolResults. No actual command, edit, or production memory write occurs. Four fresh counterexamples all yielded value_accepted=true and confirmed recovery in structured_claims:

1. Same cwd /repo/workspace, tests/test_auth.py fails then tests/test_billing.py passes: cwd itself appears in whole-path resource intersection.
2. Different cwd /repo/backend vs /repo/frontend, same relative tests/test_config.py: relative operands compared before cwd resolution.
3. tests/test_auth.py vs backend/tests/test_auth.py: pure suffix treated as identity without absolute anchor/rename evidence.
4. pytest tests/test_auth.py -k=expired fails, docs edit, then -k=valid passes: selector option discarded from target and shared source file incorrectly counts as same verification objective.

Locations: reflection_evidence.py:848–886,2027–2034,2336–2363. Prefer structured target identity; parse cwd as anchor rather than resource; preserve selector semantics; uncertain associations must not confirm.

## Parallel findings under verification

- Quote abstraction skips DecisionEvidence inferred status if statement is paraphrased; parser/validator/value all accept confirmed (new regression in shared LLM path).
- Episode projection drops earlier failed checks required for red-green relationship validation.
- Candidate episode extraction chooses first successful same tool, e.g. git status, and hides actual later timeout retry.
