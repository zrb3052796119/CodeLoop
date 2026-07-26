# Retrieval Phase 3B Ablation

All arms use frozen synthetic data. Arms B-E intentionally expose the consequence of candidate generation without a selective Gate; their rendered precision is diagnostic, not a production proposal.

## Phase 3A Sealed

| Arm | R@1 | R@3 | R@5 | R@10 | R@20 | MRR@20 | NDCG@5 | Gate recall | Render recall | Precision | HN render |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A Frozen lexical | 12.50% | 12.50% | 12.50% | 12.50% | 20.83% | 0.1313 | 0.1250 | 8.33% | 8.33% | 20.00% | 41.67% |
| B Dense-only | 54.17% | 87.50% | 91.67% | 95.83% | 100.00% | 0.6960 | 0.7463 | 100.00% | 91.67% | 12.22% | 75.00% |
| C Union | 8.33% | 62.50% | 79.17% | 91.67% | 95.83% | 0.4085 | 0.4896 | 95.83% | 79.17% | 10.56% | 75.00% |
| D RRF | 12.50% | 12.50% | 20.83% | 20.83% | 91.67% | 0.1944 | 0.1572 | 91.67% | 20.83% | 2.78% | 66.67% |
| E Weighted | 29.17% | 58.33% | 75.00% | 91.67% | 95.83% | 0.4822 | 0.5274 | 95.83% | 75.00% | 10.00% | 75.00% |
| F Selected + current Gate | 12.50% | 12.50% | 20.83% | 20.83% | 91.67% | 0.1944 | 0.1572 | 8.33% | 8.33% | 20.00% | 41.67% |
| G Selected + semantic Gate | 12.50% | 12.50% | 20.83% | 20.83% | 91.67% | 0.1944 | 0.1572 | 4.17% | 4.17% | 3.57% | 58.33% |

## Phase 3B Independent Holdout

| Arm | R@1 | R@3 | R@5 | R@10 | R@20 | MRR@20 | NDCG@5 | Gate recall | Render recall | Precision | HN render |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A Frozen lexical | 33.33% | 44.44% | 50.00% | 50.00% | 52.78% | 0.3891 | 0.4152 | 30.56% | 30.56% | 34.38% | 62.50% |
| B Dense-only | 69.44% | 77.78% | 83.33% | 91.67% | 91.67% | 0.7612 | 0.7697 | 91.67% | 83.33% | 10.00% | 79.17% |
| C Union | 33.33% | 69.44% | 77.78% | 83.33% | 91.67% | 0.5486 | 0.5971 | 91.67% | 77.78% | 9.33% | 75.00% |
| D RRF | 50.00% | 52.78% | 55.56% | 80.56% | 91.67% | 0.5629 | 0.5295 | 91.67% | 55.56% | 6.67% | 75.00% |
| E Weighted | 52.78% | 72.22% | 77.78% | 88.89% | 88.89% | 0.6441 | 0.6671 | 88.89% | 77.78% | 9.33% | 79.17% |
| F Selected + current Gate | 50.00% | 52.78% | 55.56% | 80.56% | 91.67% | 0.5629 | 0.5295 | 30.56% | 30.56% | 34.38% | 62.50% |
| G Selected + semantic Gate | 50.00% | 52.78% | 55.56% | 80.56% | 91.67% | 0.5629 | 0.5295 | 19.44% | 19.44% | 17.50% | 54.17% |

## Findings

1. Dense-only is the strongest candidate generator: sealed Recall@20 is 100%, holdout is 91.67%.
2. RRF with `rrf_k=20` overweights the weak lexical rank near the head. Its sealed R@1-R@10 is materially below dense-only and weighted fusion.
3. Reusing the current lexical Gate destroys the hybrid gain: Arm F render recall returns to the lexical baseline on both decision sets.
4. The calibrated Semantic Gate reduces some analysis noise but generalizes poorly. It removes 21 sealed and 26 holdout correct candidates while still rendering 7 and 13 labeled hard negatives.
5. CandidateConsolidator causes zero incorrect primary suppression. Controller and hard budget add no primary loss in the final arm.

The complete 2,880-configuration analysis grid is stored in the frozen config artifact; no sealed or holdout result was used to select it.
