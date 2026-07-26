# Memory Retrieval Production Baseline v3 Certification

## Certification decision

`memory-retrieval-production-v3` is certified as the active production-source baseline. It is the child of the immutable `memory-retrieval-production-v2` manifest and accepts only Batch 3B-2A callback-based Tool/Assistant Run observation changes.

This is not acceptance of a Memory Retrieval algorithm change. Agent Loop, RunJournal, Memory storage, retrieval, Gate, candidate consolidation, controller, injection, counters, feedback, context compaction, and the frozen 108-case dataset remain unchanged. Assistant bodies and Tool input/output are not persisted.

## Immutable evidence and lineage

Manifest SHA-256 values:

- v1: `b5434d98b3ac3bbd6c98a6b643983bb58d0e4325b83882d2be13954a1263b417`
- v2: `15df83efbbce1d2e684b27c6ccf63a4cc3d6cb5d12a61a78103a775e9eb51bab`
- v3: `0722314faf0476e1566657578782a6437d8d218a779ef24bde474351a5b86522`

The v1 and v2 JSON files and pins are byte-for-byte unchanged. Their existing v1→v2 lineage is also unchanged: three lifecycle entrypoints changed and RunJournal/Run lifecycle were explicit additions.

The exact v2→v3 common-file changed set is:

| File | Reason code |
|---|---|
| `minicode/run_lifecycle.py` | `execution_trace_observer` |
| `minicode/headless.py` | `execution_trace_entrypoint` |
| `minicode/main.py` | `execution_trace_entrypoint` |
| `minicode/tui/input_handler.py` | `execution_trace_entrypoint` |

No file was added to or removed from the protected production set. In particular, these protected files retain their v2 hashes:

- `minicode/agent_loop.py`
- `minicode/run_journal.py`
- `minicode/memory.py`
- `minicode/memory_pipeline.py`
- `minicode/memory_retrieval.py`
- `minicode/memory_injector.py`
- `minicode/memory_candidate_consolidation.py`
- `minicode/context_compactor.py`

## Callback and trace boundary

Existing Agent Loop callbacks provide Tool name/input at start and Tool name/output/error at result. They provide no stable original call ID, Agent step, or true duration. Concurrent-safe Tool callbacks are deferred until execution has completed, then issued during ordered result processing. `on_assistant_message` is not a terminal-only signal: it can carry context summaries, fallback text, progress-adjacent output, and await-user output.

The v3 observer therefore:

- derives Tool events only from the existing start/result callbacks;
- discards input/output immediately at the entrypoint;
- creates an observer-local `toolop_<uuid>` correlation ID;
- pairs same-name Tool callbacks FIFO;
- records an unpaired result with `paired=false` and no operation ID;
- records no Tool step or duration;
- derives at most one `assistant.completed` from normally returned messages;
- persists only Assistant content presence, bounded length, and fixed kind;
- never treats Assistant output as task-quality success.

Gateway continues to reuse `run_headless(..., run_source="gateway")`, so one valid `/run` creates one Gateway Run and never an extra Headless Run. TTY wraps its existing UI callbacks and records only after the original callback returns, preserving callback priority and transcript behavior.

## Deterministic tooling

Default verification checks active v3 without writing:

```bash
python3 scripts/generate_memory_retrieval_production_baseline.py
```

The v3 candidate and fixed-target write modes are:

```bash
python3 scripts/generate_memory_retrieval_production_baseline.py --print-v3
python3 scripts/generate_memory_retrieval_production_baseline.py --write-v3
```

Candidate generation first validates pinned v1/v2 manifests and the exact historical lineage, then requires the exact four-file v2 difference. It rejects missing, additional, or undeclared protected changes. Output is deterministic sorted JSON with no arbitrary output path, timestamp, HOME, username, PID, absolute path, network access, or production-file mutation. v1, v2, and v3 manifests remain individually verifiable.

## Semantic behavior equivalence

The accepted artifact remains `artifacts/memory-retrieval-semantic-gap-baseline.json`, with SHA-256 `5629d6cfc5d2573d37a348ff08909706fdc6a8ad65082b86e3cb0005de1fdd3b`.

The complete deterministic behavior projection remains `b9fabf0aede79044963c8452f367c3667eec696204c98d04365491c1ae1bbd60` for accepted v1, certified v2, and active v3. The deterministic per-case fingerprint remains `b73da444d03864a49401c4a629466125d96e953f8e679fb581704a8fd4ca8667`.

The 108-case evaluator re-certified:

- dataset ID, counts, split, categories, and frozen bytes;
- all three retrieval arms;
- candidate IDs, ranks, scores, Gate, consolidation, rendering, counters, feedback, controller, and first-loss fields;
- Recall@1/3/5/10/20, MRR, NDCG, rendered recall/precision, hard-negative and leakage metrics;
- counter disagreement, semantic-gap adjudication, and confirmed case IDs;
- remote calls = 0;
- diagnostic counter side effects = 0;
- diagnostic filesystem side effects = 0;
- formal state before and after = equal.

Latency samples remain outside the deterministic behavior fingerprint. No dataset, gold artifact, threshold, Gate, consolidation, controller, or injection source was changed.

## Verification result

- Production baseline certification: 12 passed.
- Semantic-gap evaluator certification: 29 passed.
- Complete pytest: 1629 passed, 2 skipped, 0 failed in 63.84 seconds; only the three existing unregistered benchmark-marker warnings remain.
- Touched-file Ruff, `py_compile`, full `compileall -q minicode scripts tests`, production `node --check`, dependency inspection, wheel build, isolated install, installed Gateway/read APIs/assets, and installed `/run` smoke passed.
- Runtime dependencies remain empty.
- Isolated browser acceptance produced the six-event Tool/Assistant timeline, verified redaction and unavailable metrics, exercised all primary and Memory routes plus Skill Routing/MCP, recovered a Runs failure via Retry, found no horizontal overflow, and recorded zero console warning/error entries.

Any future change to Agent Loop or another protected source requires another explicit baseline. v3 certifies only callback-based Tool/Assistant observability; it does not authorize model, usage, Memory, Skill, MCP-runtime, SSE, or write-control expansion.
