# Reflection Golden Dataset

This directory contains synthetic, manually labelled execution traces for measuring the current `ReflectionEngine`. The labels describe facts present in each trace; they are not generated from current engine output.

## Layout

- `schema.json`: versioned JSON Schema for every case file.
- `cases/*.json`: category cases, the Trace Contract v2 extension, and 30 claim/value-gate cases.

## Annotation Rules

1. File roles come from explicit tool semantics and structured fields. A shell command, URL, or prose fragment is not itself a file path.
2. A library is `confirmed` only when dependency/import/install evidence exists. `weak_mention` and `not_dependency` are negative labels for dependency precision.
3. One logical error may cite multiple source events. Same-call `tool_result` and `error` records are one error; separate failed calls remain separate occurrences.
4. Recovery is an action taken after failure. Verification additionally requires an observable check and result; a statement that something is fixed is not verification.
5. Decisions and claims carry an epistemic status. `confirmed` requires direct evidence, `inferred` is explicitly tentative, and `unknown` forbids a fabricated root cause.
6. Routine reads, listings, searches, formatting, and already-green checks are low-value unless they reveal a durable constraint or reusable correction.
7. Secrets are placeholders or already redacted. No fixture is sourced from a real user session, memory file, credential, or external service.
8. Required terms are semantic anchors, not an exact expected output string. Evidence references point to stable explicit IDs or deterministic `legacy-event-NNNNNN` fallback IDs.
9. The original 40 case IDs remain the shared before/after comparison set. Eight additional cases exercise Trace Contract v2 and legacy fallback behavior; 30 `claim-value-*` cases exercise synthesis, validation, value selection, and persistence policy.
10. `validation_probe_claims` is evaluator-only. It permits deliberately malformed candidate claims to exercise the production `ReflectionClaimValidator` without exposing a production candidate-injection interface. Invalid references in these probes are intentional and are never accepted claims.

Duplicate event IDs are tested as malformed input in evaluator unit tests and are intentionally excluded from the valid golden corpus. Repeated `call_id` values are valid when they connect events from one tool lifecycle.
