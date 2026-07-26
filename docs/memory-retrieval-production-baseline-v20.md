# Memory Retrieval Production Baseline v20

Baseline ID: `memory-retrieval-production-v20`

Parent: `memory-retrieval-production-v19`

Reason: `Batch 7B SSE-driven Dashboard store invalidation`

Manifest SHA-256:
`4104965fd30bdfeb06910701be6b53d0a623607f3965b15ed8f9d80809baca05`

## Exact v19 to v20 lineage

Changed:

- `minicode/web/static/assets/app.js`
- `minicode/web/static/assets/styles.css`
- `minicode/web/static/index.html`

Added: none.

Removed: none.

All three entries use reason code `dashboard_sse_store_switchover`. The active
manifest protects the same 36 production sources as v19. Backend Gateway, HTTP,
Event Stream, Change Feed, Agent, Session, Turn, Memory, Skill, and MCP sources
therefore remain byte-identical to v19.

The default verifier is read-only. The accepted v1–v19 manifests retain their
existing byte pins; v20 candidate equality, current-file equality, exact
lineage, and controlled tamper reporting are independently tested.
