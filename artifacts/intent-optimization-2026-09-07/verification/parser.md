# Intent discourse repair — final validation

This bounded change repairs action roles and authority scope in `minicode/intent_parser.py` and `minicode/intent_negation.py`. The pre-existing dirty worktree was preserved. No real model calls or requested filesystem actions were executed by this subtask.

## Final behavior

- Configuration-file nouns, exception-handling nouns, and accidental deletion descriptions no longer create configuration, repair, or deletion commands.
- Direct document targets take precedence over generic code labels; explicit documentation complements such as deployment steps remain document edits. A separate request to add executable function behavior remains a code operation.
- Prefix retraction and explicit postposed request cancellation preserve negative operations. Retraction words used as business feature names do not negate the surrounding review.
- Sentence boundaries end local negation. Chinese memory directives are recognized at command positions; remembered-feature names and retrospective queries do not become memory writes.
- Hypothetical events supply read-only explanation topics, while explicit conditional commands and later real commands keep their own targets. Pure hypothetical fragments, quoted payloads, and their reporting prefixes cannot be sent back to the semantic provider as missing commands.
- A polite recovery request following an accidental deletion report remains available to semantic fallback, with an exact original-text span. The existing downstream context limitation is unchanged: its semantic fragment does not itself carry the earlier file entity, although the aggregate parsed intent retains that entity.

## Verification

- `python -m pytest tests/test_intent*.py tests/test_skill_router*.py tests/test_skill_required_operation_redteam.py tests/test_skill_explicit_reference_grammar.py -q`: **2381 passed** in 55.49 seconds. See `regression-final.log`.
- Final new regression file: **66 passed**. It covers the original failures, natural variants, direct affirmative commands, subsequent real execution, and an adversarial fake provider that always proposes a write.
- `python -m ruff check minicode/intent_parser.py minicode/intent_negation.py tests/test_intent_discourse_phase1.py`: passed.
- `python -m py_compile` for the same three files: passed.
- Independent review reran 16 fixed-scope examples across memory nouns, conditional commands, recovery, business retraction nouns, document/code separation, empty hypothetical conditions, and reported quotations. All passed; see `../review-parser.md`.

## Real-baseline counterexample

Mode: GENERATE / AUDIT.

Contract: when a user discusses, withdraws, or constrains an action, parsing must preserve the actual current task and explicit constraints without manufacturing a second effectful command; independently requested real actions and unresolved real requests must remain available.

Baseline: exact pre-fix source snapshot in `../baseline`, frozen before any edits. Every source hash was rechecked against `../baseline-freeze.json`.

The bundled test-counterexample verifier ran the **same final test bytes** with the same interpreter and command on current production and an isolated copy of that snapshot:

- Fixed: **66 passed**, exit 0.
- Baseline: **45 failed, 21 passed**, exit 1.
- Status: **PROVEN** for the regression suite. Baseline-passing cases are affirmative/control contracts, not claimed new bug counterexamples.
- Evidence: `counterexample.json`; complete baseline assertion diagnostics in `baseline-assertions.log`. Failures were inspected as target `AssertionError`s, not imports or setup failures.
- Final test SHA-256: `b7aff9553258e493fda520b436e08737ee1c397515baf7e903fa2a27c7012d7d`.

`final-source-hashes.json` records the frozen production and test bytes. `intent_parser.patch` and `intent_negation.patch` are diffs against the actual pre-fix snapshot, excluding unrelated earlier dirty changes.

## Scope limits

This is a deterministic discourse repair, not a new semantic adjudication architecture. Unknown read-only requests can still depend on model classification; the remaining ability of a model to propose an incorrect label for such an unknown request predates this change and is reserved for the next routing stage. No permission or semantic action allowlist was broadened.
