# Memory Retrieval Production Baseline v15

## Purpose

`memory-retrieval-production-v15` is the immutable production-source contract
for Dashboard Batch 6B-2A durable Turn identity and restart reconciliation. It
does not certify a Memory Retrieval algorithm change; it extends the existing
execution boundary because Chat now depends on new durable state and Session
commit semantics.

Parent baseline: `memory-retrieval-production-v14`.

Manifest: `tests/fixtures/memory_retrieval_production_freeze/v15.json`.

Manifest SHA-256:
`f9e6254c59f8e7b4065c70aba28c20e8d53361e252866a1519264be92704df7a`.

## Exact v14 to v15 lineage

Changed protected files:

- `minicode/conversation.py`
- `minicode/web/chat_http.py`

Newly protected files:

- `minicode/conversation_turn_store.py`
- `minicode/session.py`
- `minicode/web/http.py`
- `minicode/web/static/assets/app.js`

Removed protected files: none.

The resulting protected set contains 30 files. `reasonCode` is exactly
`dashboard_chat_durable_turn_identity`. Existing historical chat lineage from
v13 to v14 is preserved independently.

## Verification properties

- The default verifier is read-only and verifies v1-v15 manifest pins, each
  parent lineage, the deterministic v15 candidate, and all 30 current hashes.
- `--print-v15` writes only canonical JSON to stdout. `--write-v15` may write
  only the fixed v15 path after historical validation. `--write-v14` is
  validation-only; v1-v14 manifests remain immutable.
- Candidate generation rejects missing sources, parent-source drift, unexpected
  overlap, wrong reason codes, and lineage differences.
- A tampered protected file is reported as the exact mismatch and cannot cause
  the verifier to rewrite accepted evidence.

Final default verification reported:

- active baseline `memory-retrieval-production-v15`;
- `candidateMatches=true`;
- `currentFiles.matches=true`, 30/30 files;
- v1-v15 `manifestIntegrity=true` for every version;
- exact two changed, four added, zero removed lineage.

## Semantic preservation

The official offline evaluator remained unchanged:

- 108 cases;
- 37 confirmed gaps, 18 sealed;
- 0 remote calls;
- behavior projection fingerprint
  `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`;
- per-case fingerprint
  `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.

The accepted semantic gold remained byte-identical:

- SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
- size `3,033,592` bytes;
- mtime ns `1784135857000000000`.

The v15 baseline contract tests passed 63 tests, semantic evaluator contract
tests passed 32, and the final evaluator-after regression passed
`2144 passed, 2 skipped, 3 warnings in 107.18s`.

