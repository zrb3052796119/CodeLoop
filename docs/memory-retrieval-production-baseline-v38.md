# Memory Retrieval Production Baseline v38

## Certification

`memory-retrieval-production-v38` is the active production source baseline for
MiniCode Reliability 1B-1B. Its parent is
`memory-retrieval-production-v37`.

- manifest:
  `tests/fixtures/memory_retrieval_production_freeze/v38.json`;
- manifest SHA-256:
  `49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`;
- reason code: `web_fetch_safe_transport_boundary`;
- protected file count: 60;
- candidate match: true;
- current files match: true;
- manifest integrity: v1–v38 all true.

## Exact parent delta

Changed:

- `minicode/tools/http_utils.py`

Added to protection:

- `minicode/tools/web_fetch.py`

Removed: none.

`web_fetch.py` was not a protected v37 path, so v38 truthfully records it as
added rather than changed. Every v37 protected file outside the one-item
changed allowlist retains its v37 digest. Historical manifests v1–v37 and the
v37 SHA were not rewritten.

## Verification

The default verifier is read-only and reports
`activeBaselineId=memory-retrieval-production-v38`,
`candidateMatches=true`, `currentFiles.matches=true`, 60/60 protected files,
the exact one-changed / one-added / zero-removed lineage and v1–v38 manifest
integrity all true. `boundedDnsResolverLineage` independently preserves the
v36→v37 delta.

The v38 writer targets only the fixed `v38.json` path. A controlled tamper of
`minicode/tools/web_fetch.py` produces exactly one current-file mismatch and
does not rewrite any manifest.

The official semantic evaluator passed with 108 cases, 37 confirmed gaps,
`phase3b_gate=true`, `remote_calls=0` and `evaluation_passed=true`. Both final
complete pytest suites passed with 3147 passed and 2 skipped. The accepted
semantic gold remained frozen at:

- SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
- size: 3,033,592 bytes;
- mtime_ns: `1784135857000000000`.
