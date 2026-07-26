# Memory Retrieval Production Baseline v13

## Certification purpose

`memory-retrieval-production-v13` certifies the exact protected source delta for
MiniCode Dashboard Batch 5C-2B.1. Its immutable parent is
`memory-retrieval-production-v12`; all v1-v12 manifests and pins remain
byte-identical.

The v13 manifest is:

```text
tests/fixtures/memory_retrieval_production_freeze/v13.json
SHA-256 ef295a3aa3dcfc522d4cc421310434de3013772122f3b913b6b137144a96fc2c
```

It protects the same 23 production files as v12. The exact lineage is:

```text
changed
  minicode/gateway.py
  minicode/mcp_current_state.py

newly protected
  none

removed
  none
```

Both changed files use reason code
`mcp_current_state_workspace_isolation`. The Registry owns pre-probe scoped
selection; the Gateway captures the same Registry in the scoped Dashboard loader
and continues to provide that object to POST `/run`.

`minicode/web/mcp_current_projection.py` remains outside the historical Memory
Retrieval protected set. v13 certifies the already-protected current-state
Registry/Gateway call chain and does not broaden Retrieval algorithm acceptance.

## Immutable history and deterministic writer

The v12 manifest remains SHA-256
`a8fba6ed9134b465167525f4b8c81de2369363ad0527f6368527de0369bd05a7`.
The v12 builder returns the pinned historical target when present and the v12
writer only validates that fixed target; neither can reconstruct or rewrite v12
from v13 source state.

The default verifier is read-only:

```bash
python scripts/generate_memory_retrieval_production_baseline.py
```

The deterministic v13 candidate and sole v13 writer are:

```bash
python scripts/generate_memory_retrieval_production_baseline.py --print-v13
python scripts/generate_memory_retrieval_production_baseline.py --write-v13
```

Candidate output is identical across cwd, HOME, and hash seed. Controlled tamper
tests independently modify copied `minicode/gateway.py` and
`minicode/mcp_current_state.py`; each reports only that path and does not rewrite
the copied v13 manifest. The active verifier reports candidate equality, 23/23
current files, exact two-file lineage, and true integrity for every v1-v13 pin.

## Semantic and regression evidence

The accepted semantic artifact remains immutable at SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
size 3033592, and mtime ns `1784135857000000000`. The official evaluator passed
108 cases with 37 confirmed gaps, zero remote calls, evaluation true, and Phase
3B true. The accepted behavior projection and per-case fingerprints remain
unchanged.

The final certification sequence was full regression → v13 verifier → official
evaluator → gold metadata comparison → full regression. The two full runs passed
`1985 passed, 2 skipped, 3 warnings` in 84.00s and 82.44s; only the three existing
benchmark marker warnings remain.
