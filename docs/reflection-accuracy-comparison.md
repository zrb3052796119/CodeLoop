# Reflection Accuracy Comparison

The before/after table uses only case IDs shared with the immutable baseline. The full table additionally includes Trace Contract v2 cases.

## Shared Cases

- Baseline cases: `40`
- Current shared cases: `40`
- Current full cases: `48`

| Evidence | Before P | Before R | Before F1 | After P | After R | After F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `files_read` | 64.5% | 100.0% | 78.4% | 100.0% | 100.0% | 100.0% |
| `files_changed` | 100.0% | 0.0% | 0.0% | 100.0% | 100.0% | 100.0% |
| `tools` | 100.0% | 93.9% | 96.8% | 100.0% | 100.0% | 100.0% |
| `libraries` | 0.0% | 0.0% | 0.0% | 100.0% | 100.0% | 100.0% |
| `errors` | 72.7% | 100.0% | 84.2% | 100.0% | 100.0% | 100.0% |
| `recoveries` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `decisions` | 62.5% | 50.0% | 55.6% | 100.0% | 100.0% | 100.0% |
| `verification` | 100.0% | 0.0% | 0.0% | 100.0% | 100.0% | 100.0% |

## Error Identity

- Duplicate records: `7` -> `0`
- Call-ID association errors: `15` -> `0`
- Merge accuracy: `72.7%` -> `100.0%`
- Evidence-reference errors after: `0`
- Outcome accuracy: `97.5%` -> `95.0%`
- Current outcome mismatch cases: `edge-assistant-only-missing-fields-004, edge-empty-trace-003`

## Full V2 Dataset

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

## Unchanged-Stage Observations

Claim synthesis, value selection, and confidence were not acceptance targets in this stage.

- Unsupported claims: `98` -> `104`
- Claims without evidence references: `111` -> `121`
- Low-value false-write rate: `83.3%` -> `88.9%`

## Known Defects

- `command_interpreted_as_path`: **FIXED** (`path-command-is-not-file-002`)
- `changing_interpreted_as_gin`: **FIXED** (`library-changing-gin-negative-005`)
- `tool_result_error_duplicate`: **FIXED** (`error-same-call-two-sources-001`)
