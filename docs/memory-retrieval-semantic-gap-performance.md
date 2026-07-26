# Memory Retrieval Semantic Gap Performance

> Offline measurements on synthetic candidates; timings and memory are machine-dependent.

| Candidates | Manager p50 / p95 ms | Canonical p50 / p95 ms | Evaluator projection p50 / p95 ms | Peak bytes | Selected | Cap |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 1.020 / 1.077 | 50.645 / 57.657 | 0.029 / 0.034 | 2741841 | 100 | 256 |
| 500 | 6.449 / 7.035 | 314.445 / 317.974 | 0.066 / 0.072 | 13331929 | 256 | 256 |
| 1000 | 13.252 / 34.679 | 350.786 / 354.241 | 0.129 / 0.145 | 15506560 | 256 | 256 |

- Full evaluation peak traced memory: `11574404` bytes.
- Per-case latency p50 / p95 / max: `24.173` / `32.702` / `260.991` ms.
- Complexity: Manager scoring and canonical ranking scan all active entries; sorting is O(N log N). Post-gate CandidateConsolidator remains bounded to 256 before pairwise work.
- The production CandidateConsolidator limit remains 256; this phase does not change it.
