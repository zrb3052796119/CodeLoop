# Memory Retrieval Production Baseline v4 Certification

## Certification decision

`memory-retrieval-production-v4` is certified as the active production-source baseline. It is the child of the immutable v3 manifest and accepts only Batch 3B-2B Model request-boundary observability.

v4 does **not** accept a Memory Retrieval algorithm or behavior change. `RunJournal`, Context compaction, Memory storage, retrieval, candidate consolidation, injection, counters, feedback, controller behavior, and the frozen 108-case dataset remain unchanged. Historical Runs are not backfilled.

## Exact scope and event seam

`minicode/run_events.py` defines the independent optional seam:

```python
class AgentEventSink(Protocol):
    def emit(
        self,
        event_type: str,
        *,
        step: int | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None: ...
```

`run_agent_turn(..., event_sink=None)` remains the default. When no sink is supplied, no operation ID is generated and event observation performs no work. A configured sink receives the original bounded payload object. Ordinary sink or observation failures are isolated behind a generic payload-free warning and cannot change messages, Model call counts, fallback/recovery behavior, exception propagation, cleanup, or lifecycle terminal state. `KeyboardInterrupt` and `SystemExit` raised by execution itself are observed and then propagated unchanged.

There is one lexical `_model_next()` call in Agent Loop. Immediately around each actual invocation, v4 records one of these pairs with the same observer-local `modelop_<32 lowercase hex>` ID and the real Agent step:

```text
model.started -> model.completed
model.started -> model.failed
```

Empty responses and recoverable thinking stops are normal completed operations. A later retry is a new loop step and new operation. Effective Context recovery and successful ModelSwitcher recovery leave the failed operation failed and assign the next real Model call a new ID. No completed event is fabricated for a failed operation.

## Safe Model event contract

Persisted payloads are deliberately minimal:

- `model.started`: `operationId`.
- `model.completed`: `operationId`, `resultType=assistant|tool_calls`, real `contentPresent` boolean, and bounded non-negative `toolCallCount`.
- `model.failed`: `operationId` and `failureKind=interrupted|network|timeout|provider_error`.

The Model event contract never contains Prompt, messages, output, Assistant body, thinking, stream chunks, provider/model identity, exception type or text, usage, cache data, cost, tokens, or duration. The existing Batch 3B-2A Tool/Assistant contract remains unchanged: Tool input/output is discarded at the callback boundary, Tool correlation IDs are observer-local, and Assistant records only presence, bounded length, and fixed returned-output kind.

Headless, Gateway-through-Headless, classic non-TTY CLI, and interactive TTY pass their existing `RunObservation` as the sink. Gateway still delegates `run_headless(..., run_source="gateway")`, so one valid `/run` creates exactly one Gateway Run. `RunObservation` forwards Model events through the already-owned lifecycle writer; it exposes no Run ID or storage path and does not duplicate Tool, Assistant, or lifecycle events.

## Read-only projection and UI

`DashboardReadModel` returns no raw event payload. Its Model-event whitelist independently validates operation ID grammar, fixed result/failure enums, a real boolean content flag, a non-boolean bounded integer Tool-call count, and the event envelope's real step. Unknown or invalid fields are dropped.

Runs renders restrained Model request started/completed/failed rows, real step, result type, Tool-call count, and safe failure kind. Operation IDs are correlation metadata rather than provider IDs and are not expanded into raw payloads. A failed Model attempt does not force the containing Run to failed; later ModelSwitcher recovery can lead to `run.completed`.

Coverage is now `lifecycle-model-tool-assistant`, with Model, Tool, and Assistant code paths `live`, historical coverage `partial`, and usage/Memory/Skills `unavailable`. Here `live` means instrumented code path, not Provider connectivity or streaming. Cost, tokens, duration, usage, Memory/Skill runtime events, MCP runtime, Ops aggregation, SSE, and Dashboard writes remain unavailable.

## Immutable evidence and lineage

Pinned manifest SHA-256 values:

- v1: `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`
- v2: `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`
- v3: `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`
- v4: `5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10`

The exact v3→v4 delta is:

| File | Change | Reason code |
|---|---|---|
| `minicode/agent_loop.py` | changed | `model_event_sink` |
| `minicode/run_lifecycle.py` | changed | `model_event_observer` |
| `minicode/headless.py` | changed | `model_event_entrypoint` |
| `minicode/main.py` | changed | `model_event_entrypoint` |
| `minicode/tui/input_handler.py` | changed | `model_event_entrypoint` |
| `minicode/run_events.py` | added | `model_event_sink_dependency` |

No protected file was removed. v4 protects 13 production files. `RunJournal` remains at `20f41213996c853e178bacd114d0e99f4ec94a3a626d8be691324aa74b9144c1`, and every protected Memory/Context source retains its prior hash.

Default verification is read-only:

```bash
python3 scripts/memory_retrieval_production_baseline.py
```

`--print-v4` is deterministic and read-only. `--write-v4` can write only the fixed v4 fixture after validating pinned v1/v2/v3 evidence and exact prior lineage. A controlled protected-source change causes verification failure and does not rewrite the manifest.

## Semantic behavior equivalence

The accepted semantic artifact SHA-256 remains `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`. The complete deterministic behavior projection remains `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`, and the 108-case per-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667` across v1, v2, v3, and v4.

The evaluator re-certified all retrieval arms, candidates/ranks/scores, Gate, consolidation, rendering, controller, counters, feedback, metrics, adjudication, frozen assets, zero remote calls, zero diagnostic filesystem/counter side effects, and formal before/after state equality.

## Verification result

- Agent Event Sink/Model/lifecycle/entrypoint/Agent Loop/integration focused matrix: 86 passed, 2 skipped before the final combined regression.
- Combined lifecycle, Journal, Dashboard, frontend, packaging, Agent Loop, and integration regression: 231 passed, 2 skipped.
- Complete Memory Retrieval matrix: 187 passed.
- Baseline plus semantic certification: 57 passed; active v4 matched all 13 protected files and all four pins.
- Complete pytest: 1647 passed, 2 skipped, 0 failed in 63.37 seconds, with only three existing unregistered benchmark-marker warnings.
- Touched-file Ruff, explicit `py_compile`, full `compileall -q minicode scripts tests`, production `node --check`, dependency inspection, wheel build, isolated installation, installed Gateway/all read APIs/assets, and installed `/run` smoke passed. Runtime dependencies remain empty.
- Isolated HTTP/browser acceptance used safe fake Model/Tool implementations through the real Agent Loop. The normal Tool Run produced 10 ordered events; the failure-then-ModelSwitcher-recovery Run produced 8 ordered events and remained completed. API/UI redaction, distinct paired IDs, real steps, all eight main routes, all five Memory subroutes, localized Runs error/Retry recovery, 1280 px no-overflow layout, and zero browser development-log entries passed.

Any future Memory, Skill, usage/cost/token/cache/duration, MCP-runtime, Ops, SSE, or write-control connection requires a separate minimal event contract, strict read projection, tests, and production re-certification. v4 authorizes only Model request-boundary observability.
