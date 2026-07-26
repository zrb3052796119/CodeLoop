# Memory Retrieval Phase 2A vs Phase 2B

| Metric | Phase 2A | Phase 2B | Delta |
|---|---:|---:|---:|
| Precision@1 | 0.8625 | 0.8750 | 0.0125 |
| Recall@5 | 0.9514 | 0.9514 | 0.0000 |
| Primary hit | 0.9859 | 0.9859 | 0.0000 |
| Rendered precision | 0.8979 | 0.9812 | 0.0833 |
| Must-exclude violation | 0.2125 | 0.0375 | -0.1750 |

Phase 2B changes only the post-gate candidate set. Retrieval candidate generation, relevance gating, controller policy, hard budgets, counters, and rendered-only feedback remain the existing Phase 2A boundaries.
