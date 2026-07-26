# Memory Retrieval Production Baseline v30

## Identity

- Baseline: `memory-retrieval-production-v30`
- Parent: `memory-retrieval-production-v29`
- Reason code: `memory_approval_store_ui`
- Manifest: `tests/fixtures/memory_retrieval_production_freeze/v30.json`
- Manifest SHA-256: `55654b2b979812440514686b44c5bf09b5a0527a59709d37907ffb7ffd9c5edd`
- Protected production files: 50

The v29 manifest remains pinned at SHA-256
`e43777832841629549d180e039d40ac54209c5f15a3581e9bdf09b308592d4d1`.

## Exact v29 to v30 lineage

Changed:

- `minicode/web/static/assets/app.js`
- `minicode/web/static/assets/styles.css`

Added: none.

Removed: none.

`app.js` adds the independent fail-closed Memory approval store, validators,
fenced GET/POST controller, existing-`resources.memory` reconciliation, sixth
Memory subroute, and safe master/detail rendering. `styles.css` adds only the
bounded Waku-style approval queue/detail presentation and narrow-screen stack.
No backend authority, transport, persistence, Retrieval/Injection algorithm,
Tool Permission, Chat, Session, MCP, or TUI production file changes in v30.

The unchanged formal assets remain:

- `index.html`: `43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b`
- `cost-format.js`: `194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`

The final changed asset hashes are:

- `app.js`: `3673a3e0d34f718611cea826afe5bdb4cbb8fbfd8711498721fe17cac9e03b80`
- `styles.css`: `a825a19437f1b532195ce6c9785313c08054f8c5830103c0a30474d9ba029d75`

## Certification contract

The default verifier is read-only and must report active v30, exact two-file
lineage, candidate equality, all current files matching, and manifest integrity
for v1 through v30. v30 has parameterized tamper tests for both changed assets,
deterministic candidate generation across CWD/HOME/hash seeds, and a writer that
can write only the fixed v30 target while preserving every v1-v29 byte and
mtime. The accepted semantic gold remains immutable.

