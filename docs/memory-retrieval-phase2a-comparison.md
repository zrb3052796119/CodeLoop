# Memory Retrieval Phase 1 vs Phase 2A

| Metric | Phase 1 Pipeline Inject | Phase 2A Pipeline Inject | Absolute delta |
|---|---:|---:|---:|
| precision_at_1 | 0.7500 | 0.8625 | 0.1125 |
| recall_at_5 | 0.9561 | 0.9514 | -0.0047 |
| primary_hit_rate | 0.9718 | 0.9859 | 0.0141 |
| actual_rendered_precision | 0.7312 | 0.8979 | 0.1667 |
| must_exclude_violation_rate | 0.4750 | 0.2125 | -0.2625 |
| negative_false_injection_rate | 0.8889 | 0.0000 | -0.8889 |
| returned_rendered_disagreement_rate | 0.0500 | 0.0000 | -0.0500 |
| rendered_recorded_disagreement_rate | 0.0500 | 0.0000 | -0.0500 |

Phase 2A raises precision while preserving the required R@5 floor. The remaining recall loss is concentrated in hard count/token/context-pressure cases; those limits are now enforced instead of bypassed.

Must-exclude violations fell from `0.4750` to `0.2125`; negative false injection fell from `0.8889` to `0.0000`.
