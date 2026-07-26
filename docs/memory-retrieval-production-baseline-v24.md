# Memory Retrieval Production Baseline v24

Baseline ID: `memory-retrieval-production-v24`

Parent: `memory-retrieval-production-v23`

Reason: `Batch 8A-2 Dashboard permission approval UI and realtime invalidation`

Manifest SHA-256:
`f6022dec899fbf083db090385dd4358560673817e25764e469d97548e827307f`

## Exact v23 to v24 lineage

Changed:

- `minicode/gateway.py`
- `minicode/web/change_feed.py`
- `minicode/web/event_stream.py`
- `minicode/web/http.py`
- `minicode/web/static/assets/app.js`
- `minicode/web/static/assets/styles.css`
- `minicode/web/static/index.html`

Added: none.

Removed: none.

The active manifest protects the same 45 production sources as v23. Authority
modules including PermissionApprovalBroker, PermissionManager, permission HTTP,
Conversation, AgentRuntime, RunJournal, and Dashboard ReadModel remain
byte-identical to v23.

## Certification

- deterministic candidate equals the accepted v24 manifest;
- all 45 current protected hashes match;
- exact v23 to v24 changed/added/removed sets match;
- every v1 through v24 manifest-integrity pin is true;
- controlled file tampering reports the precise target and the default verifier
  remains read-only;
- v23 remains SHA-256
  `c6cab0e867db309f9ddfbaf3034e269f4f65ce7b1c66e155997c0697b3388aa8`;
- every v1 through v23 manifest remains immutable;
- accepted semantic gold remains SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592 bytes, mtime_ns 1784135857000000000;
- official semantic evaluation remains 108 cases, 37 confirmed gaps, zero
  remote calls, Phase 3B true, and pass twice;
- installed-wheel static/API/Chat/permission/SSE smoke passes 9 tests;
- final full suites pass 2,437 tests twice with two skips and only three
  pre-existing benchmark-marker warnings.

Default verification is read-only. Historical v23 candidate/writer entrypoints
validate pinned evidence without reconstructing or rewriting v23; only the
explicit fixed v24 writer was used to establish this manifest. The accepted
semantic gold was never regenerated or overwritten.
