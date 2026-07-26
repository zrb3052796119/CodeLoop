# Memory Retrieval Phase 2A

> Offline deterministic evaluation on the frozen 80-case synthetic fixture.

## Acceptance

- Correctness gates: `True`
- Quality gates: `True`
- Performance gates: `True`
- Remote calls: `0`
- Protected files unchanged: `True`
- Phase 1 baseline unchanged: `True`

## Five Arms

| Arm | P@1 | R@5 | Primary hit | Rendered precision | Exclude rate | Negative false | Avg saves | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| manager_global_search | 0.6750 | 0.9514 | 0.9577 | n/a | 0.4875 | 0.8889 | 1.7125 | 1.3786 |
| manager_context_query | 0.8625 | 0.9514 | 0.9859 | 0.8979 | 0.2125 | 0.0000 | 1.3125 | 1.6229 |
| pipeline_read | 0.8625 | 0.9514 | 0.9859 | 0.8979 | 0.2125 | 0.0000 | 1.3125 | 1.5397 |
| pipeline_inject | 0.8625 | 0.9514 | 0.9859 | 0.8979 | 0.2125 | 0.0000 | 2.5875 | 2.2013 |
| canonical_retrieval | 0.8625 | 0.9514 | 0.9859 | 0.8979 | 0.2125 | 0.0000 | 2.5875 | 2.1233 |

## Identity And Ownership

- Unified Top-1 agreement: `1.0000`.
- `MemoryPipeline.inject` is the only production persistent-memory prompt owner.
- Recorded injection IDs and outcome feedback IDs are derived from the saved rendered IDs.
- Reranker summaries are disabled and cannot enter the prompt.

## I/O And Budget

- Pipeline task-start saves: `1.3125` average scopes.
- Pipeline full-turn saves: `2.5875` average scopes.
- Max-memory violations: `0`.
- Token-budget violations: `0`.

## Limits

- The 80 cases are fixed synthetic regressions, not a production-user distribution.
- No embedding, vector database, query rewrite, LLM filter, reranker, or remote provider is used.
- Manager global search remains a low-level compatibility arm; the four query-aware production entrypoints are the unified agreement set.
- Latency is environment-sensitive and excluded from deterministic comparisons.
- Hard context pressure intentionally suppresses injection even when a labelled memory is relevant.
