# Memory Retrieval Production Baseline v39

## Certification

`memory-retrieval-production-v39` is the active production source baseline for
MiniCode Reliability 1B-1C. Its parent is
`memory-retrieval-production-v38`.

- manifest:
  `tests/fixtures/memory_retrieval_production_freeze/v39.json`;
- manifest SHA-256:
  `9bcf038d20aa7c044f4db613626b484e2fa89819929be20b46390bca00a99d6e`;
- reason code: `web_search_provider_chain`;
- protected file count: 62;
- candidate match: true;
- current files match: true;
- manifest integrity: v1–v39 all true.

## Exact parent delta

Changed:

- `minicode/tools/http_utils.py`

Added to protection:

- `minicode/tools/search_providers.py`
- `minicode/tools/web_search.py`

Removed: none.

`web_search.py` existed in the source tree but was not a v38 protected path,
so v39 truthfully records it as added to protection. The new provider module
is also added. Every v38 protected file outside `http_utils.py` retains its
v38 digest. Historical manifests v1–v38 and pins remain byte-identical; the
v38 manifest SHA is
`49f3319b06289ef23ab8c2f40bc3da0deaf443cb365f654cd2d1683a42b727f3`.

## Verification

The default verifier is read-only and reports:

- `activeBaselineId=memory-retrieval-production-v39`;
- `candidateMatches=true`;
- `currentFiles.matches=true`;
- 62/62 protected files;
- exact one-changed/two-added/zero-removed lineage;
- v1–v39 manifest integrity all true.

The v39 writer targets only the fixed `v39.json` path after validating the
historical chain. Controlled tamper tests report only the changed protected
path and never rewrite a manifest. Candidate output is deterministic across
working directory, HOME and hash seed.

The complete production-baseline and semantic test matrix passed 239 tests.
The official semantic evaluator passed with 108 cases, 37 confirmed gaps,
`phase3b_gate=true`, `remote_calls=0` and `evaluation_passed=true`. Both final
baseline checks remain green. After the decoded-target URL fix, the first
complete pytest suite passed with 3314 passed, 2 skipped and the same three
historical
benchmark-marker warnings. The second complete suite stopped with 2 unchanged
Phase 2A performance-evaluator failures, 3312 passed and 2 skipped: canonical
retrieval P95 was 5.269083 ms against its frozen 5.0 ms gate while a subsequent
system sample was 84.38% CPU idle. No baseline source, pin, threshold or gold
was changed, and no lucky-result rerun was performed.
The subsequent raw trailing-control guard passed the final baseline, scoped,
compatibility, wheel and static gates; the full suite was not rerun afterward.

The accepted semantic gold remained frozen at:

- SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
- size: 3,033,592 bytes;
- mtime_ns: `1784135857000000000`.
