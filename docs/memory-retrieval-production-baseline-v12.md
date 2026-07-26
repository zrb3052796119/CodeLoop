# Memory Retrieval Production Baseline v12

## Certification purpose

`memory-retrieval-production-v12` certifies the exact protected source delta for
MiniCode Dashboard Batch 5C-2B. Its immutable parent is
`memory-retrieval-production-v11`; v1 through v11 manifests and pins are not
rewritten.

The v12 manifest is:

```text
tests/fixtures/memory_retrieval_production_freeze/v12.json
SHA-256 a8fba6ed9134b465167525f4b8c81de2369363ad0527f6368527de0369bd05a7
```

It protects the same 23 production files as v11. The exact v11 → v12 lineage is:

```text
changed
  minicode/gateway.py

newly protected
  none

removed
  none
```

The only reason code is `mcp_current_state_projection`. The Gateway change creates
one registry and injects that same instance into both the existing `POST /run`
composition and the new request-time read loader.

The new `minicode/web/mcp_current_projection.py`, Dashboard read model, static
assets, and tests are intentionally not added to the Memory Retrieval production
freeze. v12 only certifies the already-protected Gateway call-chain change; it
does not broaden the historical Retrieval boundary.

## Historical and semantic invariants

The v11 manifest remains byte-identical at SHA-256
`c5d12d47e25db4ebd566f066420d398f7b04a53b518a407003784d8261371c71`.
The verifier checks every v1–v12 pin, every parent lineage, exact current 23-file
hashes, and deterministic v12 candidate equality.

The accepted 108-case semantic artifact remains immutable at SHA-256
`5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`.
The accepted behavior projection and per-case fingerprints remain
`b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60`
and `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.

## Read-only verification and controlled write

The default verifier is read-only:

```bash
python scripts/generate_memory_retrieval_production_baseline.py
```

The deterministic candidate command is:

```bash
python scripts/generate_memory_retrieval_production_baseline.py --print-v12
```

The only v12 writer target is:

```bash
python scripts/generate_memory_retrieval_production_baseline.py --write-v12
```

Controlled tamper verification changes only a copied `minicode/gateway.py`,
requires that exact mismatch, and proves the copied v12 manifest bytes and mtime
are not rewritten. Candidate generation is tested across cwd, HOME, and hash
seed; manifests contain no machine path, timestamp metadata, wildcard, or secret.

## Final verification evidence

The final read-only verifier reported `candidateMatches=true`, 23/23 current
files, no mismatches, and every v1-v12 integrity flag true. The exact lineage
remains one changed protected file (`minicode/gateway.py`) with no additions or
removals. Controlled tamper and fixed-target tests also prove that immutable v11
cannot be reconstructed or rewritten from v12 source state.

The final certification sequence was full regression → v12 verifier → official
semantic evaluator → gold SHA/mtime/size comparison → full regression. Both full
runs passed `1970 passed, 2 skipped`; the evaluator passed 108 cases with 37
confirmed gaps and zero remote calls. The accepted artifact, behavior projection,
and per-case fingerprints remained unchanged.
