# Memory Retrieval Production Baseline v2 Certification

## Certification decision

`memory-retrieval-production-v2` is certified as the active production-source baseline. It is the child of `memory-retrieval-production-v1` and accepts only the Batch 3B-1 lifecycle observer at three top-level execution entrypoints.

This certification is not acceptance of a Memory Retrieval algorithm change. Retrieval, Gate, candidate consolidation, controller, injection, counters, feedback, Agent Loop, Memory modules, and the frozen semantic dataset remain semantically unchanged. Historical Dashboard runs were not backfilled.

Any later protected production-source change requires a new explicit baseline and the same semantic re-certification; v2 must not be silently rewritten.

## Original failure and classification

The pre-certification semantic-gap suite produced `25 passed, 2 failed`. Both failures came from the evaluator applying the historical v1 production hashes to the current Batch 3B-1 entrypoints.

The exact v1 mismatch set was:

- `minicode/headless.py`
- `minicode/main.py`
- `minicode/tui/input_handler.py`

No fourth mismatch existed. The other seven v1 production files, all Phase 1 files, all Phase 2A files, all Phase 2B files, and all 18 frozen semantic-gap dataset files matched their recorded hashes.

## Evidence and audit boundary

Direct evidence:

- `agent_loop.py` and every protected Memory implementation file retain their v1 hashes.
- Current entrypoint source places `observe_run()` around the existing top-level Agent call and preserves the Agent call arguments.
- Headless uses the source override, classic CLI uses `source=tui` with no invented Session ID, and TTY uses `source=tui` with `state.session.session_id`.
- Lifecycle equivalence tests compare disabled, healthy, and failing Journal modes for return/messages, original exceptions, permission cleanup, tool disposal, Session/context state, and TTY completion.
- The current 108-case semantic behavior equals the accepted v1 artifact exactly under the deterministic projection described below.

The workspace retains v1 hashes but no v1 source-body backup. A historical line-by-line source diff therefore cannot be reconstructed. The lifecycle-only conclusion combines current-source inspection, the Batch 3B-1 audit record, unchanged downstream source hashes, entrypoint behavior-equivalence tests, and exact semantic behavior equivalence. It does not claim unavailable textual evidence.

## Versioned manifest contract

The manifests are deliberately outside the already frozen semantic-gap dataset:

- `tests/fixtures/memory_retrieval_production_freeze/v1.json`
- `tests/fixtures/memory_retrieval_production_freeze/v2.json`

Both use a closed deterministic contract:

- `schemaVersion`
- `baselineId`
- `parentBaselineId`
- `reason`
- `files`
- `allowedChangesFromParent`
- `addedFiles`

Manifest SHA-256 values:

- v1: `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`
- v2: `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`

The verifier pins both manifest files, validates their closed schema, rejects wildcard/absolute/traversing paths, restricts reason codes, compares lineage, builds an independent candidate, and then hashes every active v2 source file.

## v1 to v2 lineage

The common-file changed set is exactly:

| File | Reason code |
|---|---|
| `minicode/headless.py` | `lifecycle_observer_entrypoint` |
| `minicode/main.py` | `lifecycle_observer_entrypoint` |
| `minicode/tui/input_handler.py` | `lifecycle_observer_entrypoint` |

All other v1 file hashes are identical in v2. No v1 file was removed.

The following v2-only dependencies are recorded separately:

| File | Reason code |
|---|---|
| `minicode/run_lifecycle.py` | `lifecycle_observer_dependency` |
| `minicode/run_journal.py` | `lifecycle_observer_dependency` |

They are protected because they are direct dependencies on the entrypoint-to-Agent path and can influence whether execution is reached. They are not represented as files that existed in v1. Gateway composition and TTY state types remain outside the historical Memory Retrieval production set: Gateway delegates into protected Headless, while state types do not invoke retrieval or Agent execution.

## Deterministic generator and verifier

Default verification is read-only:

```bash
python3 scripts/generate_memory_retrieval_production_baseline.py
```

Candidate inspection is also read-only:

```bash
python3 scripts/generate_memory_retrieval_production_baseline.py --print-v2
```

The only write option targets the fixed v2 manifest path and first requires the exact declared v1 difference set:

```bash
python3 scripts/generate_memory_retrieval_production_baseline.py --write-v2
```

There is no arbitrary output-path option. Candidate output is UTF-8, sorted deterministic JSON and contains no current time, process metadata, machine path, or user environment data. Two runs with different working directories, isolated user directories, and Python hash seeds produced identical bytes.

## Semantic behavior equivalence

The accepted gold remains `artifacts/memory-retrieval-semantic-gap-baseline.json`, pinned at SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`.

The full deterministic equivalence projection has SHA-256 `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60` for both accepted v1 and current v2. It contains:

- dataset ID, counts, analysis/sealed split, categories, and freeze state;
- all three retrieval arms and their deterministic aggregate metrics;
- every case's candidate IDs/ranks and deterministic scores;
- post-Gate, post-consolidation, rendered, suppressed, injection-counter, feedback, controller, and first-loss fields;
- overall and sealed Recall@1/3/5/10/20, MRR, NDCG, downstream recall/precision, and hard-negative rates;
- forbidden and lifecycle/safety leakage counts;
- semantic-gap adjudication and confirmed case IDs;
- counter disagreement, save/counter semantics, diagnostic side effects, and remote-call count;
- the frozen Phase 2B regression projection.

Latency, performance samples, temporary paths, process data, formal-tree timestamps, and current execution metadata are excluded. The existing deterministic per-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.

Certification results:

- dataset: 108 cases, 72 positive, 36 hard negative, 72 analysis, 36 sealed; freeze matched;
- retrieval arms: manager global search, canonical diagnostic, and canonical production matched v1;
- remote calls: 0;
- diagnostic counter side-effect cases: 0;
- diagnostic filesystem side-effect cases: 0;
- formal state before and after evaluation: equal;
- Phase 1, Phase 2A, and Phase 2B freezes: matched.

## Tamper and regression protection

A controlled temporary-copy mutation of `minicode/memory_retrieval.py` caused v2 verification to fail and report only that path. The verifier did not rewrite or accept the manifest. Tests also prove that an undeclared fourth v1 difference prevents candidate certification, v1 evidence is pinned, and wildcard reason declarations are rejected.

Final re-certification:

- baseline plus semantic-gap certification: 39 passed;
- lifecycle entrypoints: 34 passed;
- RunJournal plus Dashboard Runs: 29 passed;
- requested Memory Retrieval matrix: 137 passed;
- complete suite: 1619 passed, 2 skipped, 0 failed, with the three pre-existing benchmark-marker warnings;
- touched-file Ruff, Python compilation, full compileall, manifest parsing, deterministic generation, and safety scan: passed;
- third-party runtime dependency additions: none.

Batch 3B-1.1 changed only manifests, evaluator/certification tooling, tests, and documentation. It did not modify production Agent, Memory, lifecycle, RunJournal, Dashboard, Gateway, Headless, CLI, or TTY logic.
