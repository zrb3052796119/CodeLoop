# Memory Retrieval Semantic Gap Performance

> Offline measurements on synthetic candidates; timings and memory are machine-dependent.

| Candidates | Manager p50 / p95 ms | Canonical p50 / p95 ms | Evaluator projection p50 / p95 ms | Peak bytes | Selected | Cap |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 1.357 / 2.011 | 58.250 / 67.026 | 0.040 / 0.049 | 2592073 | 100 | 256 |
| 500 | 6.695 / 7.303 | 358.920 / 367.879 | 0.082 / 0.103 | 11707860 | 256 | 256 |
| 1000 | 11.832 / 14.836 | 351.335 / 358.301 | 0.181 / 0.289 | 13119611 | 256 | 256 |

- Full evaluation peak traced memory: `30411802` bytes.
- Per-case latency p50 / p95 / max: `85.406` / `245.713` / `696.742` ms.
- Complexity: Manager scoring and canonical ranking scan all active entries; sorting is O(N log N). Post-gate CandidateConsolidator remains bounded to 256 before pairwise work.
- The production CandidateConsolidator limit remains 256; this phase does not change it.
