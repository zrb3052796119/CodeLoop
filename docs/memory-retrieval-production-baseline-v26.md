# Memory Retrieval Production Baseline v26

## Identity

```text
baselineId: memory-retrieval-production-v26
parentBaselineId: memory-retrieval-production-v25
manifest SHA-256: b44abf36befb98723b26036530296f8675a0d92ae59884956767b352445ed936
protected files: 50
reasonCode: persistent_memory_approval_authority
```

v25 remains byte-identical with SHA-256
`c431a30e03e12aab5085f49eab22a86aa57c99190fb93fb7fcb0c207c4a22aef`.
The accepted semantic gold is not rewritten.

## Exact v25 to v26 lineage

Changed protected files:

```text
minicode/gateway.py
minicode/memory.py
minicode/memory_pipeline.py
minicode/web/http.py
```

Files first added to the protection set:

```text
minicode/agent_reflection.py
minicode/memory_approval.py
minicode/memory_curator_agent.py
minicode/memory_store.py
minicode/web/memory_approval_http.py
```

`agent_reflection.py` and `memory_curator_agent.py` existed before v26 but were
not protected by v25; v26 adds them because their automatic durable Memory
writes are part of the approval boundary. The other three are new production
modules. Removed files: none.

## Protected behavior

v26 certifies:

- typed explicit versus review-required creation policy;
- persistent pending exclusion through the existing `is_active` contract;
- automatic Pipeline, legacy reflection and curator policy wiring;
- one cooperative local Memory store transaction;
- typed workspace-scoped approval authority and safe review projection;
- strict loopback pending/decision HTTP routes; and
- unchanged Retrieval ranking, gates, budgets, consolidation and Prompt form.

The active verifier requires candidate equality, current-file equality, exact
lineage, and every v1-v26 pinned manifest integrity check. Historical manifest
writers through v25 are immutable validation functions; only the fixed v26
writer can write the v26 target.

## Semantic certification

The official evaluator remains the frozen 108-case offline authority. Its
isolated filesystem projection ignores only the exact persistent coordination
lock after validating it is a regular `0600`, zero-byte file. It continues to
detect every other temporary file and formal-tree change.

Required result:

```text
cases: 108
confirmed gaps: 37
Phase 3B: true
remote calls: 0
evaluation_passed: true
```

Accepted gold identity remains:

```text
SHA-256: 5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b
size: 3033592
mtime_ns: 1784135857000000000
```

No semantic artifact, behavior projection or per-case fingerprint is resigned.
