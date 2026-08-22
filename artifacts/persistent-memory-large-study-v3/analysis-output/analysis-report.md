# Analysis Report: Large Persistent-Memory Study

## Analysis contract

- Primary question: does an approved relevant Memory reduce repository discovery cost on a new conversation in the same synthetic project while preserving oracle-verified success?
- Design: 16 task families, three randomized provider blocks, 48 warm/cold pairs and 96 target Turns.
- Inferential unit: task family (`n=16`). The three provider blocks are nested repeats, not 48 independent subjects.
- Positive savings and positive percentage reductions favor Memory.
- No valid first-attempt observation was excluded or replaced.

## Outcome

Memory target Turns passed 48/48 (100.0%) versus 47/48 (97.9%) for cold controls. All 48 intended Memory injections were observed; cold controls had 0 injections.

The sole failed target was `pmem-b3-package-map-cold`: the agent completed repository operations but returned progress prose without the required marker. It remains in the intent-to-treat dataset.

## Primary efficiency results

- Repository tool calls: 79.2% reduction (family-cluster 95% CI 76.1% to 81.3%); exact Wilcoxon p=0.000031, Holm-adjusted p=0.000061, rank-biserial r=1.000.
- Task input tokens: 57.6% reduction (family-cluster 95% CI 52.8% to 61.4%); exact Wilcoxon p=0.000031, Holm-adjusted p=0.000061, rank-biserial r=1.000.

## Secondary results

- Task model calls: 57.6% reduction (family-cluster 95% CI 52.9% to 61.4%); exact Wilcoxon p=0.000031, Holm-adjusted p=0.000122, rank-biserial r=1.000.
- Task output tokens: 60.5% reduction (family-cluster 95% CI 56.0% to 63.9%); exact Wilcoxon p=0.000031, Holm-adjusted p=0.000122, rank-biserial r=1.000.
- Tool failures: mean absolute saving 0.021 per target Turn (family-cluster 95% CI 0.000 to 0.062); exact Wilcoxon p=1.0000, Holm-adjusted p=1.0000. Only one cold Turn had a tool failure, so a relative percentage is not informative.
- Elapsed duration (ms): 54.1% reduction (family-cluster 95% CI 49.2% to 58.0%); exact Wilcoxon p=0.000031, Holm-adjusted p=0.000122, rank-biserial r=1.000.

Direct successful `read_file` was the first repository action in 48/48 Memory Turns versus 0/48 cold Turns. Because event journals intentionally omit tool arguments, this proves direct-first mechanism use but not the exact path from journal data alone.

## Lesson creation and amortization

All 24 learned-condition creation Turns produced a verified failed-read/successful-read recovery and all matching cases passed the lesson-write plus next-Turn injection gates. A creation Turn used a mean 39228 task input tokens and 5.17 repository tool calls. If the entire useful recovery Turn is conservatively treated as Memory overhead, its task-input cost is recovered after approximately 1.93 comparable future reuses; including post-run reflection gives 2.03 reuses. This amortization estimate is descriptive, because the original recovery Turn also completed useful work.

## Interpretation boundary

This experiment supports a causal claim for the randomized paired synthetic path-recovery workload: relevant approved Memory reduced discovery work without an observed success penalty. It does not establish performance on arbitrary coding, mutation, debugging or long-horizon tasks. Family-level resampling captures variation across the 16 task families; it cannot capture all future repository or provider variation. A 48/48 warm success result is strong descriptive evidence but is not a formal non-inferiority proof.

## Reproducibility authority

- Manifest: `artifacts/persistent-memory-large-study-v3/manifest.json` (SHA-256 `923272933307127ab0a99e45e1e8449f10ee8a121810baf05e71196d195f6e0d`)
- First-attempt result: `artifacts/persistent-memory-large-study-v3/full-results-initial.json` (SHA-256 `6cb06e4ce0aca747f837a678b8f678ceb7b5249ba6ae4e078664c55adbaed592`)
- Bootstrap: 20,000 family-cluster samples, seed `20260821`.
- Exact tests enumerate all sign assignments after zero differences are removed.
