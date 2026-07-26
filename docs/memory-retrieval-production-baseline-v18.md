# Memory Retrieval Production Baseline v18

## Purpose

`memory-retrieval-production-v18` certifies the production-source delta for
Dashboard Batch 7A live-refresh foundation. It does not change Memory Retrieval
semantics. Its parent is `memory-retrieval-production-v17`; every v1-v17
manifest is immutable and every historical writer is validation-only.

Manifest: `tests/fixtures/memory_retrieval_production_freeze/v18.json`.

Manifest SHA-256:
`515d3cacd96365bc09bfb608df59ff1bfcc4b0c10cff1d1e4e114cb8ef6ecee5`.

## Exact v17 to v18 lineage

Changed protected files:

- `minicode/gateway.py`
- `minicode/web/http.py`
- `minicode/web/static/assets/app.js`
- `minicode/web/static/assets/styles.css`
- `minicode/web/static/index.html`

Added protected files:

- `minicode/web/change_feed.py`
- `minicode/web/read_model.py` (newly protected scoped-MCP composition seam)

Removed protected files: none.

Every declaration uses reason code `dashboard_live_refresh_foundation` and the
single reason `Batch 7A Dashboard live refresh foundation`. The protected set
contains 35 files.

## Verification properties

- The default verifier is read-only and selects v18.
- Candidate construction requires the exact pinned v17 parent and rejects every
  changed, added, or removed path outside the declaration above.
- The active verifier checks deterministic candidate equality, all 35 current
  hashes, every v1-v18 manifest pin, and every historical lineage.
- v16 and v17 candidate/writer compatibility is validation-only; only the fixed
  canonical v18 target can be generated.
- Tamper tests cover every changed and added v18 source and report only the exact
  mismatched path without rewriting the manifest.

The official semantic evaluator and accepted semantic gold remain independent
of this source certification. Their final unchanged results are recorded after
the Batch 7A acceptance run.
