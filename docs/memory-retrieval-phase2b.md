# Memory Retrieval Phase 2B

> Deterministic post-gate consolidation on the frozen 80-case suite and a 33-case independent holdout.

## Acceptance

- Deterministic acceptance: `True`
- Frozen 80-case gates: `True`
- Holdout gates: `True`
- Deterministic performance invariants: `True`
- Integrity gates: `True`
- Wall-clock enforcement mode: `advisory`
- Strict wall-clock result: `False`
- Remote calls: `0`

## Frozen 80 Cases

| Metric | Phase 2A | Phase 2B |
|---|---:|---:|
| Precision@1 | 0.8625 | 0.8750 |
| Recall@5 | 0.9514 | 0.9514 |
| Primary hit | 0.9859 | 0.9859 |
| Rendered precision | 0.8979 | 0.9812 |
| Must-exclude rate | 0.2125 | 0.0375 |

## Phase 2B Holdout

- `retrieval_candidate_recall`: `1.0000`
- `post_gate_recall`: `1.0000`
- `post_consolidation_precision`: `1.0000`
- `post_consolidation_recall`: `1.0000`
- `rendered_precision`: `1.0000`
- `rendered_recall`: `0.9231`
- `incorrect_suppression_rate`: `0.0000`
- `complementary_secondary_retention_rate`: `1.0000`
- `reason_code_accuracy`: `1.0000`

## Limits

- The 80-case and 33-case datasets are synthetic regressions, not a production distribution.
- Conflict and duplicate decisions require deterministic lexical or structured evidence; semantic-only equivalence remains out of scope.
- No embedding, vector database, LLM, reranker provider, query rewrite, or remote service is used.
- The consolidator cap is 256 post-gate candidates; overflow fails closed with candidate_limit diagnostics.
- Latency and peak memory are environment-sensitive and excluded from deterministic equality.
- Wall-clock observations are advisory unless strict enforcement is selected explicitly.
