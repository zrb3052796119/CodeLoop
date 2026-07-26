# Memory Retrieval Phase 1 Baseline

> Scope: fixed, fully synthetic cases. This is an offline diagnostic baseline, not a production-accuracy claim.

## Dataset

- Cases: 80
- Synthetic data: `true`
- Remote calls: 0
- Production files unchanged: `true`
- Formal memory touched during evaluator execution: `false`

## Core Metrics

| Arm | P@1 | P@3 | P@5 | R@5 | MRR | nDCG@5 | Exclude rate | Negative false rate | Max-count rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| manager_global_search | 0.6750 | 0.3625 | 0.2225 | 0.9655 | 0.7688 | 0.8784 | 0.4875 | 0.8889 | 0.0000 |
| manager_context_query | 0.6000 | 0.3542 | 0.2175 | 0.9326 | 0.7188 | 0.8181 | 0.4875 | 0.8889 | 0.0250 |
| pipeline_read | 0.6750 | 0.3667 | 0.2250 | 0.9725 | 0.7688 | 0.8808 | 0.4875 | 0.8889 | 0.0000 |
| pipeline_inject | 0.7500 | 0.3625 | 0.2225 | 0.9561 | 0.8063 | 0.8903 | 0.4750 | 0.8889 | 0.0250 |

## Per-Category P@1 / MRR

| Category | Global P@1 / MRR | Context P@1 / MRR | Read P@1 / MRR | Inject P@1 / MRR |
|---|---:|---:|---:|---:|
| exact_lexical | 1.0000 / 1.0000 | 0.7500 / 0.8750 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| paraphrase_synonym | 0.8750 / 0.8750 | 0.8750 / 0.8750 | 0.8750 / 0.8750 | 0.8750 / 0.8750 |
| multilingual | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| file_domain_context | 1.0000 / 1.0000 | 0.7500 / 0.8750 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| cross_scope_ranking | 0.0000 / 0.4375 | 0.0000 / 0.5000 | 0.0000 / 0.4375 | 0.6250 / 0.8125 |
| lifecycle_safety | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| negative_no_match | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 | 0.0000 / 0.0000 |
| duplicate_conflict_budget | 0.5000 / 0.7500 | 0.5000 / 0.6250 | 0.5000 / 0.7500 | 0.6250 / 0.8125 |
| failure_recovery_correction | 0.7500 / 0.8750 | 0.6250 / 0.8125 | 0.7500 / 0.8750 | 0.8750 / 0.9375 |
| entrypoint_consistency | 0.6250 / 0.7500 | 0.5000 / 0.6250 | 0.6250 / 0.7500 | 0.5000 / 0.6250 |

## Rendering And Attribution

| Arm | Rendered precision | Feedback precision | Returned/rendered disagreements | Rendered/recorded disagreements | Avg memory tokens | Avg saves |
|---|---:|---:|---:|---:|---:|---:|
| manager_global_search | unavailable | unavailable | 0 | 0 | unavailable | 1.7125 |
| manager_context_query | 0.7000 | unavailable | 0 | 0 | 81.2750 | 1.7125 |
| pipeline_read | unavailable | unavailable | 0 | 0 | unavailable | 3.4500 |
| pipeline_inject | 0.7312 | 0.9638 | 4 | 4 | 67.6125 | 5.0375 |

Pipeline injection recorded 4 returned/rendered and 4 rendered/recorded disagreements. The four ID views are retained separately in every per-case result.

## Confirmed Diagnostics

- `global-vs-inject-ordering`: confirmed by the recorded synthetic reproduction.
- `max-memories-one`: confirmed by the recorded synthetic reproduction.
- `format-first-five-attribution`: confirmed by the recorded synthetic reproduction.
- `tui-double-injection`: confirmed by the recorded synthetic reproduction.
- `local-budget-before-project`: confirmed by the recorded synthetic reproduction.
- `missing-current-files-domains`: confirmed by the recorded synthetic reproduction.
- `vector-only-fusion`: confirmed by the recorded synthetic reproduction.
- `related-graph-semantics`: confirmed by the recorded synthetic reproduction.
- `recovered-failure-feedback`: confirmed by the recorded synthetic reproduction.
- `reranker-summary-boundary`: confirmed by the recorded synthetic reproduction.
- `headless-no-query-unrelated`: confirmed by the recorded synthetic reproduction.
- `repeated-query-counters-io`: confirmed by the recorded synthetic reproduction.

## Highest-Severity Findings

1. **P1 - Injection identity is not truthful above five candidates.** The Injector records all returned IDs, Pipeline renders only five, and task feedback rewards or penalizes the full returned list.
2. **P1 - Production entrypoints do not share retrieval semantics.** Query-aware manager context is scope-sequential, Injector discards BM25/global ordering during its re-score, and Pipeline.read is bypassed.
3. **P1 - No-query paths inject unrelated active memory.** The headless reproduction emitted active entries for a no-match task; compaction calls the same no-query branch, and TUI/stdin can inject an entry twice through two managers.

Additional P2 observations: `max_memories` is not a final Injector cap; vector-only IDs cannot survive current RRF; current files/domains are empty on the real agent injection call; recovered tool errors receive negative feedback; and retrieval causes repeated persistent saves.

## Failed-Case Examples

- `manager_global_search`: primary misses `mr-budget-05, mr-para-06`; exclude violations `mr-budget-01, mr-budget-02, mr-budget-03, mr-budget-04, mr-budget-08, mr-domain-02`.
- `manager_context_query`: primary misses `mr-budget-04, mr-budget-07, mr-entry-06, mr-para-06`; exclude violations `mr-budget-01, mr-budget-02, mr-budget-03, mr-budget-04, mr-budget-08, mr-domain-02`.
- `pipeline_read`: primary misses `mr-budget-05, mr-para-06`; exclude violations `mr-budget-01, mr-budget-02, mr-budget-03, mr-budget-04, mr-budget-08, mr-domain-02`.
- `pipeline_inject`: primary misses `mr-entry-08, mr-para-06`; exclude violations `mr-budget-01, mr-budget-02, mr-budget-03, mr-budget-04, mr-budget-08, mr-domain-02`.

## Latency

| Arm | p50 ms | p95 ms |
|---|---:|---:|
| manager_global_search | 0.8773 | 1.5568 |
| manager_context_query | 0.8892 | 1.5197 |
| pipeline_read | 1.3458 | 2.3573 |
| pipeline_inject | 2.0442 | 3.2055 |

## Production Reachability

- Bypasses global rank: query-aware `get_relevant_context` budgets scopes independently; `MemoryInjector` performs a separate coarse re-score.
- Not used by production prompt injection: `MemoryPipeline.read`, query reformulation, vector/RRF, and graph spreading.
- Used in production agent injection: Injector scoped search, tag lookup, optional live-model reranker, controller, first-five formatting, injection recording, and outcome feedback.
- No production caller found: failure-recovery injection and timeline session search/context.

## Metric Validity

Valid here: rank metrics against manual synthetic grades, exact marker-derived rendered IDs, count/token checks, lifecycle leakage, attribution overlap, latency, and save counts.

Unavailable where the interface exposes no prompt or feedback: rendered precision, token-budget checks, and attribution metrics remain null for read-only arms. See `unavailable_metrics` in the JSON artifact.

## Recommended Phase 2 Order

1. Introduce one retrieval result contract carrying ordered candidates, rendered IDs, recorded IDs, score provenance, and limits.
2. Make one query-aware production owner serve TUI, stdin, headless, agent injection, and compaction; remove double injection before changing ranking weights.
3. Enforce final count and total-token budgets, then attribute injection and feedback only to rendered IDs.
4. Preserve BM25/global relevance through Injector selection and wire real files/domains before reconsidering vector, graph, or reranker expansion.
5. Add a relevance floor/no-match outcome and reranker-summary safety validation, then rerun this frozen dataset without changing gold labels.

## Interpretation

Facts: the four arms use different production methods and can return different orders and counts. `MemoryPipeline.inject` does not call `MemoryPipeline.read`.

Inference: unifying ownership around one candidate/result contract is the smallest next step, but this phase does not modify that behavior.

Limits: results apply only to this synthetic fixture, with LLM and vector retrieval disabled. Unavailable metrics are serialized as null rather than replaced with proxies.
