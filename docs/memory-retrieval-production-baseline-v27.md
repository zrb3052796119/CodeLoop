# Memory Retrieval Production Baseline v27

## Identity

```text
baselineId: memory-retrieval-production-v27
parentBaselineId: memory-retrieval-production-v26
manifest SHA-256: 18ad99488f7a73e71bbe30011d9c86a8de6ab077b5d1be8790718c6ffac14013
protected files: 50
reasonCode: memory_approval_read_only_hardening
```

v26 remains byte-identical with SHA-256
`b44abf36befb98723b26036530296f8675a0d92ae59884956767b352445ed936`
and size 6,328 bytes.  Every earlier manifest and integrity pin remains
unchanged.

## Exact v26 to v27 lineage

Changed protected files:

```text
minicode/memory_approval.py
```

Added files: none.  Removed files: none.  The protected set remains 50 files.
The protected production module SHA-256 is
`38a499f1b1d345dbc4fa89466027a0e4be518124affeddfc539bae1117125477`.

## Protected behavior

v27 adds certification for:

- no-write `MemoryApprovalAuthority.snapshot()` and `revision()`;
- no-write real pending-approval GET for empty, current, and legacy stores;
- deterministic in-memory legacy approval interpretation;
- fail-closed corrupt, duplicate, hash-mismatched, audit-invalid, symlink, and
  non-regular source handling without recovery output;
- bounded directory-relative regular-file reads with replacement checks;
- GET-to-POST review-revision compatibility; and
- unchanged RLock/flock/reload/validate/audit/atomic-save decision authority.

The default verifier is read-only and requires candidate equality, current
50-file equality, exact v26-to-v27 lineage, and all v1-to-v27 manifest pins.
Dedicated tamper tests change the newly protected approval module and prove an
exact verifier mismatch.  The fixed writer targets only v27; historical writer
functions remain immutable validators.

## Semantic and frontend certification

The official evaluator remains the frozen 108-case offline authority and
reports 37 confirmed gaps, Phase 3B true, zero remote calls, and
`evaluation_passed=true`.

Accepted semantic gold identity before and after evaluation:

```text
SHA-256: 5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b
size: 3033592
mtime_ns: 1784135857000000000
```

Formal frontend identities before and after v27:

```text
minicode/web/static/index.html
  43432f8ab17c26ffb36c0d822bcf7b3181dc0d38e41c620dd1dcb0686116ae0b
minicode/web/static/assets/app.js
  1508700d7d75d99f6a5c166172c89f761e81100bc6d89f6b2873731c1e747ccb
minicode/web/static/assets/styles.css
  092dd3279f613f802a050276db833d386c30663e6277fb5152597d966149d3e8
minicode/web/static/assets/cost-format.js
  194e6b99cc409c9dede90a2c92dea23a75286b0794ef50b94987a3f8c4fd2916
```

No semantic gold, behavior projection, per-case fingerprint, frontend asset,
or historical production manifest is resigned by v27.
