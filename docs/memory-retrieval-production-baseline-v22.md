# Memory Retrieval Production Baseline v22

Baseline ID: `memory-retrieval-production-v22`

Parent: `memory-retrieval-production-v21`

Reason: `Batch 8A-1 Gateway permission approval authority`

Manifest SHA-256:
`a47b1e5f203371e9ced01fed01e6df37947a2a0e891c1bee6c2ed43a51e59906`

## Exact v21 to v22 lineage

Changed:

- `minicode/agent_loop.py`
- `minicode/agent_runtime.py`
- `minicode/conversation.py`
- `minicode/gateway.py`
- `minicode/run_journal.py`
- `minicode/web/http.py`
- `minicode/web/read_model.py`

Added:

- `minicode/file_review.py`
- `minicode/permission_approval.py`
- `minicode/permission_event_contract.py`
- `minicode/permissions.py`
- `minicode/tools/run_command.py`
- `minicode/web/permission_http.py`
- `minicode/workspace.py`

Removed: none.

Every entry uses reason code `gateway_permission_approval_authority`. The
active manifest protects 45 production sources. Existing files newly brought
under protection are classified as added relative to the protected v21 set;
this records the complete permission side-effect boundary without claiming the
files were newly created in the repository.

## Certification

- active candidate equals the accepted v22 manifest;
- all 45 current protected source hashes match;
- exact v21 to v22 changed/added/removed sets match;
- every v1 through v22 manifest-integrity pin is true;
- controlled changes to `permission_approval.py` and
  `permission_event_contract.py` report exactly the altered file;
- the verifier is read-only and cannot rewrite v22 during verification;
- every v1 through v21 manifest and pin remains byte-identical;
- accepted semantic gold remains SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592, and mtime_ns 1784135857000000000;
- official semantic evaluation remains 108 cases, 37 confirmed gaps, zero
  remote calls, Phase 3B true, and pass.

The formal v21 HTML/CSS/JavaScript assets are deliberately outside this delta
and remain byte-identical. Candidate generation and explicit writing target
only the fixed v22 path; historical manifests and the accepted semantic gold
are never regenerated.
