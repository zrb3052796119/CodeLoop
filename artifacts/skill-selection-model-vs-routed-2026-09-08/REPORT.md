# Skill Selection Mode Paired A/B: `routed` vs `model` — 2026-09-08

**Bottom line: model mode did not improve actual correct loads in this paired run — it was slightly worse (12/17 vs 15/17 positive tasks), with identical negative behavior (0 false triggers on 3 negative tasks in both arms) and ~5% higher input tokens.** The deterministic side of model mode is strictly more permissive (whole catalog admissible; the S14-class policy rejection cannot occur), but the routed arm's "Routed skills:" shortlist acts as a strong hint that the model almost always follows; without it, the model skipped loading and went straight to exploration tools on 3 tasks (S05, S12, S13) within the observation window. One run per cell; the 3-task gap could partly be model nondeterminism, but no evidence favors model mode here.

## Methodology

- **Only variable:** `controls["skill_selection_mode"]` passed to the production `route_skill_turn` seam — `"routed"` (deterministic routing pipeline, embedding retrieval on) vs `"model"` (flat full-catalog listing, no scoring/embeddings/reranker; load policy = catalog-scoped, minus explicit denials).
- **Task set:** the frozen 20 cases from the 2026-09-07 evaluation, `cases.json` sha256 `c5930a29…cb2cc` (verified byte-identical to the baseline copy). 17 positive tasks, 3 negative (S18 quoted names, S19 explicit opt-out, S20 unrelated chat).
- **Probe:** `model_probe.py` byte-identical to the baseline probe (sha256 `c2630d0b…`). Real model calls, dispatch of model-requested `load_skill` only, **stop at the first non-load tool request**; max 4 calls/task, 30 s/call, 0 retries, 2048 max output tokens — identical limits in both arms and identical to baseline.
- **Model:** `glm-5.3-flash` via the configured custom provider (credentials present in `~/.mini-code/.env`; values not inspected). Embedding `text-embedding-v3` via DashScope, routed arm only (model mode makes no embedding calls by design).
- **Same sweep:** all 40 arm-runs executed in one process, per case routed-then-model, identical prompt builder, catalog, permissions, and tool registry (29 core tools, no MCP, no memories).
- **Code snapshot:** production `minicode/` was being edited concurrently mid-run (a half-applied edit crashed the first sweep with `NameError: MAX_SKILL_LOAD_POLICY_BINDINGS` in `skill_routing_turn.py`; the file changed again minutes later). To guarantee both arms ran identical code, the package was snapshotted byte-for-byte into `snapshot/minicode/` and the evaluator asserts `minicode` resolves from there. At report time the snapshot is identical to the working tree (`diff -rq` clean), so results describe current production code.
- **Scoring (identical formulas to baseline):**
  - `retrieval_strict` (routed arm only): selected set covers every required skill group, contains no disallowed extras, and outcome ∈ expected outcomes.
  - `model_load_strict`: observation available (all model calls completed) and the set of successfully loaded skills is strict vs the case labels.
  - `body_handoff_strict`: strict on loaded bodies confirmed inside a subsequent completed model request.
  - `required_skill_admission` / `local_required_load_ok` (deterministic): every required group has a member both visible in the prompt and admitted by the turn load policy; plus an independent real `load_skill` execution of the first member of each required group under that arm's policy, with body digest verified against the catalog.
  - Timeouts counted separately, never as correct. There were none.
- **Raw evidence:** per case × arm, full request/response JSON, load_skill call/result events, system prompts, and journals under `live-paired/<case>-<mode>/`. Aggregates: `live-paired/results.json`, `live-paired/summary.json`. Harness: `paired_evaluator.py`.

## Scoreboard (20 tasks/arm; positives = 17, negatives = 3)

| Metric | routed | model |
| --- | --- | --- |
| Strict retrieval selection (routing-side) | **20/20** | n/a (no selection by design) |
| Required-skill policy admission (deterministic) | 17/17 | 17/17 |
| Independent required-skill load + digest (deterministic) | 17/17 | 17/17 |
| **Model actually loaded correctly (strict, positives)** | **15/17 (88.2%)** | **12/17 (70.6%)** |
| Body handoff confirmed in-window (strict, positives) | 6/17 | 5/17 |
| Negative false triggers | 0/3 | 0/3 |
| Negative correct abstains | 3/3 | 3/3 |
| Timeouts | 0 | 0 |
| Model errors | 0 | 0 |
| Completed GLM calls | 26 | 26 |
| Input tokens | 191,562 | 201,192 (+5.0%) |
| Output tokens | 5,228 | 6,163 |
| Total probe wall time | 170.8 s | 173.8 s |
| Prompt integrity checks | 20/20 | 20/20 |

All 52 load_skill executions across both arms succeeded with digest-verified bodies; zero policy rejections occurred in either arm this round.

## Per-task comparison

| Case | routed: routed→loaded | model: loaded | Δ |
| --- | --- | --- | --- |
| S01 source walkthrough | codebase-explanation → codebase-explanation ✓ | codebase-explanation ✓ | = |
| S02 read-only design review | design-review → design-review ✓ | design-review ✓ | = |
| S03 EN tutoring | minicode-learning-coach-demo → same ✓ | same ✓ | = |
| S04 onboarding README | readme-authoring → same ✓ | same ✓ | = |
| S05 F401/E722 cleanup | lint-and-static-cleanup → same ✓ | **nothing loaded** ✗ | **routed better** |
| S06 upload/query security | secret-and-injection-review → same ✓ | same ✓ | = |
| S07 EN pytest failure | pytest-debugging → same ✓ | same ✓ | = |
| S08 concurrency deadlock | concurrency-debugging → same ✓ | same ✓ | = |
| S09 stale dependency | dependency-hygiene → same ✓ | same ✓ | = |
| S10 regression from bug | regression-from-bug-report → same ✓ | same ✓ | = |
| S11 TDD | test-driven-development → same ✓ | same ✓ | = |
| S12 behavior-preserving refactor | safe-refactor → same ✓ | **nothing loaded** ✗ | **routed better** |
| S13 EN package move | structural-rename → same ✓ | **nothing loaded** ✗ | **routed better** |
| S14 explicit minicode-study | minicode-study (+coach) → minicode-study ✓ | minicode-study ✓ | = (both fixed) |
| S15 explicit commit-hygiene | commit-hygiene → same ✓ (+handoff) | same ✓ (+handoff) | = |
| S16 profile then refactor | both selected; loaded hotpath-profiling only ✗ | loaded hotpath-profiling only ✗ | = (both miss 2nd stage in window) |
| S17 security + README | both selected; loaded security only ✗ | loaded security only ✗ | = (both miss 2nd stage in window) |
| S18 translate skill names (neg) | no route, no load ✓ | no load ✓ | = |
| S19 "no skills" (neg) | deny-all suppressed, no load ✓ | deny-all suppressed, no load ✓ | = |
| S20 cat joke (neg) | no route, no load ✓ | no load ✓ | = |

### Where the modes differ

- **S05, S12, S13 (model arm misses):** with the flat catalog, the model's first response went straight to `list_files`/`grep_files`/`file_tree` without calling `load_skill`; the probe window stops there. Routed mode's top-1 shortlist ("Routed skills:") was loaded immediately in all three. In production the model might load a skill later in the turn — the window cannot see that — but the same window applies to both arms, and the pattern (3/3 misses are "explore first, no load") suggests the routed recommendation is what triggers prompt loading, not the catalog's mere presence.
- **S14 (explicit `code-skills/minicode-study`):** the baseline-round failure (regex sliced a bogus `code-` name; policy rejected the load) is gone in *both* arms — current production code resolves the qualified name correctly and both policies admitted the load. Model mode additionally makes this class of failure structurally impossible (unresolved explicit references stay advisory; policy is catalog-scoped), but there was no residual failure left for it to prevent.
- **S16/S17 (multi-step):** both arms loaded only the first-stage skill inside the window; the probe stops at the first non-load call, so second-stage loading is unobservable in both. Not a mode difference.
- **Negatives:** identical and correct in both arms, including S19 where current code now detects deny-all in both modes (baseline round missed the parsing).

## Comparability to the 2026-09-07 baseline (8/20 strict retrieval)

The task set, probe, scoring, and limits are identical, but **the production routing code changed materially between rounds** (the same uncommitted feature work that added model mode also touched `skill_router.py`, `tooling.py`, `prompt.py`, `skill_routing_turn.py`), and two of 17 skills changed content/metadata since the frozen catalog (`performance/hotpath-profiling`, `quality/lint-and-static-cleanup`; descriptions unchanged). Routed-arm strict retrieval is now 20/20 vs 8/20 baseline; that improvement is attributable to the code/catalog changes, not to anything this experiment controls. Cross-round comparisons of absolute numbers should therefore be treated as indicative only; the routed-vs-model comparison **within** this run is fully paired and valid.

## Caveats

- One run per cell; `glm-5.3-flash` is nondeterministic. The 15 vs 12 gap rests on 3 tasks and could shrink or grow on a rerun.
- The probe window stops at the first non-load tool call in both arms. Model mode's production **load-batch barrier** (deferring non-load calls in a batch containing a fresh load) is not exercised by the probe; in production, model mode would keep the turn alive after a mixed batch, so its in-production load rate could be higher than measured here. Body-handoff counts (6 vs 5) are window-limited lower bounds in both arms, as in the baseline.
- Model-mode prompts are larger (full catalog with descriptions vs a routed shortlist): +9,630 input tokens across 20 tasks (~+480/task average, before cache effects).
- The evaluation wrote no production code, tests, or fixtures; all artifacts live under `artifacts/skill-selection-model-vs-routed-2026-09-08/`. The skill version ledger may record load observations, as in the baseline round.

## Artifacts

- `REPORT.md` (this file), `paired_evaluator.py`, `model_probe.py` (baseline-identical), `cases.json` (frozen task set), `catalog-frozen-2026-09-07.json`
- `snapshot/minicode/` — pinned code copy under test; `live-paired/source-hashes.json` (per-file sha256)
- `live-paired/results.json`, `live-paired/summary.json`, `live-paired/settings.json`, `live-paired/catalog-stability.json` (catalog unchanged during the run: true)
- `live-paired/<case>-<mode>/` — raw per-call request/response JSON, load events, messages-at-stop, journals; `live-paired/<case>-<mode>-system-prompt.txt`
