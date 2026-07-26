# TaskEvidence Performance

Measured with `python scripts/benchmark_reflection_evidence.py` on 2026-07-14. Each latency is the median of seven runs; peak memory is Python allocation peak from `tracemalloc`.

| Scenario | Input events | Median | Peak | Result |
| --- | ---: | ---: | ---: | --- |
| Normal trace | 100 | 4.583 ms | 83.8 KiB | 100 file facts; bounded to 64 tool records |
| Normal trace above limit | 1,000 | 23.682 ms | 424.4 KiB | Processes 500 and records a truncation diagnostic |
| Project event limit | 500 | 23.528 ms | 424.3 KiB | Bounded file/tool output |
| Repeated same-call errors | 500 | 24.271 ms | 174.5 KiB | One merged logical error |
| 2,000-level cyclic payload | 1 | 0.029 ms | 3.7 KiB | No recursion failure or payload path guess |

The extractor caps input events at 500, top-level evidence lists at 64 records (file roles allow 64 per role), nested evidence references at 64 IDs, traversal depth at 5, and copied text at 600 characters. The measurements show approximately linear cost through the accepted event range; inputs above the project limit do not increase extraction work beyond the bounded prefix.
