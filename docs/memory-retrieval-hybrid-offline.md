# Retrieval Phase 3B Offline Hybrid Report

## Decision

Phase 3B fails. The local embedding model closes most candidate-generation gaps, but the frozen Semantic Gate does not separate correct semantic matches from hard negatives. Production enablement and real-user shadow are both prohibited.

This was an offline synthetic pressure test. No `minicode/` production path, formal memory, session history, counter, feedback, approval audit, pending memory, Markdown memory, or vector memory was written.

## Frozen Inputs

- Phase 3B holdout: 60 cases, 36 positive, 24 hard negative, plus 64 unrelated background memories.
- Holdout freeze manifest SHA-256: `42c23499cc3c622a3280a2fba6528bf8d0471a54f7f6b6abaa4e2fe10e8a1a73`.
- Frozen configuration payload SHA-256: `3440fd98e1fa37d861d4baeabfe723015b8a06d5cbf48bfa1971ec48a8a19c5a`.
- Frozen configuration file SHA-256: `bc95e02f3a6cd6bd69942ea5ad74efd8edbf58e3e78ef67faf30aa52c0b6c83c`.
- Calibration used only 72 Phase 3A analysis cases. It records 2,880 attempted configurations and four deterministic folds. No sealed or Phase 3B holdout case ID appears in calibration inputs.

## Model

- Adapter: `LocalEmbeddingAdapter`, ONNX Runtime, CPU, normalized 384-dimensional vectors.
- Model: `Xenova/multilingual-e5-small`.
- Revision: `761b726dd34fb83930e26aab4e9ac3899aa1fa78`.
- Model fingerprint: `cb55c8134bf02eeff414a6fcb53a88e5160e45cf74e7a7cf1befbc5a9fa2b230`.
- License: MIT.
- Runtime: NumPy 2.3.1, ONNX Runtime 1.22.1, Tokenizers 0.21.2, CPU.
- `trust_remote_code=false`; remote inference calls: 0.

The adapter never downloads during construction. The model download is available only through the explicit `download-model --allow-model-download` CLI mode. The standard pytest suite uses only `DeterministicFakeEmbeddingAdapter`.

## Representation And Index

The selected `structured-v1` representation includes only redacted `content`, `category`, `tags`, `domains`, allowlisted file paths, and a bounded source type. It excludes provenance, approval audit, environment, authorization, system prompts, sessions, traces, history, rejected/pending content, and formal user memory.

Index eligibility requires approved, active, safe, unlocked, non-archival entries in a visible USER/PROJECT/LOCAL scope. Records bind entry ID, content hash, approval/lifecycle hash, model ID and revision, model fingerprint, representation version and hash, dimension, and normalized vector.

Tests cover content and metadata updates, approval/reject/restore, lifecycle and curator lock changes, entry and scope deletion, model and representation revision invalidation, duplicate IDs, corrupt and stale cache, atomic-write interruption, symlink/traversal rejection, concurrent read/update, and deterministic tie-breaking.

## Frozen Configuration

- Representation: `structured-v1`.
- Fusion: RRF, lexical top-K 20, dense top-K 20, `rrf_k=20`.
- Semantic Gate: dense threshold 0.89, top-1 margin 0.03, no structured bonus, maximum one accepted item, rank limit 20.
- Lexical override threshold 1.0 effectively disables lexical override.

## Decision Results

| Split | Lexical Recall@20 | Final Recall@20 | Post-Gate recall | Rendered recall | Precision | Hard-negative rendered |
|---|---:|---:|---:|---:|---:|---:|
| Phase 3A sealed | 20.83% | 91.67% | 4.17% | 4.17% | 3.57% | 58.33% |
| Phase 3B holdout | 52.78% | 91.67% | 19.44% | 19.44% | 17.50% | 54.17% |

Candidate recall gains are +70.84 and +38.89 percentage points, so embeddings solve much of candidate generation. They do not solve relevance classification.

On labeled primary/excluded items, the frozen Gate confusion counts are:

| Split | True positive | False negative | False positive | True negative |
|---|---:|---:|---:|---:|
| Phase 3A analysis | 12 | 36 | 9 | 15 |
| Phase 3A sealed | 1 | 23 | 7 | 5 |
| Phase 3B holdout | 7 | 29 | 13 | 11 |

Safety/lifecycle leakage, incorrect consolidation suppression, duplicate render, unresolved-conflict unsafe render, and rendered/recorded/feedback ID disagreement are all zero on both decision splits.

## Interpretation

The selected bi-encoder score and top-1 margin are not calibrated across languages and semantic relation types. In sealed, 21 of 24 positives reach fused top 20 and are then removed by the Gate. In holdout, 26 of 36 positives are removed by the Gate. At the same time, semantically close contradictions, wrong roots, wrong objects, and ambiguous queries remain above the threshold.

The full machine-readable results, per-case stage states, Wilson intervals, dimensions, model manifest, and performance data are in `artifacts/memory-retrieval-hybrid-offline.json`.

## Allowed Next Step

Do not connect this prototype to `MemoryPipeline`, agent loop, TUI, headless, context compactor, or formal `MemoryManager`. Do not run real-user shadow. A next research iteration requires a new version, a new independent holdout, and a stronger globally explainable semantic discrimination design; the current holdout cannot be reused for tuning.

## Integrity Note

All 142 production files recorded at stage start are byte/size/mtime identical. Phase 1, 2A, 2B, and 3A frozen sets (15/8/12/27 files) are identical. The complete 864-file formal `~/.mini-code` tree is identical.

The production directory's exact file set is not identical because six unrelated `minicode/web/dashboard_prototype/` files were created concurrently after the stage snapshot. They were not created, edited, deleted, or incorporated by this Phase 3B work. They are retained as external user work.
