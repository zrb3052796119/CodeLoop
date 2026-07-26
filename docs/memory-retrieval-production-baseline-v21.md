# Memory Retrieval Production Baseline v21

Baseline ID: `memory-retrieval-production-v21`

Parent: `memory-retrieval-production-v20`

Reason: `Batch 7C connection-scoped Assistant and Tool streaming`

Manifest SHA-256:
`5a6422b0ae18649166e3e8d28c990a9736f457093f105db661f7ff4b40d8a8ff`

## Exact v20 to v21 lineage

Changed:

- `minicode/agent_runtime.py`
- `minicode/conversation.py`
- `minicode/web/chat_http.py`
- `minicode/web/static/assets/app.js`
- `minicode/web/static/assets/styles.css`
- `minicode/web/static/index.html`

Added:

- `minicode/conversation_presentation.py`
- `minicode/web/chat_stream.py`

Removed: none.

Every entry uses reason code `dashboard_connection_scoped_chat_stream`. The
active manifest protects 38 production sources. The new core presentation seam
and Web NDJSON framing module are therefore independently protected and covered
by exact tamper-detection tests.

## Certification

- active candidate equals the accepted v21 manifest;
- all 38 current protected source hashes match;
- exact v20 to v21 changed/added/removed sets match;
- every v1 through v21 manifest-integrity pin is true;
- controlled changes to every new/changed v21 contract are reported without
  rewriting the manifest;
- v1 through v20 manifests and pins remain immutable;
- accepted semantic gold SHA, 3,033,592-byte size, and mtime_ns are unchanged;
- official semantic evaluation remains 108 cases, 37 confirmed gaps, zero
  remote calls, Phase 3B true, and pass.

The default verifier is read-only. Candidate generation and explicit writing
target only the fixed v21 path; the evaluator writes generated reports and does
not replace accepted gold.
