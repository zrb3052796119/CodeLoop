# Memory Retrieval Production Baseline v32

## Identity

- Baseline: `memory-retrieval-production-v32`
- Parent: `memory-retrieval-production-v31`
- Reason code: `dashboard_data_deletion_ui`
- Manifest: `tests/fixtures/memory_retrieval_production_freeze/v32.json`
- Manifest SHA-256:
  `9680f6f4bb61d3489a98fd63cff01d99f6a5af2c98891befbfb6c513fc023fb1`
- Protected production files: 54

The v31 manifest remains byte-identical at SHA-256
`d0ea9a10ccd45d6f8e7807f92acfc38afce801f22e8be0967897653aed82fbae`.

## Exact v31 to v32 lineage

Changed:

- `minicode/web/static/assets/app.js`
- `minicode/web/static/assets/styles.css`

Added: none.

Removed: none.

`app.js` adds strict content-free deletion preview/result validators, two
independent volatile deletion stores, one accessible confirmation surface,
explicit one-shot POST actions, stale/partial/lost-response handling,
tombstone/generation fencing, and authoritative Session/Run/Dock and
Memory/Approval reconciliation. Existing SSE resources may request a fresh GET
preview but can never submit a POST.

`styles.css` adds only the bounded low-saturation Waku deletion presentation,
responsive single-column controls, visible focus, safe wrapping and reduced
motion. The narrow footer's visual order matches its DOM/Tab order.

No backend deletion authority, HTTP schema, Change Feed resource, read model,
storage, Agent, Memory Retrieval/Reflection, TUI or runtime dependency changed
in v32.

## Formal frontend hashes

- `index.html` (unchanged):
  `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`
- `app.js`:
  `62815e4b3bfe79f498e6426a184f7bd256131bd8e52296d401c40160e1f07126`
- `styles.css`:
  `647c5a63d1552e2b4f1b8a0edfe3a14b8b1abfa66189028d6b93f4d1b212d376`
- `cost-format.js` (unchanged):
  `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`

## Certification contract

The default verifier reports active v32, exact two-file lineage, current and
candidate equality for all 54 protected files, and manifest integrity for v1
through v32. v31 is now immutable historical evidence: its candidate and
writer validate and return the pinned target without rewriting it. v32 has
closed-schema, exact-lineage, fixed-pin, writer-preservation and tamper
coverage.

The accepted semantic gold remains immutable at SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
3,033,592 bytes and mtime_ns `1784135857000000000`.
