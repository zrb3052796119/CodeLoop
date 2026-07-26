# Reflection Value Gate Comparison

The before/after evidence table uses only case IDs shared with the immutable baseline. Value tables additionally separate the original 40 cases, the existing 48-case TaskEvidence set, the 30 claim/value cases, and the complete dataset.

## Shared Cases

- Baseline cases: `48`
- Current shared cases: `48`
- Current full cases: `78`

| Evidence | Before P | Before R | Before F1 | After P | After R | After F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `files_read` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `files_changed` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `tools` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `libraries` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `errors` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `recoveries` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `decisions` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `verification` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## Error Identity

- Duplicate records: `0` -> `0`
- Call-ID association errors: `0` -> `0`
- Merge accuracy: `100.0%` -> `100.0%`
- Evidence-reference errors after: `0`
- Outcome accuracy: `95.8%` -> `95.8%`
- Current outcome mismatch cases: `edge-assistant-only-missing-fields-004, edge-empty-trace-003`

## Full Dataset Evidence

| Evidence | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| `files_read` | 100.0% | 100.0% | 100.0% |
| `files_changed` | 100.0% | 100.0% | 100.0% |
| `tools` | 100.0% | 100.0% | 100.0% |
| `libraries` | 100.0% | 100.0% | 100.0% |
| `errors` | 100.0% | 100.0% | 100.0% |
| `recoveries` | 100.0% | 100.0% | 100.0% |
| `decisions` | 100.0% | 100.0% | 100.0% |
| `verification` | 100.0% | 100.0% | 100.0% |

## Value Selection

| Slice | Cases | Precision | Recall | F1 | Low-value false write |
| --- | ---: | ---: | ---: | ---: | ---: |
| Before original shared | 40 | 55.6% | 90.9% | 69.0% | 88.9% |
| After original shared | 40 | 100.0% | 100.0% | 100.0% | 0.0% |
| Before TaskEvidence shared | 48 | 57.1% | 85.7% | 68.6% | 90.0% |
| After TaskEvidence shared | 48 | 100.0% | 96.4% | 98.2% | 0.0% |
| New claim/value cases | 30 | 100.0% | 100.0% | 100.0% | 0.0% |
| Complete dataset | 78 | 100.0% | 97.5% | 98.7% | 0.0% |

## Claim Safety

- Unsupported claims before -> unsupported accepted after: `134` -> `0`
- Claims without evidence references: `151` -> `0`
- Forbidden accepted claims after: `0`
- Invalid evidence references after: `0`
- Epistemic status mismatches after: `0`
- Confirmed recovery without verification after: `0`
- Confirmed root cause without full chain after: `0`
- Low-value false-write rate: `90.0%` -> `0.0%`

The two known outcome differences remain `edge-empty-trace-003` and `edge-assistant-only-missing-fields-004`: Trace Contract v2 returns `unknown` without terminal or verification evidence. They are excluded from value-gate success criteria.

## Known Defects

- `command_interpreted_as_path`: **FIXED** (`path-command-is-not-file-002`)
- `changing_interpreted_as_gin`: **FIXED** (`library-changing-gin-negative-005`)
- `tool_result_error_duplicate`: **FIXED** (`error-same-call-two-sources-001`)
