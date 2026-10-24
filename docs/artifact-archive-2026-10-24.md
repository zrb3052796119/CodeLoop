# Generated report archive — 2026-10-24

Four generated JSON reports have moved to a [manifest and SHA-256 inventory](../artifacts/archives/governance-2026-10-24/manifest.json), with their complete original bytes retained as small gzip files beside the manifest. No experiment was rerun and no result or historical hash was rewritten. The semantic-gap report still records `evaluation_passed: false`.

The originals total **9,854,538 bytes**; their compressed copies total **374,809 bytes**, saving **9,479,729 bytes** before the small manifest, documentation and restore utility. This reduces checkout/source-archive bulk. Git history is unchanged, so this is not a claim that full-clone history shrinks. Git LFS is not installed or configured in CI, so this change introduces no LFS dependency.

## Recover the original files offline

From the repository root, verify every archive without writing anything:

```sh
python scripts/restore_archived_artifacts.py
```

Restore exact bytes under a separate directory, preserving their original repository-relative paths:

```sh
python scripts/restore_archived_artifacts.py --restore --output-root /tmp/codeloop-report-export
```

To use the historical Phase 4 scripts/manifests at their existing paths, restore in place first:

```sh
python scripts/restore_archived_artifacts.py --restore
```

The utility checks compressed and original hashes/sizes before writing; it leaves matching existing files alone and refuses to replace a differing file. It rejects traversal paths and symlink parents that escape the selected destination. Recovery requires only Python standard-library gzip support: no Git history, LFS, network or provider calls. The manifest also records the original Git commit and blob as a second provenance route. Recovered reports are disposable local copies of the committed archives.

## Archived reports

| Original path (restore before opening) | Original bytes | Compressed bytes | Retained meaning |
| --- | ---: | ---: | --- |
| `artifacts/memory-retrieval-semantic-gap-evaluation.json` | 3,066,920 | 98,460 | 108 synthetic cases; failed evaluation; zero remote calls |
| `artifacts/intent-optimization-phase4-2026-09-07/offline-delivery.json` | 2,268,793 | 92,202 | 230 historical result rows and original source hashes |
| `artifacts/intent-optimization-phase4-2026-09-07/offline-all-final.json` | 2,264,068 | 92,105 | 230 historical result rows and original source hashes |
| `artifacts/intent-optimization-phase4-2026-09-07/offline-all-baseline.json` | 2,254,757 | 92,042 | 230 historical result rows and original source hashes |

The three Phase 4 reports remain members of their original evidence bundle. Its [original manifest](../artifacts/intent-optimization-phase4-2026-09-07/manifest.json), [results review](../artifacts/intent-optimization-phase4-2026-09-07/results-review.md), source snapshots and remaining inputs are unchanged; restored report bytes still satisfy the existing manifest hashes. No summary substitutes for those bytes.

`scripts/evaluate_memory_retrieval_semantic_gap.py` still writes new generated output to its established `artifacts/memory-retrieval-semantic-gap-evaluation.json` path. That output is distinct from the accepted baseline and from this archived historical report; restoration refuses to overwrite a newer generated output.

## Pinned test and replay inputs

The eight inputs previously retained in place have a separate [manifest](../artifacts/archives/governance-pinned-inputs-2026-10-24/manifest.json) and complete gzip copies. Their **46,227,033 original bytes** become **3,088,342 compressed bytes**, saving **43,138,691 bytes** before the small manifest/docs. Compression uses level 9, mtime 0 and an empty filename for deterministic bytes. The older archive manifest retains its historical retention decision; this manifest records the subsequent migration.

Before quality checks, pytest, or historical replay, restore the eight originals in each fresh checkout/source export:

```sh
python scripts/restore_archived_artifacts.py --manifest artifacts/archives/governance-pinned-inputs-2026-10-24/manifest.json --restore
```

CI performs this step before quality checks and pytest. The existing consumers still read ordinary files at their original paths; they do not read gzip transparently. Recovered originals are ignored, and every historical source/evaluator/input pin remains unchanged. Omit `--restore` for read-only validation; add `--output-root /tmp/codeloop-input-export` for external export. External export alone does not satisfy consumers with fixed repository paths.

| Original filename (link downloads gzip; restore before use) | Bytes | Dependency |
| --- | ---: | --- |
| [memory-retrieval-hybrid-offline.json](../artifacts/archives/governance-pinned-inputs-2026-10-24/1b2c1fea40fc2651f9e6674dbca1e1958af3681bd2fc30daba2ec6ed9d031fab.json.gz) | 18,058,648 | Read and schema/assertion checked by tests/test_memory_retrieval_hybrid_evaluator.py:203,216; CLI default output. |
| [memory-retrieval-hybrid-config.json](../artifacts/archives/governance-pinned-inputs-2026-10-24/bc95e02f3a6cd6bd69942ea5ad74efd8edbf58e3e78ef67faf30aa52c0b6c83c.json.gz) | 7,872,717 | Read by local evaluation default; SHA bound in hybrid-offline report and docs; not a runtime dependency. |
| [tasks.json](../artifacts/archives/governance-pinned-inputs-2026-10-24/4c7ea0bbfa2d809a0e90b4978ed30332df489436e8eee28c7b13723d5c91b737.json.gz) | 7,599,345 | Generic gen2 auditor requires tasks.json via registrationFiles (scripts/audit_multi_agent_gen2.py:101); SHA pinned in run-v2/manifest.json and source-manifest. Whole experiment pack must remain replayable. |
| [memory-retrieval-semantic-gap-baseline-v2.json](../artifacts/archives/governance-pinned-inputs-2026-10-24/4f1bb69ce1f79a3a91253c7a11078c0668fd25999d29dd39f8fef96b7687333f.json.gz) | 3,074,311 | Accepted evaluator artifact hash; tests read; default quality-v3 analysis baseline. |
| [memory-retrieval-semantic-gap-v2-candidate.json](../artifacts/archives/governance-pinned-inputs-2026-10-24/7c68c5e1d767c748a57b12e3972f43d64d11593d3e428a0a91f70234320b291f.json.gz) | 3,074,017 | Candidate evaluator artifact hash; tests read the immutable original. |
| [memory-retrieval-semantic-gap-baseline.json](../artifacts/archives/governance-pinned-inputs-2026-10-24/5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b.json.gz) | 3,033,592 | Accepted v1 SHA pins in semantic and hybrid evaluators; direct tests and many historical references. |
| [memory-retrieval-phase2a.json](../artifacts/archives/governance-pinned-inputs-2026-10-24/2f488120e4016d9fafb275cd2b22b7e978ddf8f4039b990aeff1724e00759327.json.gz) | 2,374,133 | PHASE2A_FROZEN_HASHES in evaluator and tests; phase2b consumes it. |
| [skill-embeddings.json](../artifacts/archives/governance-pinned-inputs-2026-10-24/9fa8872acf11ee3e45ff1c64ab27c2d25e0bfb9491a641481e8dfe6caded75e8.json.gz) | 1,140,270 | SHA bound in live-paired/settings.json. Historical paired_evaluator.py:158-170 copies and fingerprints this cache; retain as part of reproducible experiment archive. |

The original semantic/hybrid failures and candidate/accepted distinctions are preserved. Restoration checks compressed and original hashes/sizes, refuses a differing existing file, and needs no Git history, LFS, network or model. Historical replay of the four reports above needs its separate default-manifest restore command as well. Checkout/source archives become smaller; normal test/replay workspaces regain the original disk footprint after recovery, and existing Git history/full-clone cost is unchanged.

## Validation

The restore tests recover all four files without using Git, compare Phase 4 files with the unchanged historical manifest, preserve the failed semantic evaluation, reject corrupted compressed data, preflight existing-file conflicts, and reject path/symlink escapes. The source JSONs were compared byte-for-byte with commit `88ec62d0ca7d8b6b090c31fda1835642d7e0e603` before archiving.


## Pre-trial local experiment outputs

A further 458 previously untracked intermediate files (13,250,931 bytes) are
preserved in [pre-trial-evidence.tar.gz](../artifacts/archives/governance-2026-10-24/pre-trial-evidence.tar.gz)
(2,174,653 bytes). The [per-file manifest](../artifacts/archives/governance-2026-10-24/pre-trial-evidence-manifest.json)
records each original path, size and SHA-256, plus the archive hash. Every member
was read back and checked before the loose originals were moved to local recovery
storage. No failed run was replaced or discarded. The archive includes the
remaining intermediate outputs from the practicality, gen-2 and native-review
experiments whose documents cite those directories broadly.

The 86 directly referenced evidence files and required inputs are retained at
original paths; their [inventory](../artifacts/governance-trial-2026-10-24/retained-pre-trial-evidence.json)
is separate from this archive. This keeps normal tests and explicit document
references readable. Raw pytest logs retain their original whitespace.

To inspect the archived intermediates, verify the archive SHA-256 against its
manifest and extract into a new empty directory, preserving repository-relative
paths. The JSON restore helper above handles the four reports and eight pinned inputs
through their separate manifests, not this tar archive:

```sh
mkdir /tmp/codeloop-pre-trial-export
tar -xzf artifacts/archives/governance-2026-10-24/pre-trial-evidence.tar.gz -C /tmp/codeloop-pre-trial-export
```

Recovery needs no model calls or network access. Compare recovered file hashes
with the manifest before using them in historical replay; do not overwrite newer
outputs at the old paths.
