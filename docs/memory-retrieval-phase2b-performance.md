# Memory Retrieval Phase 2B Performance

| Input candidates | Retained | Suppressed | P50 ms | P95 ms | Peak bytes |
|---:|---:|---:|---:|---:|---:|
| 100 | 100 | 0 | 2.7450 | 2.9783 | 349682 |
| 500 | 256 | 244 | 8.3429 | 13.0428 | 929716 |
| 1000 | 256 | 744 | 8.4401 | 8.6589 | 1013172 |

- Enforcement mode: `advisory`.
- Strict wall-clock result: `False`.
- Deterministic performance invariants: `True`.
- Full canonical P95: `2.9173 ms`.
- Phase 2A reference: `2.1233 ms`.
- Material limit: `2.8665 ms`.
- Wall-clock gates: `{"canonical_p95_not_materially_above_phase2a": false, "consolidator_100_p95_at_most_10_ms": true}`.
- Complexity bound: `O(N log N + P + B^2), with deterministic buckets and B<=256`.
- Average task-start saves: `0.9394`.
- Average full-turn saves: `1.8788`.
