# Reflection Accuracy Baseline

This report measures the current, unmodified `ReflectionEngine` against synthetic, manually labelled execution traces. Low scores are baseline findings, not test failures.

## Dataset

- Dataset schema version: `1`
- Cases: `40`
- Engine: `minicode.agent_reflection.ReflectionEngine`
- Source policy: synthetic traces only; no real sessions, memory files, credentials, models, or network services.

| Category | Cases |
| --- | ---: |
| `decisions_and_constraints` | 5 |
| `error_deduplication` | 5 |
| `library_detection` | 5 |
| `low_value_tasks` | 5 |
| `multilingual_and_edge_cases` | 5 |
| `path_extraction` | 5 |
| `recovery_and_verification` | 5 |
| `security_and_redaction` | 5 |

## Current Trace Schema

Production trace events currently include `tool_call`, `tool_result`, `error`, `recovery`, `assistant_step`, and terminal `task_result`. Stable linkage is primarily through `call_id`; golden fixtures add deterministic `event_id` values for evidence references.

Current reflection output exposes generic files, libraries, tools, errors, recoveries, project state, decisions, lessons, and confidence. It does not expose file roles, verification records, error call IDs, or claim evidence references.

## Evidence Extraction

| Field | Precision | Recall | F1 | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `files_read` | 64.5% | 100.0% | 78.4% | 11 | 0 |
| `files_changed` | 100.0% | 0.0% | 0.0% | 0 | 8 |
| `tools` | 100.0% | 93.9% | 96.8% | 0 | 3 |
| `libraries` | 0.0% | 0.0% | 0.0% | 3 | 9 |
| `errors` | 72.7% | 100.0% | 84.2% | 6 | 0 |
| `recoveries` | 100.0% | 100.0% | 100.0% | 0 | 0 |
| `decisions` | 62.5% | 50.0% | 55.6% | 3 | 5 |
| `verification` | 100.0% | 0.0% | 0.0% | 0 | 18 |

## Error Deduplication

- Expected logical errors: `16`
- Actual error records: `22`
- Duplicate error records: `7`
- Merge accuracy: `72.7%`
- Missing/incorrect call-ID associations: `15`

## Value Selection

- Should write and wrote: `17`
- Should write but skipped: `5`
- Should not write but wrote: `15`
- Should not write and skipped: `3`
- Low-value false-write rate: `83.3%`

## Claims

- Supported claims: `13`
- Unsupported claims: `98`
- Missing required claims: `7`
- Forbidden claims: `1`
- Claims without evidence references: `111`

## Confidence Calibration

| Confidence | Cases | Correct conclusions | Unsupported claim ratio | Low-value write ratio |
| --- | ---: | ---: | ---: | ---: |
| `[0.0,0.5)` | 8 | 0.0% | 70.6% | 0.0% |
| `[0.5,0.7)` | 24 | 0.0% | 93.7% | 58.3% |
| `[0.7,0.9)` | 7 | 0.0% | 87.5% | 14.3% |
| `[0.9,1.0]` | 1 | 0.0% | 85.7% | 0.0% |

Event-count/confidence Pearson correlation: `0.344`.

High-confidence cases with factual or claim errors:

- `recovery-edit-targeted-pass-001`

Confidence `1.0` cases with unsupported claims:

- `recovery-edit-targeted-pass-001`

## Known Defects

- **REPRODUCED**: command interpreted as a path (`path-command-is-not-file-002`).
- **REPRODUCED**: changing interpreted as gin (`library-changing-gin-negative-005`).
- **REPRODUCED**: tool_result/error produce duplicate error records (`error-same-call-two-sources-001`).

## Capability Gaps

- `file_access_role`
- `dependency_certainty`
- `error_call_id_association`
- `verification_evidence`
- `claim_evidence_references`
- `epistemic_status`

These fields are scored as missing when the golden trace requires them. The evaluator does not reconstruct them from trace data because that would inflate the current engine baseline.

## Next Stage Conditions

1. Keep case IDs and manual labels stable while production extraction changes.
2. Require every new extractor behavior to improve relevant golden metrics without increasing unrelated false positives.
3. Introduce structured `TaskEvidence` only after file roles, error identity, recovery, verification, and evidence-reference semantics are agreed.
4. Treat confidence changes as a separate calibration task after evidence precision is measurable.
5. Continue routing suspicious trace content through the existing memory safety and approval path.

## Reproduce

```bash
python scripts/evaluate_reflection.py \
  --dataset tests/fixtures/reflection_golden \
  --output artifacts/reflection-accuracy-baseline.json \
  --markdown docs/reflection-accuracy-baseline.md
```
