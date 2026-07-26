# Memory Retrieval Production Baseline v6 Certification

## Certification decision

`memory-retrieval-production-v6` is the active production-source baseline. It is the child of immutable v5 and accepts only Batch 4A observation of canonical `AgentStep.usage` plus monotonic whole-model-call duration.

This certification does not accept a Model request, retry, fallback, response, Tool, Agent, Session, Memory, Skill, Context, lifecycle, persistence, or retrieval behavior change. The frozen 108-case semantic dataset and accepted behavior artifacts remain unchanged. Historical Runs are not backfilled, and Cost remains unavailable.

## Exact v5 to v6 delta

| File | Change | Reason code |
|---|---|---|
| `minicode/agent_loop.py` | changed | `model_usage_observer` |
| `minicode/run_events.py` | changed | `model_usage_projection` |

No protected file is added or removed. Adapters, types, Run lifecycle, RunJournal, Headless, classic main, TUI, Memory, Skill, Session, and Context modules retain their v5 hashes.

## Observation contract

Agent Loop reads the monotonic clock immediately around each existing `_model_next()` invocation. Every actual retry or ModelSwitcher recovery receives a distinct observer-local operation ID and duration. No sink means no operation ID, clock read, or usage projection.

Completed Model events contain a strict projection with `source` (`provider`, `estimated`, or `unavailable`) and four nullable non-negative bounded token buckets. Completed and failed Model events may contain a bounded non-negative `durationMs`; failed events never contain usage. Prompt, messages, output, provider/model identity, raw provider objects, credentials, pricing, Cost, and wall-clock timestamps are not added. Observer, clock, projection, and sink failures remain best-effort and cannot replace the business result or exception.

## Read-only Dashboard boundary

`DashboardReadModel` independently revalidates and pairs Model events by bounded operation IDs. Run detail, Snapshot Overview, and `/api/v1/ops` share retained-RunJournal aggregation under fixed Run and event scan limits. Invalid, duplicate, unpaired, corrupt, or historical observations are localized as unavailable/partial diagnostics rather than zero usage.

Overview, Runs, and Ops display canonical Provider/Estimated/Unavailable buckets and Model duration. Coverage is `lifecycle-model-usage-tool-assistant-skill-memory`; instrumented paths are live, retained historical coverage is partial, and Cost remains `unavailable/null`. The API and page stay read-only with manual refresh and no SSE or polling.

## Immutable evidence

Pinned manifest SHA-256 values:

- v1: `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`
- v2: `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`
- v3: `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`
- v4: `5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10`
- v5: `70ece17f53ec7963395aadc3be2b104636c2804087928d45c707ee94a5e672ff`
- v6: `623366c6d895d057ef03fc7e719d9d2c3dfdd6e4e1f394b355dc6441daaae89b`

Default verification is read-only:

```bash
python3 scripts/memory_retrieval_production_baseline.py
```

`--print-v6` is deterministic and read-only. `--write-v6` writes only the fixed v6 fixture after validating every pinned v1-v5 manifest and lineage edge. Historical writers validate their immutable target and do not rewrite it.

## Semantic behavior equivalence

The accepted artifact SHA-256 remains `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`. The complete behavior projection remains `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`, and the 108-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667` across v1-v6.

Future pricing/Cost, Provider or model identity, WorkingMemory, Context, MCP runtime, SSE, Dashboard writes, or real Chat work requires a separate contract and production re-certification. v6 authorizes only canonical Model usage and duration observation.
