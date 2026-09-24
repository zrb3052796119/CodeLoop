# Memory Retrieval Semantic Gap Baseline

> Frozen synthetic Retrieval Phase 3A evaluation. These values are not production traffic estimates.

## Acceptance

- Evaluation integrity passed: `False`
- Behavior candidate passed: `False`
- Source certification passed: `False`
- Accepted baseline: `False`
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
| Recall@1 | 19.44% | 23.61% | 12.50% | 17/72 |
| Recall@3 | 23.61% | 29.17% | 12.50% | 21/72 |
| Recall@5 | 29.17% | 36.11% | 12.50% | 26/72 |
| Recall@10 | 37.50% | 41.67% | 12.50% | 30/72 |
| Recall@20 | 45.83% | 48.61% | 20.83% | 35/72 |
| MRR@20 | 0.2428 | 0.2895 | 0.1313 | - |
| NDCG@5 | 0.2422 | 0.2980 | 0.1250 | - |

## Downstream And Negative Metrics

- Post-Gate recall: `19.44%`.
- Post-Consolidation recall: `19.44%`.
- Rendered recall / precision: `19.44%` / `26.42%`.
- Hard-negative candidate / post-Gate / rendered rates: `75.00%` / `61.11%` / `61.11%`.
- Forbidden candidate leakage entries: `0`.

## First Loss

- `candidate_generation_top20`: 37
- `relevance_gate`: 22
- `rendered`: 13

## Decision

- Confirmed semantic gaps: `36` overall, `18` sealed.
- Phase 3B entry gate: `False` (`inconclusive_expand_independent_holdout`).
- Direct production hybrid enablement: `False`.

## Statistical Scope

The Wilson interval in the artifact applies only to this deliberately adversarial synthetic fixture. It does not estimate the frequency of semantic misses in real user traffic and supports no population-level significance claim.
