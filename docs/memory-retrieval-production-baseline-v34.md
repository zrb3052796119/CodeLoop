# Memory Retrieval Production Baseline v34

## Certification identity

- Baseline: `memory-retrieval-production-v34`
- Parent: `memory-retrieval-production-v33`
- Manifest SHA-256:
  `3136e096a97192de5078882523106f5179cb20a3e9885c050fd187038f815cbb`
- Protected sources: 56
- Added sources: none
- Removed sources: none
- Reason code: `dashboard_waku_visual_shell`

The v34 manifest is the Batch 9D-1A visual-system and three-column Shell
certification boundary. It does not certify any page-internal redesign or new
Dashboard business behavior.

## Exact v33 to v34 production delta

| Path | v34 SHA-256 |
| --- | --- |
| `minicode/web/static/index.html` | `d00d29b0df3cd2f284a524edef6ad7f5a22e541aa2c9a2740ddc1ea907b01afa` |
| `minicode/web/static/assets/styles.css` | `59eb5cab22b6a705ce2fee135635552b3acbc5d39f72d661e774d8c2a8ed1ed4` |
| `minicode/web/static/assets/app.js` | `5082899135487a2722830d365df8107119788ab3745ad01bc783840c80b3b91f` |

`minicode/web/static/assets/cost-format.js` remains byte-identical at
`194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916`.
No Python runtime source, REST/SSE schema, Store, evaluator, threshold or
dependency entered the v33 to v34 production delta.

## Historical immutability and active verification

- v33 remains pinned at
  `a5a6c84205d68c6c30f85724f1091d06593cf203dc8390514731d1b65e995313`.
- All v1 through v33 manifest bytes and pins remain unchanged.
- `manifestIntegrity` is true for all 34 manifests.
- `candidateMatches=true`.
- `currentFiles.matches=true`.
- `currentFiles.fileCount=56`.
- The verifier reports exactly three changed, zero added and zero removed files.

The historical v33 writer now validates and returns the immutable pinned target;
the v34 writer is the only writer that may create the new fixed target after all
historical pins validate.

## Accepted semantic truth

The accepted semantic gold remains byte- and metadata-identical:

- SHA-256:
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`
- Size: `3033592`
- mtime_ns: `1784135857000000000`

The official offline evaluator completed 108 cases, retained 37 confirmed gaps,
passed the Phase 3B gate, made zero remote calls and reported
`evaluation_passed=true`. Default Phase 2B remained `28 passed`.

## Release boundary

v34 certifies only Batch 9D-1A. Batch 9A-2, 9A-3, 9B and 9C are deferred by
user, so this evidence must not be described as complete release hardening.
Batch 9D-2 may later establish a Dashboard Visual Release Candidate until those
deferred stages resume.
