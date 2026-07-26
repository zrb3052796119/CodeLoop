# Memory Retrieval Production Baseline v29

## Identity

- Baseline: `memory-retrieval-production-v29`
- Parent: `memory-retrieval-production-v28`
- Reason code: `invisible_control_diff_fidelity_hardening`
- Manifest: `tests/fixtures/memory_retrieval_production_freeze/v29.json`
- Manifest SHA-256: `e43777832841629549d180e039d40ac54209c5f15a3581e9bdf09b308592d4d1`
- Protected production files: 50/50

The v28 manifest remains byte-identical at SHA-256
`75c71d1d740b35f530965d7f797f4bbe3ceafb019129be3ee4d73d9256b453e5`.

## Exact v28 to v29 lineage

Changed:

- `minicode/file_review.py`
- `minicode/permission_approval.py`

Added: none.

Removed: none.

`file_review.py` now classifies both real file bodies before any line splitting.
C0/C1 controls other than tab/LF and CR used only as CRLF, the explicit Unicode
format ranges, line/paragraph separators, BOM, and surrogate code points return
one fixed sensitive-review marker. `permission_approval.py` applies the same
classifier before parsing a supplied Diff and again to projected values. This
keeps the producer and projector fail-closed without sending raw unsafe content
to the TUI, broker, HTTP payload, event stream, log, or RunJournal.

## Certification contract

The default verifier is read-only and reports:

- `activeBaselineId=memory-retrieval-production-v29`
- `matches=true`
- `candidateMatches=true`
- `currentFiles.matches=true`
- 50 protected files with no mismatches
- the exact two-changed, zero-added, zero-removed lineage above
- `manifestIntegrity=true` for every manifest from v1 through v29

Parameterized tamper tests independently modify each v29 production file and
prove an exact current-file mismatch without rewriting any manifest. Candidate
output is deterministic across CWD, HOME, and hash seed. The explicit writer is
pinned to v29 and leaves v1-v28 bytes and mtimes unchanged.

## Frozen semantic evidence

The accepted semantic gold remains byte/stat identical before and after the
official evaluator:

- SHA-256: `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`
- Size: 3,033,592 bytes
- mtime_ns: 1,784,135,857,000,000,000

The evaluator passed 108 cases with 37 confirmed gaps,
`phase3b_gate=true`, and zero remote calls. It did not rewrite the accepted
gold.

## Verification

- Baseline and semantic-evaluator tests: 181 passed.
- Default v29 verifier: passed with 50/50 current files.
- Official semantic evaluator: passed.
- Final complete suites: 2,773 passed, 2 skipped, and three pre-existing
  unregistered benchmark-marker warnings, twice.

