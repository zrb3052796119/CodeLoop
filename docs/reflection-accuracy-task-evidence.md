# Reflection Accuracy - TaskEvidence

This report measures the evidence-linked `ReflectionEngine` against synthetic, manually labelled execution traces. Evidence, synthesis/claims, and value/confidence remain separate metric groups.

## Dataset

- Dataset schema version: `1`
- Cases: `48`
- Engine: `minicode.agent_reflection.ReflectionEngine`
- Source policy: synthetic traces only; no real sessions, memory files, credentials, models, or network services.

| Category | Cases |
| --- | ---: |
| `decisions_and_constraints` | 5 |
| `error_deduplication` | 8 |
| `library_detection` | 5 |
| `low_value_tasks` | 5 |
| `multilingual_and_edge_cases` | 7 |
| `path_extraction` | 6 |
| `recovery_and_verification` | 7 |
| `security_and_redaction` | 5 |

## Current Trace Schema

Production Trace Contract v2 includes deterministic `event_id`, `call_id`, role-specific files, `recovery_suggestion`, real `recovery`, and terminal outcome fields. Legacy traces receive extraction-local fallback IDs without mutation.

`TaskEvidence` exposes file roles, verification records, error call IDs, evidence references, dependency strength, and epistemic status. Claim-level references remain a later synthesis-stage capability.

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

- Expected logical errors: `23`
- Actual error records: `23`
- Duplicate error records: `0`
- Merge accuracy: `100.0%`
- Missing/incorrect call-ID associations: `0`
- Evidence records without source IDs: `0`
- Outcome accuracy: `95.8%`
- Outcome mismatch cases: `edge-assistant-only-missing-fields-004, edge-empty-trace-003`

## Value Selection

- Should write and wrote: `24`
- Should write but skipped: `4`
- Should not write but wrote: `18`
- Should not write and skipped: `2`
- Low-value false-write rate: `90.0%`

## Claims

- Supported claims: `17`
- Unsupported claims: `134`
- Missing required claims: `3`
- Forbidden claims: `1`
- Claims without evidence references: `151`

## Confidence Calibration

| Confidence | Cases | Correct conclusions | Unsupported claim ratio | Low-value write ratio |
| --- | ---: | ---: | ---: | ---: |
| `[0.0,0.5)` | 6 | 0.0% | 88.9% | 0.0% |
| `[0.5,0.7)` | 32 | 0.0% | 91.2% | 50.0% |
| `[0.7,0.9)` | 4 | 0.0% | 88.9% | 50.0% |
| `[0.9,1.0]` | 6 | 0.0% | 81.8% | 0.0% |

Event-count/confidence Pearson correlation: `0.328`.

High-confidence cases with factual or claim errors:

- `decision-user-correction-old-memory-disproved-005`
- `edge-chinese-error-recovery-001`
- `recovery-edit-targeted-pass-001`
- `recovery-multiple-verification-levels-005`
- `recovery-switch-tool-workaround-003`
- `trace-v2-recovered-final-success-004`

Confidence `1.0` cases with unsupported claims:

- `decision-user-correction-old-memory-disproved-005`
- `edge-chinese-error-recovery-001`
- `recovery-edit-targeted-pass-001`
- `recovery-multiple-verification-levels-005`
- `recovery-switch-tool-workaround-003`

## Known Defects

- **NOT REPRODUCED**: command interpreted as a path (`path-command-is-not-file-002`).
- **NOT REPRODUCED**: changing interpreted as gin (`library-changing-gin-negative-005`).
- **NOT REPRODUCED**: tool_result/error produce duplicate error records (`error-same-call-two-sources-001`).

## Capability Gaps

- `claim_epistemic_status`
- `claim_evidence_references`

These remaining gaps belong to claim synthesis rather than deterministic evidence extraction.

## Next Stage Conditions

1. Keep the original 40 case IDs and manual labels stable as the shared comparison set.
2. Add `ReflectionValueGate` only after low-value policy labels are reviewed independently.
3. Add claim-level evidence references and validation before changing confidence calibration.
4. Continue routing suspicious trace content through the existing memory safety and approval path.

## Reproduce

```bash
python scripts/evaluate_reflection.py \
  --dataset tests/fixtures/reflection_golden \
  --output artifacts/reflection-accuracy-task-evidence.json \
  --markdown docs/reflection-accuracy-task-evidence.md \
  --baseline artifacts/reflection-accuracy-baseline.json \
  --comparison docs/reflection-accuracy-comparison.md
```
