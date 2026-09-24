# Intent optimization phase 3

## Goal
Prevent write/execute operations inferred from reported text and nominal action words; preserve genuine current commands and improve adjacent read-only/negation boundaries.

## Phases
- [x] Snapshot actual phase2 final source and preserve the dirty worktree.
- [x] Diagnose request ownership and freeze behavioral regressions.
- [x] Implement bounded shared role checks for rules and semantic admission; repair proven adjacent scope bugs.
- [x] Prove tests against the actual pre-fix snapshot; independent review and relevant full regression.
- [x] Freeze code; evaluate existing 130 development cases and a new independently sealed 50-case set.
- [x] Audit causal attribution and read-only safety separately; publish report and evidence.

## Constraints
- No global config change, no sample action execution, no label edits or provider-setting changes for a better score.
- New holdout author sees no source, prior cases, results, or fixes; no implementation tuning on fresh results.
- Reuse original frozen scorers for each old dataset. New holdout uses phase2 operation-group scoring.
- Predeclare live sampling: each before/after version runs each new case once; no claim of stable LLM gain from single samples. Use deterministic fixed proposals for causal safety checks.

## Errors
- Nominal guard initially suppressed true commands after completed review plus an adverb; fixed with sequence grammar and positive controls.
- Attribution helper initially swallowed imperative prefixes; fixed with bounded content-source noun phrases, preserving unknown requests.
- Method review found path-shadowable scorer import; switched to pinned file imports before any evaluation.
- First attempt to resume holdout author hit the active-agent limit; resumed once scope agent completed.
- Initial rg for AGENTS.md returned no matching files; checked ancestors explicitly.

## Status
Implementation and fixed-scope review complete; 2599 related regressions passed, critical Ruff passed, method review passed. Production and scoring frozen before fresh evaluation.

Frozen public-entry tests: nominal25 + scope33 + semantic57 = 115 pass. Counterexample baseline nominal13 fail/12 pass, scope12 fail/21 pass; semantic34 fail/23 pass (current57 pass), all PROVEN.
Baseline and candidate fresh50 live each ran once; process windows overlapped by 2.630s at the baseline tail/candidate start. Preserve both samples; do not use their elapsed times as a performance claim.
Independent review closed two new regression groups using the same28 probes; two pre-existing gaps remain. Fresh50 independently sealed with40 Chinese/10 English,30 read-only/20 effectful,12 context cases. No fresh inputs or outputs read by implementation agents/root.

Fresh50 unblinded only after final code freeze: disabled21→21/50, live36→33/50 (candidate5timeouts, baseline2timeouts+1provider error). Independent attribution audit complete: 150 fresh pairs and18 changing development pairs matched across versions. No code or label tuning on revealed data. All eight comparison groups independently recomputed and matched.

## Final outcome
Specific safety/scope fixes are verified; overall LLM and new-data accuracy did not improve. Live C43→43,N29→29,H29→28,T36→33. Prior errorsC40/H33 eliminated; freshT05/T11remain. Sources and all eight candidate runs match final freeze. Report and complete per-case evidence archived under artifacts/intent-optimization-phase3-2026-09-07/.
