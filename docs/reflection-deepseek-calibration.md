# DeepSeek Reflection Schema & Value Calibration

Quality metrics below come from captured synthetic responses replayed through the same final deterministic gates.

| Metric | Baseline | Calibrated |
|---|---:|---:|
| Parser success | 50.0% | 100.0% |
| Semantic-key failures | 4 | 0 |
| Validator precision | 0.0% | 33.3% |
| Validator recall | 0.0% | 50.0% |
| All claims rejected | 3 | 0 |
| Value precision | 100.0% | 100.0% |
| Value recall | 0.0% | 100.0% |
| Low-value false writes | 0 | 0 |
| Invalid references | 0 | 0 |
| Epistemic mismatches | 0 | 0 |
| Candidate root-cause overclaim | 1 | 0 |
| Final root-cause overclaim | 0 | 0 |

## Provider Budget

- Calls baseline/intermediate/final: **8 / 8 / 8**
- Total calls / limit: **24 / 30**
- Estimated total cost: **$0.007355**
- Average input tokens baseline -> calibrated: **913.9 -> 1487.9**
- Average input-token delta: **+574.0 (62.8%)**

| Provider metric | Baseline | Calibrated |
|---|---:|---:|
| Calls | 8 | 8 |
| Input tokens | 7311 | 11903 |
| Output tokens | 2190 | 2208 |
| Cache-read tokens | 6272 | 7296 |
| Latency avg/median/P95 ms | 2456.7/2287.1/3897.3 | 2652.1/2710.4/3210.2 |
| Estimated cost USD | 0.002076 | 0.002795 |

## Adjudicated Metric

- Exact expected-case recall: **50.0%**
- Adjudicated expected-case recall: **66.7%**
- Legal synonym/split-claim case: `holdout-project-constraint-005`; exact claim metrics above remain unchanged.
- Label-policy disagreements retained: `holdout-unverified-recovery-024`, `holdout-partial-recovery-008`.

## Structural Distributions

- Baseline -> calibrated claim types: `{"decision": 1, "error_pattern": 3, "recovery": 2, "root_cause": 1, "verification_rule": 1}` -> `{"constraint": 1, "decision": 2, "error_pattern": 6, "recovery": 4, "verification_rule": 3}`
- Baseline -> calibrated epistemic statuses: `{"confirmed": 7, "inferred": 1}` -> `{"confirmed": 13, "inferred": 3}`
- Calibrated Validator issues: `{"claim_statement_not_grounded": 7, "claim_type_evidence_mismatch": 7, "verification_rule_not_stable": 3}`
- Calibrated durable signals: `{"key_technical_decision": 2, "reusable_error_pattern": 4, "stable_project_constraint": 1}`
- Rule-only / LLM-only correct cases: `["holdout-timeout-fallback-032", "holdout-verified-recovery-007"]` / `[]`

## Expansion Gate

- Result: **do_not_expand_shadow**
- `parser_success_at_least_95_percent`: **PASS**
- `semantic_key_failure_at_most_5_percent`: **PASS**
- `invalid_evidence_references_zero`: **PASS**
- `epistemic_mismatches_zero`: **PASS**
- `final_root_cause_overclaim_zero`: **PASS**
- `low_value_false_writes_zero`: **PASS**
- `negative_real_samples_at_least_8`: **FAIL**
- `validator_precision_at_least_80_percent`: **FAIL**

## Local Performance

- Valid/invalid parse: **0.0332 / 0.0059 ms/op**
- Replay Parser+Validator+ValueGate: **0.2679 ms/op**
- Prompt/schema growth: **+2145 chars / +833 bytes**
- Network calls: **0**

## Case Comparison

| Case | Baseline parser | Calibrated parser | Valid/rejected A -> B | Value A -> B | TP/FP/FN B |
|---|---|---|---:|---:|---:|
| `holdout-causal-trap-025` | ok | ok | 0/2 -> 1/1 | False -> True | 1/0/0 |
| `holdout-partial-recovery-008` | ok | ok | 1/1 -> 1/1 | False -> False | 0/1/0 |
| `holdout-project-constraint-005` | invalid_semantic_key | ok | 0/0 -> 2/0 | None -> True | 0/2/1 |
| `holdout-provider-fallback-033` | ok | ok | 0/1 -> 1/0 | False -> True | 1/0/0 |
| `holdout-redacted-secret-error-028` | invalid_semantic_key | ok | 0/0 -> 1/1 | None -> True | 1/0/0 |
| `holdout-timeout-fallback-032` | ok | ok | 0/3 -> 1/1 | False -> True | 0/1/1 |
| `holdout-unverified-recovery-024` | invalid_semantic_key | ok | 0/0 -> 1/1 | None -> False | 0/1/0 |
| `holdout-verified-recovery-007` | invalid_semantic_key | ok | 0/0 -> 1/2 | None -> True | 0/1/1 |

## Limits

- Only eight selected cases produced provider responses per arm; two security cases were rejected before provider invocation.
- Only two negative cases reached the provider in the final arm, below the required eight.
- Validator precision remains below the expansion threshold even though case-level write decisions improved.
- Synthetic holdout evidence does not establish production-distribution quality.
