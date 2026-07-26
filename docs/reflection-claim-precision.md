# Reflection Claim Precision

- Evaluation: **provider_capture_replay**
- Requested provider cases: **15** (7 positive / 8 negative)
- Claim-level replay cases: **10**; summary-only provider cases: **5**
- Provider-eligible negatives: **8**
- Acceptance gates: **8/15**

| Arm | Exact P/R/F1 | Adjudicated P/R/F1 | Primary recall | Redundant rate | Rule regressions | Gap successes | Calls | Avg input tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| calibrated_verbose+replace | 6.2%/16.7%/9.1% | 37.5%/60.0%/46.2% | 33.3% | 0.0% | 2 | 0 | 10 | 1575.7 |
| calibrated_compact+replace | 0.0%/0.0%/0.0% | 31.2%/50.0%/38.5% | 16.7% | 0.0% | 3 | 0 | 10 | 1296.7 |
| calibrated_compact+gap_fill | 20.0%/50.0%/28.6% | 53.3%/80.0%/64.0% | 66.7% | 0.0% | 0 | 0 | 10 | 1296.7 |

Compact actual input-token reduction: **17.7%**

## Provider A/B (15 Cases)

| Prompt | Parser | Validator exact P/R/F1 | Negative false writes | Avg input | Cache read | Avg/median/P95 latency ms | Cost USD |
|---|---:|---:|---:|---:|---:|---:|---:|
| calibrated_verbose | 93.3% | 0.0%/0.0%/0.0% | 8 | 1573.8 | 17280 | 2720.7/2613.5/3792.3 | 0.005888 |
| calibrated_compact | 100.0% | 0.0%/0.0%/0.0% | 8 | 1294.8 | 11264 | 2792.6/2919.2/3652.0 | 0.004852 |

## Compact LLM Stages (10 Replay Cases)

| Stage | Exact P/R/F1 | Adjudicated P/R/F1 |
|---|---:|---:|
| candidate | 0.0%/0.0%/0.0% | 19.2%/50.0%/27.8% |
| validator | 0.0%/0.0%/0.0% | 31.2%/50.0%/38.5% |
| value | 0.0%/0.0%/0.0% | 31.2%/50.0%/38.5% |
| persistable | 0.0%/0.0%/0.0% | 31.2%/50.0%/38.5% |

## Gates

- PASS: `parser_success_at_least_95_percent`
- PASS: `semantic_key_failure_at_most_5_percent`
- PASS: `provider_negative_samples_at_least_8`
- FAIL: `low_value_false_accept_zero`
- PASS: `invalid_reference_zero`
- PASS: `epistemic_mismatch_zero`
- PASS: `root_cause_overclaim_zero`
- PASS: `gap_fill_rule_regression_zero`
- FAIL: `adjudicated_persistable_precision_at_least_90_percent`
- FAIL: `exact_persistable_precision_at_least_80_percent`
- FAIL: `primary_lesson_recall_at_least_80_percent`
- PASS: `accepted_redundant_rate_at_most_10_percent`
- FAIL: `gap_fill_success_at_least_2`
- FAIL: `gap_fill_false_positive_zero`
- FAIL: `input_token_reduction_at_least_20_percent`
