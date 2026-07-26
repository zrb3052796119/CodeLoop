# Memory Retrieval Production Baseline v28

## Identity

- Baseline: `memory-retrieval-production-v28`
- Parent: `memory-retrieval-production-v27`
- Reason code: `workspace_local_diff_review_normalization`
- Manifest: `tests/fixtures/memory_retrieval_production_freeze/v28.json`
- Manifest SHA-256: `75c71d1d740b35f530965d7f797f4bbe3ceafb019129be3ee4d73d9256b453e5`
- Protected production files: 50/50

The v27 manifest remains byte-identical at SHA-256
`18ad99488f7a73e71bbe30011d9c86a8de6ab077b5d1be8790718c6ffac14013`.

## Exact v27 to v28 lineage

Changed:

- `minicode/file_review.py`
- `minicode/permission_approval.py`

Added: none.

Removed: none.

`file_review.py` now derives every public unified-Diff label from the resolved
target relative to the resolved Workspace. `permission_approval.py` validates
that the two Diff headers exactly match the projected relative target and keeps
actual sensitive, absolute-path, private-key, unsafe-control, redacted, or
truncated Diff bodies deny-only.

The second production file is intentional. Deterministic RED tests proved that
producer-only header normalization did not classify unrelated absolute paths,
ANSI/control bytes, or private-key material in the Diff body. The projector
change closes that boundary without rewriting the body or broadening Allow.

## Certification contract

The default verifier is read-only. Its result reports:

- `activeBaselineId=memory-retrieval-production-v28`
- `matches=true`
- `candidateMatches=true`
- `currentFiles.matches=true`
- 50 protected files with no mismatches
- exact changed/added/removed lineage above
- `manifestIntegrity=true` for every manifest from v1 through v28

Parameterized tamper tests change each v28 production file independently and
prove an exact `currentFiles.mismatches` entry, `candidateMatches=false`, and no
manifest rewrite. Candidate output is deterministic across CWD, HOME, and hash
seed. The explicit writer can write only the fixed v28 target and preserves the
v27 bytes and mtime.

## Frozen semantic evidence

The accepted semantic gold remains byte-identical:

- SHA-256: `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`
- Size: 3,033,592 bytes
- mtime_ns: 1,784,135,857,000,000,000

The official evaluator passed 108 cases with 37 confirmed gaps,
`phase3b_gate=true`, and zero remote calls. It did not rewrite the accepted
gold.

## Verification

- Baseline and semantic evaluator tests: 179 passed.
- Default v28 verifier: passed with 50/50 current files.
- Official semantic evaluator: passed.
- Final complete suites: 2,572 passed, 2 skipped, and three pre-existing
  unregistered benchmark-marker warnings, twice.

