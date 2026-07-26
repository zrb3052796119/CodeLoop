# Memory Retrieval Production Baseline v19

## Purpose

`memory-retrieval-production-v19` certifies the production-source delta for
Dashboard Batch 7A.1 versioned SSE event transport. It does not change Memory
Retrieval semantics. Its parent is `memory-retrieval-production-v18`; every
v1–v18 manifest is immutable and every historical writer is validation-only.

Manifest: `tests/fixtures/memory_retrieval_production_freeze/v19.json`.

Manifest SHA-256:
`9c48c5c0f02f48c49a31411292b1d65b1e52de4667c2048477343ff64eaa82c6`.

## Exact v18 to v19 lineage

Changed protected files:

- `minicode/gateway.py`
- `minicode/web/http.py`

Added protected file:

- `minicode/web/event_stream.py`

Removed protected files: none.

Every declaration uses reason code `dashboard_sse_event_transport` and the
single reason `Batch 7A.1 versioned SSE event transport`. The protected set
contains 36 files.

## Verification properties

- The default verifier is read-only and selects v19.
- Candidate construction requires every exact pinned v1–v18 manifest and rejects
  any changed, added, or removed production path outside the declaration above.
- The active verifier checks deterministic candidate equality, all 36 current
  hashes, every v1–v19 manifest pin, and every historical lineage.
- v18 candidate/writer compatibility is validation-only; only the fixed
  canonical v19 target can be generated.
- Tamper tests cover every changed and added v19 source, report only the exact
  mismatched path, and prove the manifest bytes and mtime are not rewritten.

The official semantic evaluator and accepted semantic gold remain independent
of this source certification. Batch 7A.1 records their unchanged results after
the final acceptance run.
