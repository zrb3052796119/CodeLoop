# Memory Retrieval Production Baseline v33

## Identity

- Baseline: `memory-retrieval-production-v33`
- Parent: `memory-retrieval-production-v32`
- Manifest: `tests/fixtures/memory_retrieval_production_freeze/v33.json`
- Manifest SHA-256:
  `a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313`
- Protected production files: 56
- Reason code: `persistence_inventory_read_only_health`

## Exact v32 → v33 delta

Changed:

- `minicode/gateway.py`
- `minicode/web/http.py`
- `minicode/web/static/assets/app.js`
- `minicode/web/static/assets/styles.css`

Added:

- `minicode/storage_health.py`
- `minicode/web/storage_health_http.py`

Removed: none.

Tests, documentation, reports, plans and the manifest implementation itself are
not production-protected files.

## Certified boundary

v33 protects the single bounded/no-write persistence-health authority, its thin
query-free HTTP adapter, Gateway composition, and the read-only System/Data
Health projection. It does not certify cleanup, reset, retention, repair,
migration or index rebuild behavior because none is implemented in Batch 9A-1.

The v1–v32 manifests remain byte-identical. v33 does not modify the accepted
108-case semantic gold, behavior projection or per-case fingerprints.
