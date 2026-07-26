# Retrieval Phase 3B Performance

Environment: Apple CPU, local quantized ONNX `multilingual-e5-small`, 384 dimensions, batch size 32. Cold model load was 537.68 ms; observed process maximum-RSS delta during load was 667,303,936 bytes. Timings include query encoding. All indexes and caches were under isolated temporary directories.

## Scale Results

| Entries | Build/encode | Throughput | BM25 build | Warm total P50/P95/P99 | Index bytes | Peak traced memory |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 571.18 ms | 175.08/s | 5.91 ms | 2.25 / 2.70 / 3.28 ms | 878,488 | 3,958,270 |
| 500 | 2,919.88 ms | 171.24/s | 28.98 ms | 2.64 / 2.97 / 6.25 ms | 4,392,568 | 9,259,700 |
| 1,000 | 6,087.88 ms | 164.26/s | 61.10 ms | 3.13 / 4.04 / 9.70 ms | 8,784,973 | 18,373,052 |
| 10,000 | 69,304.50 ms | 144.29/s | 615.92 ms | 14.63 / 39.36 / 73.20 ms | 87,858,338 | 182,982,614 |

The warm total P95 meets the suggested 150 ms at 1,000 entries and 250 ms at 10,000 entries.

## 10,000-Entry P50/P95/P99

| Stage | Milliseconds |
|---|---:|
| Query encoding | 3.53 / 4.61 / 5.00 |
| BM25 search | 6.73 / 8.47 / 8.65 |
| Dense index search | 4.07 / 27.87 / 61.40 |
| Fusion | 0.11 / 0.13 / 0.13 |
| Semantic Gate | 0.19 / 0.21 / 0.23 |
| CandidateConsolidator | 0.11 / 0.13 / 0.16 |
| Full warm retrieval | 14.63 / 39.36 / 73.20 |

## Incremental Lifecycle

| Entries | Single update | Ten updates | Delete/invalidate |
|---:|---:|---:|---:|
| 100 | 6.71 ms | 68.38 ms | 0.006 ms |
| 500 | 6.90 ms | 67.66 ms | 0.005 ms |
| 1,000 | 9.08 ms | 69.06 ms | 0.004 ms |
| 10,000 | 7.82 ms | 81.92 ms | 0.003 ms |

Updates encode only changed records and invalidate the in-memory search matrix; they do not rebuild or re-encode the full index. Query operations write no memory or index files. JSON cache size is linear and relatively large, about 8.8 KB per record; this is acceptable for the prototype but not a production storage recommendation.
