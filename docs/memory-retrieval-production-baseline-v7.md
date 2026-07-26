# Memory Retrieval Production Baseline v7 Certification

## Certification decision

`memory-retrieval-production-v7` is the active production-source baseline. It is the child of immutable v6 and accepts only the Batch 4A.1 repair that gives optional Work Chain controllers explicit neutral state when `enable_work_chain=False`.

The repair restores an existing public execution flag. It does not accept changes to the enabled Work Chain, Model/Tool/Assistant behavior, usage or duration observation, Dashboard/API contracts, Cost, adapters, Memory, Skill, Session, Context ownership, RunJournal, or lifecycle behavior. The frozen 108-case semantic dataset and accepted behavior artifacts remain unchanged.

## Exact v6 to v7 delta

| File | Change | Reason code |
|---|---|---|
| `minicode/agent_loop.py` | changed | `work_chain_disabled_initialization` |

No protected file is added or removed. `minicode/run_events.py` and the other eleven protected files retain their v6 hashes.

## Disabled execution contract

Before entering the Work Chain branch, Agent Loop binds `context_compactor`, `context_cybernetics`, and `cost_control` to `None`. The enabled branch assigns the same concrete objects in the same order as v6.

When disabled, Agent Loop does not build a Work Chain task or construct the orchestrator, compactor, Context cybernetics, self-healing, feedforward, or Cost controllers. A supplied ordinary `ContextManager` retains its basic pre-request and auto-compact fallback behavior. A supplied MemoryManager is not wired into a MemoryPipeline. Model, Tool, Assistant, permissions, callbacks, and optional canonical usage/duration observation continue through their existing paths.

## Immutable evidence

Pinned manifest SHA-256 values:

- v1: `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`
- v2: `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`
- v3: `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`
- v4: `5034b342d68c9a8ef7b450fe2f4bcbda370204f92d3c87f05caf15eec1002e10`
- v5: `70ece17f53ec7963395aadc3be2b104636c2804087928d45c707ee94a5e672ff`
- v6: `623366c6d895d057ef03fc7e719d9d2c3dfdd6e4e1f394b355dc6441daaae89b`
- v7: `120bec4ee33cbbee5d5d056024b96e3e331c1b3101cc6dbe36beaec8fd17ebf4`

Default verification is read-only:

```bash
python3 scripts/memory_retrieval_production_baseline.py
```

`--print-v7` is deterministic and read-only. `--write-v7` writes only the fixed v7 fixture after validating every pinned v1-v6 manifest and lineage edge. Historical writers, including `--write-v6`, validate their immutable target and do not rewrite it.

## Semantic behavior equivalence

The accepted artifact SHA-256 remains `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`. The complete behavior projection remains `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`, and the 108-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667` across v1-v7.

Pricing and canonical Cost remain outside this certification. The next authorized feature batch is Batch 4B: Versioned Pricing Catalog + Canonical Cost Truth.
