# Memory Retrieval Production Baseline v5 Certification

## Certification decision

`memory-retrieval-production-v5` is the active production-source baseline. It is the child of immutable v4 and accepts only Batch 3C-1 observation of already-computed Skill Routing and final Memory Retrieval/Injection results.

This certification does not accept a routing, retrieval, rendering, injection, controller, counter, feedback, persistence, Session, Context, Tool, Model, or Agent behavior change. The frozen 108-case semantic dataset and accepted behavior artifacts remain unchanged. Historical Runs are not backfilled.

## Exact v4 to v5 delta

| File | Change | Reason code |
|---|---|---|
| `minicode/agent_loop.py` | changed | `runtime_memory_observer` |
| `minicode/run_events.py` | changed | `runtime_event_projection` |
| `minicode/headless.py` | changed | `skill_event_entrypoint` |
| `minicode/main.py` | changed | `skill_event_entrypoint` |
| `minicode/tui/input_handler.py` | changed | `skill_event_entrypoint` |

No protected file is added or removed. `run_lifecycle.py`, `run_journal.py`, `skill_router.py`, `memory_pipeline.py`, `memory_retrieval.py`, `memory_injector.py`, `memory.py`, candidate consolidation, and Context compaction are unchanged from v4.

## Runtime event contract

Each execution surface projects its existing `SkillRoutingResult` once, inside the existing top-level Run. Gateway continues to delegate Headless and does not create a second Run or route a second time.

Agent Loop observes the final `MemoryPipeline.last_retrieval_result` after the single existing `inject_memories(...)` call. It does not invoke retrieval, rendering, or injection again. A missing result produces no Memory event; an executed zero-result retrieval remains distinguishable through its real final result.

The bounded events are:

- `skill.routed`: routing version, controlled intent/action enums, bounded total/selected counts, at most 20 validated selected Skill names/source/directory/finite scores, explicit truncation, and fallback boolean.
- `memory.retrieved`: retrieval version, bounded candidate/selected/suppressed counts, no-match boolean, and controlled no-match reason.
- `memory.rendered`: render version, bounded rendered count/token estimate, controlled controller mode, and injected boolean.

They never contain Prompt, messages, output, Skill descriptions/paths/reasons/tools/affinity, Memory content/IDs/query/hash/diagnostics, raw exceptions, usage, cost, duration, or provider data. Observer failures remain best-effort and cannot alter execution.

## Read-only Dashboard boundary

The existing Runs list/detail APIs remain the only data source. `DashboardReadModel` independently revalidates and whitelists the three event types and never returns raw payload. Runs, Skills Routing, Memory Retrieval, and Memory Injection render the same safe projections through independent request state with loading, loaded, empty, historical/no-event, partial, error, Retry, manual refresh, and stale-response protection.

Coverage is `lifecycle-model-tool-assistant-skill-memory`: instrumented code paths are live, historical coverage is partial, and usage/cost/duration remain unavailable. Skills Catalog, persistent Memory pages, Connections, Ops, MCP, the mock/read-only Dock, and all write controls remain outside v5.

## Immutable evidence

Pinned manifest SHA-256 values:

- v1: `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`
- v2: `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`
- v3: `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`
- v4: `5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10`
- v5: `70ece17f53ec7963395aadc3be2b104636c2804087928d45c707ee94a5e672ff`

Default verification is read-only:

```bash
python3 scripts/memory_retrieval_production_baseline.py
```

`--print-v5` is deterministic and read-only. `--write-v5` writes only the fixed v5 fixture after validating all pinned v1-v4 evidence and every prior lineage edge. The historical writers validate their immutable target and do not rewrite it.

## Semantic behavior equivalence

The accepted artifact SHA-256 remains `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`. The complete behavior projection remains `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`, and the 108-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667` across v1-v5.

The evaluator re-certifies retrieval arms, candidates/ranks/scores, Gate, consolidation, rendering, controller, counters, feedback, metrics, adjudication, frozen assets, zero remote calls, zero diagnostic filesystem/counter side effects, and formal before/after state equality.

Future usage/cost/token/cache/duration, WorkingMemory, Context, MCP runtime, Ops, SSE, Dashboard writes, or real Chat work requires a separate contract and production re-certification. v5 authorizes only Skill Routing and final Memory result observability.
