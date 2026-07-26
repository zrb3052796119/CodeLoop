# Memory Retrieval Production Baseline v23

Baseline ID: `memory-retrieval-production-v23`

Parent: `memory-retrieval-production-v22`

Reason: `Batch 8A-1.1 permission command review projection hardening`

Manifest SHA-256:
`c6cab0e867db309f9ddfbaf3034e269f4f65ce7b1c66e155997c0697b3388aa8`

## Exact v22 to v23 lineage

Changed:

- `minicode/permission_approval.py`

Added: none.

Removed: none.

The changed entry uses reason code
`gateway_permission_command_review_hardening`. The active manifest protects the
same 45 production sources as v22; it changes only the command-review projector
and strict UTF-8 budgeting implementation.

## Certification

- the deterministic candidate equals the accepted v23 manifest;
- all 45 current protected source hashes match;
- exact v22 to v23 changed/added/removed sets match;
- every v1 through v23 manifest-integrity pin is true;
- controlled modification of `permission_approval.py` reports exactly that
  file and cannot be accepted or written by the default verifier;
- the v22 manifest remains SHA-256
  `a47b1e5f203371e9ced01fed01e6df37947a2a0e891c1bee6c2ed43a51e59906`;
- every v1 through v22 manifest remains immutable;
- accepted semantic gold remains SHA-256
  `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`,
  size 3,033,592 bytes, with its accepted modification time unchanged;
- official semantic evaluation remains 108 cases, 37 confirmed gaps, zero
  remote calls, Phase 3B true, and pass.
- final complete suites pass 2,420 tests twice, with two skips and only the
  three pre-existing benchmark-marker warnings.

Default verification is read-only. Historical v22 candidate/writer entrypoints
now validate and return the pinned v22 evidence without reconstructing or
rewriting it. Only the explicit v23 writer targets the fixed v23 manifest.

Formal HTML, CSS, and JavaScript are outside the production delta and remain
byte-identical. The accepted semantic gold is never regenerated or overwritten.
