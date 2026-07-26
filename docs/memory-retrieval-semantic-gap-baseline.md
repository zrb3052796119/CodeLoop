# Memory Retrieval Semantic Gap Baseline

> Frozen synthetic Retrieval Phase 3A evaluation. These values are not production traffic estimates.

## Acceptance

- Evaluation integrity passed: `False`
- Production code modified by Phase 3A: `False`
- Remote calls: `0`
- Dataset: `108` cases (`72` positive, `36` hard negative)
- Freeze manifest SHA-256: `59638f40dc76df881c63804275eda5cf137679b77b72916694635b5c51ac9f8b`

## Retrieval Arms

1. Manager global search: MemoryManager.search across USER/PROJECT/LOCAL, limit=50, min_relevance=0, record_usage=False.
2. Canonical diagnostic: Canonical retriever, low pressure, min_relevance=0, 20-memory/8000-token render budget; candidate ranks observed through top 50 without counters.
3. Canonical production: MemoryPipeline.inject with current defaults (5 memories, 200 tokens each, min relevance 0.3), followed by rendered-only success feedback in an isolated HOME.

## Candidate Metrics

| Metric | Manager | Canonical diagnostic | Sealed diagnostic | Raw diagnostic count |
|---|---:|---:|---:|---:|
| Recall@1 | 19.44% | 22.22% | 12.50% | 16/72 |
| Recall@3 | 22.22% | 27.78% | 12.50% | 20/72 |
| Recall@5 | 23.61% | 29.17% | 12.50% | 21/72 |
| Recall@10 | 34.72% | 36.11% | 12.50% | 26/72 |
| Recall@20 | 44.44% | 47.22% | 20.83% | 34/72 |
| MRR@20 | 0.2328 | 0.2685 | 0.1313 | - |
| NDCG@5 | 0.2180 | 0.2608 | 0.1250 | - |

## Downstream And Negative Metrics

- Post-Gate recall: `16.67%`.
- Post-Consolidation recall: `16.67%`.
- Rendered recall / precision: `16.67%` / `24.49%`.
- Hard-negative candidate / post-Gate / rendered rates: `75.00%` / `55.56%` / `55.56%`.
- Forbidden candidate leakage entries: `0`.

## First Loss

- `candidate_generation_top20`: 38
- `relevance_gate`: 23
- `rendered`: 11

## Decision

- Confirmed semantic gaps: `37` overall, `18` sealed.
- Phase 3B entry gate: `False` (`inconclusive_expand_independent_holdout`).
- Direct production hybrid enablement: `False`.

## Statistical Scope

The Wilson interval in the artifact applies only to this deliberately adversarial synthetic fixture. It does not estimate the frequency of semantic misses in real user traffic and supports no population-level significance claim.
