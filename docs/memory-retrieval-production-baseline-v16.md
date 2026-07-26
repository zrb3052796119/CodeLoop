# Memory Retrieval Production Baseline v16

## Purpose

`memory-retrieval-production-v16` is the immutable production-source contract
for Dashboard Batch 6B-2B cooperative cancellation and commit-race safety. It
does not certify a Memory Retrieval algorithm change. It extends the protected
execution boundary because cancellation now crosses the Turn Store,
Conversation, Agent, Run lifecycle, Gateway HTTP composition, and formal UI.

Parent baseline: `memory-retrieval-production-v15`.

Manifest: `tests/fixtures/memory_retrieval_production_freeze/v16.json`.

Manifest SHA-256:
`80fa4db12cb43f904a0d89cf0d32df7bd389fda1001c55b6447d7d1a5355decb`.

## Exact v15 to v16 lineage

Changed protected files:

- `minicode/agent_loop.py`
- `minicode/agent_runtime.py`
- `minicode/conversation.py`
- `minicode/conversation_turn_store.py`
- `minicode/gateway.py`
- `minicode/run_lifecycle.py`
- `minicode/web/chat_http.py`
- `minicode/web/static/assets/app.js`

Newly protected files:

- `minicode/turn_cancellation.py`
- `minicode/web/static/assets/styles.css`
- `minicode/web/static/index.html`

Removed protected files: none.

Every lineage entry uses reason code
`dashboard_chat_cooperative_cancellation`. The resulting protected set contains
33 files. Existing v1-v15 manifests and their pinned bytes remain unchanged.

## Verification properties

- The default verifier is read-only and validates every v1-v16 manifest pin,
  parent lineage, deterministic v16 candidate, and all 33 current hashes.
- v16 generation accepts only the fixed canonical manifest destination. It does
  not authorize arbitrary output paths or rewriting older baselines.
- Candidate construction rejects missing sources, historical drift, wrong
  reason codes, unexpected overlap, and lineage mismatches.
- Exact tamper tests report the changed protected path without rewriting
  accepted evidence.

Final verification reported active baseline
`memory-retrieval-production-v16`, `candidateMatches=true`,
`currentFiles.matches=true` with 33/33 files, and `manifestIntegrity=true` for
every version v1-v16.

## Semantic preservation

The official offline evaluator remained behaviorally unchanged:

- 108 cases;
- 37 confirmed gaps and 18 sealed gaps;
- zero remote calls;
- behavior projection fingerprint
  `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`;
- per-case fingerprint
  `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.

The accepted semantic gold remained byte-identical before and after evaluation:

- SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
- size `3,033,592` bytes;
- mtime ns `1784135857000000000`.

The semantic/baseline contract matrix passed 46 tests after its active-baseline
expectation was advanced from v15 to v16. The final evaluator-after full pytest
result is recorded in the Batch 6B-2B implementation document.

