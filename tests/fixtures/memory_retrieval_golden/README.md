# Memory Retrieval Golden Dataset

This fixture is a fully synthetic, manually labelled retrieval benchmark. It
contains no production memory, session transcript, credential, private path, or
remote-model output. Every document declares `synthetic_data: true` and uses the
fixed UTC reference timestamp `1736942400` (2025-01-15 12:00:00Z).

## Labels

- `graded_relevance=3`: the primary, directly actionable memory.
- `graded_relevance=2`: relevant supporting context.
- `graded_relevance=1`: weak but defensible secondary context.
- `graded_relevance=0`: irrelevant or prohibited for this task.
- `primary_id`: the single best memory, or `null` when no injection is expected.
- `must_include_ids`: relevant entries whose absence is diagnostically important.
- `may_include_ids`: acceptable secondary entries; they are relevant for ranking metrics.
- `must_exclude_ids`: entries that must not be returned or injected, including
  inactive lifecycle, unsafe, unapproved, superseded, and deliberately irrelevant entries.
- `expected_no_injection`: any returned/rendered memory is a false injection.

Gold labels were authored from each synthetic scenario's stated facts. They are
not generated from current MiniCode output. Current defects are therefore visible
as failures and are not encoded as desired behavior.

## Validation

The dependency-free loader validates the published schema contract and then
performs cross-record checks that JSON Schema alone cannot express: globally
unique case and memory IDs, expected IDs that exist in the case, disjoint
must-include/may-include/must-exclude sets, valid `primary_id`, complete grades,
fixed timestamps, and the synthetic-data declaration.

## Evaluator Arms

Each case/arm gets an isolated temporary USER/PROJECT/LOCAL store and a fresh
`MemoryManager`. Exact evaluator-only markers (`[[MRID:<id>]]`) are appended to
synthetic memory content so rendered prompt IDs can be recovered without fuzzy
substring matching. The marker is never written to production memory.

1. `manager_global_search`: `MemoryManager.search(scope=None)`.
2. `manager_context_query`: query-aware `get_relevant_context`.
3. `pipeline_read`: `MemoryPipeline.read`, with reranker and vector disabled.
4. `pipeline_inject`: `MemoryPipeline.inject`, with reranker and vector disabled.

## Metrics

For an ordered result `R` and relevant set `G={id | grade>0}`:

- `Precision@k = |R[:k] intersect G| / k` (the denominator is `k`; missing ranks are misses).
- `Recall@k = |R[:k] intersect G| / |G|`; unavailable when `G` is empty.
- `MRR = 1 / rank` of the first grade-positive result, otherwise `0`.
- `nDCG@5` uses gain `2^grade - 1` and logarithmic rank discount, divided by the ideal DCG.
- Primary hit means `primary_id` occurs in the first five returned/rendered IDs.
- Exclude violation means any `must_exclude_id` appears.
- Negative false injection means a case marked `expected_no_injection` emitted any ID.
- Duplicate injection means two emitted IDs have identical normalized fixture content.
- Max-memory and token-budget violations compare actual output/rendering with case limits.
- Inactive leakage means an emitted entry fails the production `MemoryEntry.is_active` predicate.
- Actual-rendered precision is ordinary precision over IDs parsed from the real prompt text.
- Feedback-attribution precision is `|feedback_ids intersect rendered_ids| / |feedback_ids|`.
- ID disagreements use set inequality between returned/rendered and rendered/recorded IDs.
- Pairwise Jaccard uses the top-five ID sets; primary agreement compares arm top-one IDs.

Latency percentiles and save counts are observations, not deterministic correctness
fields. Metrics that an interface cannot expose are serialized as `null` and listed
under `unavailable_metrics`; the evaluator does not invent proxy values.
