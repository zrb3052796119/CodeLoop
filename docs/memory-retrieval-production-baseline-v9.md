# Memory Retrieval Production Baseline v9 Certification

## Certification decision

`memory-retrieval-production-v9` is the active production-source baseline and
the child of immutable v8. It accepts only Batch 5B-1 observation seams for
already-completed Context compaction/recovery work and the process-local
WorkingMemory state observed after the existing final-answer protection call.

The observation layer does not read message content, summaries, errors, user
input, prompts, or WorkingMemory entry content. It does not change model calls,
retry decisions, compaction decisions, Context messages, Memory Retrieval,
Session persistence, Tool/Skill behavior, permissions, or the TUI.

## Exact v8 to v9 delta

| File | Change | Reason code |
|---|---|---|
| `minicode/agent_loop.py` | changed | `context_working_memory_observation` |
| `minicode/run_events.py` | changed | `context_working_memory_observation` |
| `minicode/run_journal.py` | changed | `context_working_memory_observation` |
| `minicode/working_memory.py` | added to protection | `context_working_memory_observation` |

No protected file was removed. v9 protects 15 production files.
`run_journal.py` changes only its closed event-type allowlist to accept
`working_memory.observed`.

## Event boundary

An effective canonical compaction may emit `context.compacted` with a bounded
message-count projection, fixed path/trigger/strategy enums, and an observer
ID matching `ctxop_<32 lowercase hex>`. Reliable token counts are included only
when the canonical result supplies them; the ContextManager fallback omits
tokens. Ineffective compactions emit no completed compaction event.

An overflow recovery attempt emits `recovery.started` before calling the
existing recovery implementation. A normally returned attempt emits
`recovery.completed` with `recovered` or `not_recovered`; if recovery itself
raises, the start remains intentionally dangling so the journal does not invent
completion. Successful recovery also emits the matching `context.compacted`
event. All events share the same Context operation ID and never include the
triggering error.

After the existing `protect_context` call for a final answer,
`working_memory.observed` contains only entry and token counts, limits, the
fixed `protected` action, and `process` scope. The frozen snapshot is pure: it
does not clean, reorder, or expose entries. Process-local scope is not a
cross-process guarantee and does not claim that WorkingMemory is consumed by
every compaction path.

With no event sink, Context operation IDs, projections, and WorkingMemory
snapshot work are skipped.

## Dashboard boundary

Run Detail strictly projects the new event payloads and the Timeline renders
bounded summaries. Context and WorkingMemory coverage are marked `partial`.
Overview, Ops, and other cross-run aggregates remain unavailable in Batch
5B-1. Memory Lifecycle explicitly states that cross-run aggregation belongs to
Batch 5B-2.

## Immutable evidence

Pinned manifest SHA-256 values remain unchanged for v1-v8. The v9 manifest is:

`3444072607489ec4cc2405b8fb09fe9bcb122f9427f4b94d25aa66b9aa52d4d0`

Default verification is read-only:

```bash
python3 scripts/generate_memory_retrieval_production_baseline.py
```

`--print-v9` is deterministic and read-only. `--write-v9` owns only the fixed
v9 target after validating every pinned v1-v8 manifest and lineage edge.

## Semantic behavior equivalence

The accepted artifact remains
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
the complete behavior projection remains
`b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`;
and the 108-case fingerprint remains
`b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.
