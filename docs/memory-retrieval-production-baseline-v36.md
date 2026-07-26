# Memory Retrieval Production Baseline v36

## Certification

`memory-retrieval-production-v36` is the active production source baseline for
MiniCode Reliability 1B-1A. Its parent is
`memory-retrieval-production-v35`.

- manifest:
  `tests/fixtures/memory_retrieval_production_freeze/v36.json`
- manifest SHA-256:
  `7d576aed1594c58e96d3125c28e2556ffab7bb60ccdd43c97b462201456a678a`
- reason code: `http_request_network_safety`
- protected file count: 58
- candidate match: true
- current files match: true
- manifest integrity: v1–v36 all true

## Exact parent delta

Changed:

- `minicode/permission_approval.py`
- `minicode/permission_event_contract.py`
- `minicode/permissions.py`
- `minicode/tooling.py`
- `minicode/web/static/assets/app.js`

Added:

- `minicode/tools/http_utils.py`
- `minicode/tools/network_safety.py`

Removed: none.

Every v35 protected file outside this allowlist retains its v35 digest.
Historical manifests v1–v35 were not rewritten.

## Verification

The active verifier reports `candidateMatches=true`,
`currentFiles.matches=true`, 58/58 protected files and the exact five-changed /
two-added / zero-removed lineage.

The official semantic evaluator reports 108 cases, 37 confirmed gaps,
`phase3b_gate=true`, `evaluation_passed=true` and zero remote calls. The
accepted semantic gold remains byte/stat-identical:

- SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`
- size: 3,033,592 bytes
- mtime_ns: `1784135857000000000`

The final complete pytest suites both report 3,042 passed, two skipped and the
same three existing benchmark-marker warnings.
