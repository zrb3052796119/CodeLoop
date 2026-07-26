# Memory Retrieval Production Baseline v11

## Certification purpose

`memory-retrieval-production-v11` certifies the exact production source delta
needed by MiniCode Dashboard Batch 5C-2A. Its parent is the immutable
`memory-retrieval-production-v10`; v1 through v10 manifests and pins are not
rewritten.

The v11 manifest is:

```text
tests/fixtures/memory_retrieval_production_freeze/v11.json
SHA-256 c5d12d47e25db4ebd566f066420d398f7b04a53b518a407003784d8261371c71
```

It protects 23 production files. The exact v10 → v11 lineage is:

```text
changed
  minicode/headless.py
  minicode/mcp.py
  minicode/tooling.py

newly protected
  minicode/gateway.py
  minicode/mcp_current_state.py
  minicode/tools/__init__.py
  minicode/tools/task.py

removed
  none
```

Every declared delta uses the closed reason code
`mcp_current_state_observation`. The new module owns the safe contract and
registry; the other six files are the complete audited client and composition
call chain, including same-process nested Task clients.

## Historical and semantic invariants

The v10 manifest remains byte-identical at SHA-256
`bc94fe753ba0a30a5b74f9e3d242d9ede4395244fbdebb8f0d1e9992d992dbdb`.
The verifier checks every v1–v11 manifest pin, every parent lineage, the exact
current 23-file set, and deterministic v11 candidate equality.

The accepted 108-case semantic artifact remains independent of this source
lineage. Its required SHA-256 is
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`;
the accepted behavior projection and per-case fingerprints remain
`b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`
and `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.

## Read-only verification and controlled write

The default command is read-only:

```bash
python scripts/generate_memory_retrieval_production_baseline.py
```

Candidate output is deterministic across cwd, HOME, and hash seed:

```bash
python scripts/generate_memory_retrieval_production_baseline.py --print-v11
```

The only active writer target is the fixed v11 manifest path:

```bash
python scripts/generate_memory_retrieval_production_baseline.py --write-v11
```

Historical v9/v10 writer compatibility paths now validate and return their
pinned target without rewriting history. Controlled tamper tests modify copies
of `minicode/mcp_current_state.py` and `minicode/mcp.py`, require the verifier to
report exactly the modified path, and prove that v11 bytes and mtime are not
changed by verification.
