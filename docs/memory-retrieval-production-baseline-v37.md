# Memory Retrieval Production Baseline v37

## Certification

`memory-retrieval-production-v37` is the active production source baseline for
MiniCode Reliability 1B-1A.1. Its parent is
`memory-retrieval-production-v36`.

- manifest:
  `tests/fixtures/memory_retrieval_production_freeze/v37.json`;
- manifest SHA-256:
  `27dda6944d88016ceabcd08960b3b2ef230df7460590d1165b3195ed23adb67b`;
- reason code: `bounded_dns_resolver_capacity`;
- protected file count: 59;
- candidate match: true;
- current files match: true;
- manifest integrity: v1–v37 all true.

## Exact parent delta

Changed:

- `minicode/tools/network_safety.py`

Added:

- `minicode/tools/bounded_resolver.py`

Removed: none.

Every v36 protected file outside this allowlist retains its v36 digest.
Historical manifests v1–v36 were not rewritten.

## Verification

The active verifier reports `candidateMatches=true`,
`currentFiles.matches=true`, 59/59 protected files and the exact one-changed /
one-added / zero-removed lineage.

The official semantic evaluator passed with 108 cases, 37 confirmed gaps,
`phase3b_gate=true`, `remote_calls=0` and `evaluation_passed=true`. Both final
complete pytest suites passed with 3062 passed and 2 skipped. The accepted
semantic gold remained frozen at:

- SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
- size: 3,033,592 bytes;
- mtime_ns: `1784135857000000000`.

The final verifier again reported `candidateMatches=true`,
`currentFiles.matches=true`, 59/59 protected files, and v1–v37 manifest
integrity all true.
