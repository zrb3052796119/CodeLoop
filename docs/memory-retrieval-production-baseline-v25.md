# Memory Retrieval Production Baseline v25

Baseline ID: `memory-retrieval-production-v25`

Parent: `memory-retrieval-production-v24`

Reason: `Batch 8A-2.1 permission UI fail-closed state hardening`

Manifest SHA-256:
`c431a30e03e12aab5085f49eab22a86aa57c99190fb93fb7fcb0c207c4a22aef`

## Exact v24 to v25 lineage

Changed:

- `minicode/web/static/assets/app.js`

Added: none.

Removed: none.

The active manifest protects the same 45 production sources as v24. Its new
`app.js` SHA-256 is
`1508700d7d75d99f6a5c166172c89f761e81100bc6d89f6b2873731c1e747ccb`.
The change adds frontend-only permission review consistency checking and
terminal permission-action retirement/reconciliation. It does not change
PermissionApprovalBroker, PermissionManager, permission HTTP, REST/SSE schemas,
Conversation, Gateway, RunJournal, or any backend approval semantics.

## Certification

- deterministic candidate equals the accepted v25 manifest;
- all 45 current protected hashes match;
- exact v24 to v25 changed/added/removed sets match;
- every v1 through v25 manifest-integrity pin is true;
- v24 remains SHA-256
  `f6022dec899fbf083db090385dd4358560673817e25764e469d97548e827307f`;
- every v1 through v24 manifest remains immutable and historical candidate /
  writer entrypoints validate pinned evidence without rewriting it;
- accepted semantic gold remains SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592 bytes, mtime_ns 1784135857000000000;
- official semantic evaluation remains 108 cases, 37 confirmed gaps, zero
  remote calls, Phase 3B true, and pass;
- the installed wheel contains the exact certified `app.js` and passes the
  Gateway/static/permission/Chat/Cancel/Status/SSE smoke;
- both final full suites pass 2,445 tests with two skips and only three
  pre-existing benchmark-marker warnings.

Default verification is read-only. Only the explicit fixed v25 writer was used
to establish this manifest; old manifests and accepted semantic gold were never
regenerated, rewritten, or overwritten.
