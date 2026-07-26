# Memory Retrieval Production Baseline v35

## Certification identity

- Baseline: `memory-retrieval-production-v35`
- Parent: `memory-retrieval-production-v34`
- Manifest SHA-256:
  `bc2f16ee8f19dc7d59b878e35324486acd0cd110f16602ed722d3f4163572fc4`
- Protected sources: 56
- Added sources: none
- Removed sources: none
- Reason code: `dashboard_agent_observatory_core_pages`

The v35 manifest is the Batch 9D-1B Agent Observatory and core-page visual
certification boundary. It certifies presentation changes only: no Python
runtime source, Store, REST/SSE schema, action authority, evaluator threshold,
dependency or accepted semantic truth changed.

## Exact v34 to v35 production delta

| Path | v35 SHA-256 |
| --- | --- |
| `minicode/web/static/index.html` | `49c991efa9b10344a7272113a2177f9b64929d1b73fbf84b595153dd0d44a38b` |
| `minicode/web/static/assets/styles.css` | `bc9b13b94354650ad549c8c96f8285984ba4d6d48f914de7c737f04d19686255` |
| `minicode/web/static/assets/app.js` | `ec43c62349dca520cd9e4ce5c42bba16638dc2e7d230431b43bcbf45cc3fa001` |

`minicode/web/static/assets/cost-format.js` remains byte-identical at
`194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.

## Historical immutability and active verification

- v34 remains pinned at
  `3136e096a97192de5078882523106f5179cb20a3e9885c050fd187038f815cbb`.
- All v1 through v34 manifest bytes and pins remain unchanged.
- The historical v34 builder and writer now validate and return the immutable
  pinned target; only the v35 writer can create the new fixed target.
- `manifestIntegrity` is true for all 35 manifests.
- `candidateMatches=true`.
- `currentFiles.matches=true`.
- `currentFiles.fileCount=56`.
- Both active `lineage` and retained `visualShellLineage` report the expected
  three changed, zero added and zero removed files.

## Accepted semantic truth

The accepted semantic gold remains byte- and metadata-identical:

- SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`
- Size: `3033592`
- mtime_ns: `1784135857000000000`

The official offline evaluator completed 108 cases, retained 37 confirmed gaps,
passed the Phase 3B gate, made zero remote calls and reported
`evaluation_passed=true`.

## Release boundary

v35 certifies only Batch 9D-1B. Batch 9A-2, 9A-3, 9B and 9C remain deferred by
the user. Batch 9D-1C is the next bounded visual task; Batch 9D-2 must not be
described as full release certification while the deferred hardening stages
remain incomplete.
