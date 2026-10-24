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

## Explicit retained exceptions

The other eight JSON files above 1,000,000 bytes remain in place (**46,227,033 bytes**). Removing them would break current tests, frozen certification/replay input, or a digest-bound historical evaluator. This change does not weaken those contracts.

| Retained path | Bytes | Dependency |
| --- | ---: | --- |
| [memory-retrieval-hybrid-offline.json](../artifacts/memory-retrieval-hybrid-offline.json) | 18,058,648 | Read and schema/assertion checked by tests/test_memory_retrieval_hybrid_evaluator.py:203,216; CLI default output. |
| [memory-retrieval-hybrid-config.json](../artifacts/memory-retrieval-hybrid-config.json) | 7,872,717 | Read by local evaluation default; SHA bound in hybrid-offline report and docs; not a runtime dependency. |
| [tasks.json](../artifacts/multi-agent-gen2-2026-10-23/run-v2/tasks.json) | 7,599,345 | Generic gen2 auditor requires tasks.json via registrationFiles (scripts/audit_multi_agent_gen2.py:101); SHA pinned in run-v2/manifest.json and source-manifest. Whole experiment pack must remain replayable. |
| [memory-retrieval-semantic-gap-baseline-v2.json](../artifacts/memory-retrieval-semantic-gap-baseline-v2.json) | 3,074,311 | Accepted evaluator artifact hash; tests read; default quality-v3 analysis baseline. |
| [memory-retrieval-semantic-gap-v2-candidate.json](../artifacts/memory-retrieval-semantic-gap-v2-candidate.json) | 3,074,017 | Candidate evaluator artifact hash; tests read the immutable original. |
| [memory-retrieval-semantic-gap-baseline.json](../artifacts/memory-retrieval-semantic-gap-baseline.json) | 3,033,592 | Accepted v1 SHA pins in semantic and hybrid evaluators; direct tests and many historical references. |
| [memory-retrieval-phase2a.json](../artifacts/memory-retrieval-phase2a.json) | 2,374,133 | PHASE2A_FROZEN_HASHES in evaluator and tests; phase2b consumes it. |
| [skill-embeddings.json](../artifacts/skill-selection-model-vs-routed-2026-09-08/live-paired/skill-embeddings.json) | 1,140,270 | SHA bound in live-paired/settings.json. Historical paired_evaluator.py:158-170 copies and fingerprints this cache; retain as part of reproducible experiment archive. |

Inspection covered direct references in production, scripts, tests, documentation and the relevant historical evidence bundles. It cannot prove the absence of arbitrary dynamic consumers; recovery at the original paths is provided for that reason. The manifest preserves this decision per file.

## Validation

The restore tests recover all four files without using Git, compare Phase 4 files with the unchanged historical manifest, preserve the failed semantic evaluation, reject corrupted compressed data, preflight existing-file conflicts, and reject path/symlink escapes. The source JSONs were compared byte-for-byte with commit `88ec62d0ca7d8b6b090c31fda1835642d7e0e603` before archiving.
