# Memory Retrieval Production Baseline v10

`memory-retrieval-production-v10` is the active production-source baseline for Batch 5C-1A MCP Runtime Observation.

## Parent

- Parent baseline: `memory-retrieval-production-v9`
- Reason code: `mcp_runtime_observation`
- Manifest SHA-256: `bc94fe753ba0a30a5b74f9e3d242d9ede4395244fbdebb8f0d1e9992d992dbdb`

## v9 → v10 delta

Changed protected files:

- `minicode/agent_loop.py`
- `minicode/run_journal.py`

Newly protected files:

- `minicode/mcp.py`
- `minicode/mcp_event_contract.py`
- `minicode/mcp_observation.py`
- `minicode/tooling.py`

Removed protected files: none.

## Certification

The default verifier reports:

```text
active baseline = memory-retrieval-production-v10
candidate matches = true
v1–v10 manifest integrity = true
all current protected files match = true
protected file count = 19
```

The v1–v9 pinned manifests remain byte-stable and v10 is the only new manifest. The protected delta is limited to the run-scoped MCP runtime observation seam and RunJournal validation; Memory Retrieval algorithms, Context/WorkingMemory algorithms, pricing/cost truth, Session behavior, Gateway behavior, and MCP business results are not changed by this baseline.
