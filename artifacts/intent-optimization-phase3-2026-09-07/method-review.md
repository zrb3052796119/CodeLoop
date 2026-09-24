# Phase 3 evaluation-method review

Decision: PASS for the frozen method identified below, after correcting the scorer-import isolation issue found during this review. No unresolved method defect was found within the reviewed scope. This is a pre-evaluation method review, not a claim about parser accuracy or live model behavior.

Reviewer: independent sub-agent `/root/phase3_fresh_holdout`.
Reviewed and frozen at (UTC): 2026-09-07T08:53:35.403273+00:00.

## Scope and independence

The reviewer inspected phase3 `evaluate.py`, `score_holdout.py`, and `score_loader.py`, and compared them with the corresponding phase2 method scripts. Only the original `score` function was extracted from the old evaluation runner. The reviewer did not inspect production parser source, previous datasets, existing evaluation results, reports, tests, or other agents' implementation work. The fresh holdout was checked only for its existing hash, shape, count, unique IDs, and scoring-schema validity; no annotation was changed. No real intent parser or model was imported, instantiated, or run.

## Findings and resolution

The initial fresh50 branch used an unqualified `score_holdout` import after prepending the candidate source root to `sys.path`, while its freeze record named the phase3 scorer path. A same-named file at the candidate root could therefore have shadowed the scorer without changing the recorded dependency hash. The coordinator replaced this with an explicit `spec_from_file_location` load of `WORK/score_holdout.py`; score selection, schema validation, and holdout/authorship hash checks now all use that same loaded module. Every dynamically loaded scorer branch also checks its module's resolved file path against the recorded scoring dependency.

A local synthetic shadowing check placed a failing `score_holdout.py` at a temporary leading import path and executed only the extracted scorer-selection branch. The corrected branch loaded the intended phase3 scorer and ignored the shadow. This check used an artificial score-loader module exposing the extracted original score function; it did not import production code.

## Reviewed behavior

- ROOT and source isolation: `WORK.parents[1]` and both scorers' `parents[2]` resolve to the intended repository root. The requested source root is resolved before parser import. All imported `minicode` modules are checked to remain under that root, with further validation recorded before and after the run. Artificial module objects passed for one source and failed when a `minicode` module came from another source. Source selection affects code import while the shared ROOT supplies the common runtime configuration.
- Dataset routing and scoring: old50 continues to use the extracted original scorer and its two original input paths. prior40 retains the exact phase2 path and selection of the phase1 holdout scorer. priorfresh40 explicitly loads the phase2 action-group scorer and validates its frozen dataset/authorship hashes. fresh50 explicitly loads the phase3 scorer. Dataset path expressions were executed in isolation and checked for all four choices without opening prior datasets. The phase1 scorer's internal implementation was not reread; its unchanged routing from phase2 was verified.
- Scoring equivalence: phase3 `score_loader.py` is byte-for-byte identical to phase2. The phase2 and phase3 `validate_case` and `score` function ASTs are identical; their scorer-file differences are the two fresh-data hashes. Across 2,688 artificial object combinations, both action-group scorers returned exactly the same result, and the original scorer's primary outcome and issue-list prefix were retained. Focused checks confirmed alternatives are OR, groups are AND, and a negated-only operation cannot satisfy an affirmative group.
- Fresh-data binding: both fresh hash constants exactly match the sealed files. The full fresh dataset has 50 unique IDs and passes schema validation. The runner checks full-dataset cardinality (50 for old50/fresh50, 40 for prior40/priorfresh40) and uniqueness before optional ID filtering. A filtered run records its actual count and must not be described as a full-dataset run.
- Control and telemetry: the non-live path passes no semantic adapter. The live path enables the semantic classifier, rejects the named mock model adapter, records resolved model/provider and classifier settings plus timeout/token overrides, attaches an active observation, budget, cancellation token, and deadline per case, and captures model events, run IDs, budget snapshots, semantic status, per-case latency, and call-count summaries. These control and telemetry branches are unchanged from phase2 except the journal title. They were inspected statically; live telemetry was not exercised in this review.
- Freeze and mutation detection: each invocation records source-file hashes, input hashes, the original scorer hash, runner hash, loader hash, and selected scoring-dependency hash before case execution. It records calibration content identity when applicable and checks source file membership/content, cases, runner, scorers/loader, module provenance, and calibration identity at completion. These completion fields are evidence checks rather than fail-fast assertions; evaluation acceptance must require the applicable unchanged/provenance flags to be true. Fresh authorship is bound by its pre-run SHA256 assertion. This review supplies the pre-evaluation method/data hash manifest below.

## Frozen hashes

| File | SHA256 |
| --- | --- |
| `evaluate.py` | `a1fe0d0ebbe86edfdd3a29d7d280fa0367a220307699ced7d44140c12f0f3c3a` |
| `score_holdout.py` | `fb041f68b7857446f8cb15a3ec5188def621fb63d402cfd8a5901190a382d699` |
| `score_loader.py` | `5631b077a924462d1c86ec2fd73b9320fc120ef7aa7d95657270d106a08019aa` |
| `holdout.json` | `72b912439196384499b50a8d135650f5a5a95e14f9b73b6d5775854452840622` |
| `holdout-authorship.md` | `58486b3337dcbca51e05307cd12dc93252fa9a9e969a9c0dd21b0e463d0fe6f4` |

All hashes refer to exact bytes under `.repair-work/intent-phase3/`. The sealed holdout remains unchanged: 50 cases, 40 Chinese / 10 English, 30 read-only / 20 actual write-or-execution requests, and 12 cases with nonempty context. No case text or parser output is reproduced in this report. The hash of this review is returned separately to the coordinator.
