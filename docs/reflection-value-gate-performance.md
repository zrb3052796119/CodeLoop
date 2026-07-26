# Reflection Value Gate Performance

Measurements use `time.perf_counter` and `tracemalloc`, with 11 isolated iterations per scenario and no benchmark dependency.

| Scenario | Median | p95 | Peak | Generated | Valid | Rejected | Accepted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `normal_1_claims` | 0.067 ms | 0.072 ms | 8.0 KiB | 1 | 1 | 0 | 1 |
| `normal_10_claims` | 0.505 ms | 0.531 ms | 28.1 KiB | 10 | 10 | 0 | 1 |
| `normal_100_claims` | 5.009 ms | 5.262 ms | 184.8 KiB | 100 | 100 | 0 | 1 |
| `duplicate_semantic_key_100` | 0.912 ms | 0.932 ms | 47.0 KiB | 100 | 1 | 0 | 1 |
| `invalid_evidence_reference_100` | 3.644 ms | 3.990 ms | 136.3 KiB | 100 | 0 | 100 | 0 |
| `extreme_text_100k` | 12.603 ms | 12.803 ms | 392.2 KiB | 1 | 0 | 1 | 0 |
| `cyclic_metadata` | 0.054 ms | 0.059 ms | 7.4 KiB | 1 | 1 | 0 | 1 |

- 100-claim normal path under 10 ms: `True`
- 100/10-claim median time ratio: `9.919`
- Complexity: Evidence and claims are indexed once; validation work is proportional to claims plus referenced evidence IDs.
- Absolute timings are machine-specific; the fixed evidence-ID indexes avoid claim-to-all-evidence string cross-products.
