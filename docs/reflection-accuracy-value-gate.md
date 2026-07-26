# Reflection Accuracy - ReflectionValueGate

This report measures deterministic evidence extraction, claim validation, and durable-value selection against synthetic, manually labelled execution traces. Confidence remains observational and is not a persistence decision.

## Dataset

- Dataset schema version: `1`
- Cases: `78`
- Engine: `minicode.agent_reflection.ReflectionEngine`
- Source policy: synthetic traces only; no real sessions, memory files, credentials, models, or network services.

| Category | Cases |
| --- | ---: |
| `decisions_and_constraints` | 13 |
| `error_deduplication` | 10 |
| `library_detection` | 7 |
| `low_value_tasks` | 12 |
| `multilingual_and_edge_cases` | 8 |
| `path_extraction` | 6 |
| `recovery_and_verification` | 12 |
| `security_and_redaction` | 10 |

## Dataset Slices

| Slice | Cases | Value P | Value R | Value F1 | Low-value false write | Unsupported accepted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `original_shared_40` | 40 | 100.0% | 100.0% | 100.0% | 0.0% | 0 |
| `task_evidence_48` | 48 | 100.0% | 96.4% | 98.2% | 0.0% | 0 |
| `claim_value_30` | 30 | 100.0% | 100.0% | 100.0% | 0.0% | 0 |
| `full` | 78 | 100.0% | 97.5% | 98.7% | 0.0% | 0 |

## Current Trace Schema

Production Trace Contract v2 includes deterministic `event_id`, `call_id`, role-specific files, `recovery_suggestion`, real `recovery`, and terminal outcome fields. Legacy traces receive extraction-local fallback IDs without mutation.

`TaskEvidence` exposes file roles, verification records, error call IDs, evidence references, dependency strength, and epistemic status. Structured claims are synthesized only from this evidence and validated before value selection.

## Evidence Extraction

| Field | Precision | Recall | F1 | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `files_read` | 100.0% | 100.0% | 100.0% | 0 | 0 |
| `files_changed` | 100.0% | 100.0% | 100.0% | 0 | 0 |
| `tools` | 100.0% | 100.0% | 100.0% | 0 | 0 |
| `libraries` | 100.0% | 100.0% | 100.0% | 0 | 0 |
| `errors` | 100.0% | 100.0% | 100.0% | 0 | 0 |
| `recoveries` | 100.0% | 100.0% | 100.0% | 0 | 0 |
| `decisions` | 100.0% | 100.0% | 100.0% | 0 | 0 |
| `verification` | 100.0% | 100.0% | 100.0% | 0 | 0 |

## Error Deduplication

- Expected logical errors: `29`
- Actual error records: `29`
- Duplicate error records: `0`
- Merge accuracy: `100.0%`
- Missing/incorrect call-ID associations: `0`
- Evidence records without source IDs: `0`
- Outcome accuracy: `97.4%`
- Outcome mismatch cases: `edge-assistant-only-missing-fields-004, edge-empty-trace-003`

## Value Selection

- Should write and accepted: `39`
- Should write but rejected: `1`
- Should not write but accepted: `0`
- Should not write and rejected: `38`
- Value precision: `100.0%`
- Value recall: `97.5%`
- Value F1: `98.7%`
- Low-value false-write rate: `0.0%`
- Reason codes: `{"accepted_durable_reflection": 39, "generic_error_summary": 2, "no_durable_signal": 7, "no_valid_claim": 37, "recovery_suggestion_only": 1, "routine_directory_listing": 2, "routine_format_only": 3, "routine_read_only": 7, "routine_search_only": 3, "routine_verification_only": 6, "task_success_only": 24, "tool_count_only": 24, "unknown_outcome_without_durable_fact": 4, "unsupported_root_cause": 1, "weak_dependency_mention": 1}`
- Durable signals: `{"confirmed_dependency": 5, "confirmed_error_recovery_verified": 8, "key_technical_decision": 6, "old_memory_disproved": 2, "reusable_error_pattern": 12, "stable_project_constraint": 7, "user_correction": 3, "verified_solution": 8}`

## Claims

- Generated claims: `58`
- Validator-valid claims: `48`
- Validator-rejected claims: `7`
- Persistable claims: `45`
- Supported accepted claims: `45`
- Unsupported accepted claims: `0`
- Missing required claims: `7`
- Forbidden accepted claims: `0`
- Claims without evidence references: `0`
- Invalid evidence references: `0`
- Epistemic status mismatches: `0`
- Missing applies_when: `0`
- Missing limitations: `0`
- Duplicate semantic keys: `0`
- Confirmed recovery without verification: `0`
- Confirmed root cause without full chain: `0`
- Generic success/tool-count claims: `0`

## Confidence Calibration

| Confidence | Cases | Correct conclusions | Unsupported claim ratio | Low-value write ratio |
| --- | ---: | ---: | ---: | ---: |
| `[0.0,0.5)` | 22 | 90.9% | 0.0% | 0.0% |
| `[0.5,0.7)` | 42 | 90.5% | 0.0% | 0.0% |
| `[0.7,0.9)` | 6 | 83.3% | 0.0% | 0.0% |
| `[0.9,1.0]` | 8 | 87.5% | 0.0% | 0.0% |

Event-count/confidence Pearson correlation: `0.476`.

High-confidence cases with factual or claim errors:

- None

Confidence `1.0` cases with unsupported claims:

- None

## Known Defects

- **NOT REPRODUCED**: command interpreted as a path (`path-command-is-not-file-002`).
- **NOT REPRODUCED**: changing interpreted as gin (`library-changing-gin-negative-005`).
- **NOT REPRODUCED**: tool_result/error produce duplicate error records (`error-same-call-two-sources-001`).

## Capability Gaps

- None

The deterministic claim/value stage reports no capability gap when structured results are available.

## Known Outcome Semantics

The legacy labels for `edge-empty-trace-003` and `edge-assistant-only-missing-fields-004` differ from Trace Contract v2. V2 returns `unknown` without terminal or verification evidence; these two differences do not affect value-gate acceptance.

## Reproduce

```bash
python scripts/evaluate_reflection.py \
  --dataset tests/fixtures/reflection_golden \
  --output artifacts/reflection-accuracy-value-gate.json \
  --markdown docs/reflection-accuracy-value-gate.md \
  --baseline artifacts/reflection-accuracy-task-evidence.json \
  --comparison docs/reflection-value-gate-comparison.md
```
