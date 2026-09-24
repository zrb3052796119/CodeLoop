# Hierarchical Skill Discovery — Fixed-Window A/B Re-Run (REPORT2, 2026-09-08)

## Bottom line

- **The `nonload_tool_requested` early-stop bug dominated the original `live/`
  results.** With the window fixed (mixed batches now continue), correct-load
  rates on base roughly **triple for arms A and B**: aggregate over the two
  scales went from 32/114 (A 7, B 10, B2 15) to **84/114 (A 28, B 29, B2 27)**.
- **B2's prefetch advantage does not survive a fair multi-step window.** In
  `live/`, B2 led because prefetch let it load directly within its first
  response while A/B were cut off mid-navigation. Once A/B get their second
  turn, they catch up and pass B2: S100/base A 15/19, B 17/19 vs B2 14/19;
  S1000/base A 13/19, B 12/19, B2 13/19. B2's remaining edge is efficiency,
  not accuracy: fewest steps (1.26/1.46 vs ~1.5), fewest browse/search calls,
  and ~20% fewer input tokens at S100 (230k vs 291–293k).
- **What A/B look like when allowed to finish navigating:** they browse or
  search on step 1, then load on step 2 — the pattern the harness was meant
  to measure. Rescue loads (target found outside the first discovery step)
  appear only in A/B (S100: 4/15 and 5/17 of correct loads; B2 0/14 — prefetch
  anchoring means B2 never rescues, it either hits directly or misses).
- **No negative false triggers in any arm at either scale** (0/19 valid
  negatives overall; 4 negative runs hit the 60s model-call deadline and are
  excluded from rates).
- Recommendation unchanged in direction but for a different reason: B2 is no
  longer more accurate than A on honest catalogs; its value is token/step
  efficiency. B (hints only) is the accuracy leader at S100 (17/19) but not
  at S1000 (12/19, worst). With n=1 per cell and a stochastic model, treat
  ±2/19 differences as noise.

## The bug and the fix (harness only, `runner.py`)

**Bug:** when a model response's tool-call batch contained ANY non-skill tool,
the child loop set `stop_reason="nonload_tool_requested"` and terminated after
the batch — even though the batch's browse/search calls had executed. The model
never got a second turn to react to discovery results, so every arm measured
"did the model load within its first response" (mean_steps ≈ 1.04 across all
old cells). This structurally favored B2's prefetch (direct first-response
loads) and penalized A/B's navigate-then-load behavior.

**Fix (child loop only):** non-skill calls are still never executed; each now
gets a synthetic tool_result
`error[blocked_in_skill_discovery_window]: not executed; finish skill discovery (browse/search/load) first`
appended to the conversation (recorded in `blocked_nonload_calls` as before),
and the loop CONTINUES. New stop conditions: (a) successful `load_skill` →
`skill_loaded` (unchanged); (b) 8-step budget → `step_budget_exhausted`;
(c) a batch with ZERO skill-tool calls → `no_skill_discovery` (model abandoned
discovery; also covers the degenerate-loop guard); (d) no tool calls →
`assistant_response` (unchanged); (e) timeout/error (unchanged). Snapshot
pinning, env wiring, and logging untouched. Snapshot re-pinned
(`runner.py snapshot --refresh`, 260 files) before the live runs.

## Validation

- New fake scenario `mixed` (step 1 batches `browse_skill_directory` +
  `list_files`): **all three arms now reach step 2 and load with
  digest-verified bodies** (`smoke-live2/S100-base/T01-{A,B,B2}`:
  `stop=skill_loaded, steps=2`; the blocked `list_files` is recorded and its
  synthetic error is in `messages-at-stop.json`). Under the old code this
  stopped at step 1.
- Regression dry-runs: `nonload` now stops as `no_skill_discovery` at step 1
  (was `nonload_tool_requested`); `happy`/`oracle` still load in 2 steps;
  `negative` still abstains.
- Live sanity (S100/base T01-A, into `live2/`): the model batched
  `search_skills` + `list_files` on step 1, got the synthetic error, and
  loaded `http-client/retry-backoff` on step 2 — exactly the recovery the bug
  prevented.

## Methodology recap

- Reduced matrix per user decision: variants = **base** only; scales S100
  (118 skills/12 dirs) and S1000 (1029 skills/50 dirs); arms A/B/B2 →
  24 tasks (19 positive, 5 negative) × 2 × 3 = **144 runs**, fresh
  subprocesses, sandboxed HOME, live glm-5.3-flash, max 8 model calls/run,
  output dir `live2/` (`live/` untouched).
- Health: 144/144 completed, 0 crashes. 4 model-call deadline timeouts, all
  on negative tasks (T21 S100 B/B2; T20 S1000 A/B; excluded from rates).
- Window usage in the fixed harness: **99/144 runs requested at least one
  non-skill tool** (previously an instant stop); of those, 72 still ended in a
  successful load. 62/144 runs were multi-step. Stop reasons:
  `skill_loaded` 90, `no_skill_discovery` 27, `assistant_response` 23,
  `model_error` 4.
- Scoring: `score.py --live live2` → `live2/score-summary.json`. Loads are
  digest-verified. Caveat: 1 run per cell; glm-5.3-flash is stochastic.

## Scoreboards

Positive rates over valid positives (19/cell minus timeouts; FT denominator =
valid negatives). in/out-tok summed over all runs in the cell. "Old" = `live/`
(buggy window); "New" = `live2/` (fixed window).

### S100 / base

| Arm | Correct old → new | Discovery old → new | Rescue | Direct | FT | Steps old → new | Browse | Search | Mean ms | in-tok | out-tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  | 4/19 → **15/19** | 11/19 → 12/19 | 4/15 | 3/15 | 0/5 | 1.04 → 1.54 | 0.38 | 0.25 | 11076 | 291173 | 6870 |
| B  | 5/19 → **17/19** | 14/19 → 15/19 | 5/17 | 2/17 | 0/4 (1 TO) | 1.12 → 1.61 | 0.48 | 0.26 | 11619 | 292797 | 6023 |
| B2 | 9/19 → **14/19** | 7/19 → 7/19 | 0/14 | 8/14 | 0/4 (1 TO) | 1.04 → 1.26 | 0.30 | 0.09 | 12234 | 230068 | 6876 |

### S1000 / base

| Arm | Correct old → new | Discovery old → new | Rescue | Direct | FT | Steps old → new | Browse | Search | Mean ms | in-tok | out-tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  | 3/19 → **13/19** | 10/19 → 11/19 | 3/13 | 3/13 | 0/4 (1 TO) | 1.04 → 1.57 | 0.43 | 0.26 | 22372 | 346157 | 7056 |
| B  | 5/19 → **12/19** | 11/19 → 10/19 | 4/12 | 3/12 | 0/4 (1 TO) | 1.12 → 1.52 | 0.35 | 0.26 | 16941 | 337455 | 5868 |
| B2 | 6/19 → **13/19** | 9/19 → 9/19 | 2/13 | 7/13 | 0/5 | 1.00 → 1.46 | 0.25 | 0.29 | 21168 | 347872 | 9004 |

### How the fixed window changes the picture

- **Directory discovery barely moved** (e.g. S100 A 11→12, B 14→15) — the old
  harness already recorded the first batch's browse/search before stopping.
  What changed is the **conversion** of discovery into loads: the model now
  gets the follow-up turn to call `load_skill` on what it found. That is the
  entire A/B improvement.
- **B2's direct-load profile is real but no longer wins:** 8/14 and 7/13 of
  B2's correct loads are direct (no discovery step), so its correct rate was
  the least under-measured by the bug — yet it still improved (9→14, 6→13)
  because prefetch sometimes anchors on the wrong directory and the second
  turn lets it recover. Its rescue rate stays near zero (0/14, 2/13): when
  prefetch misses, B2 rarely recovers via exploration; A/B do (4–5 rescues
  per cell).
- **B (hints only) at S100 is the new accuracy leader** (17/19) — the hint
  list helps pick the right directory and the fixed window lets the model
  capitalize. At S1000 it does not (12/19, worst): hint anchoring without
  prefetch's direct-load shortcut looks mildly harmful at scale. With n=1 per
  cell, both statements are weak.
- **Costs scale with the window as expected:** mean steps rose from ~1.0 to
  1.3–1.6 and input tokens grew ~40–50% per cell (each extra step replays the
  catalog-bearing system prompt). B2 remains the cheapest arm at S100 by
  ~20% on input tokens; at S1000 token costs converge.
- **`no_skill_discovery` (27 runs) is the new failure mode to watch:** the
  model opens with only non-skill calls (typically `list_files`) and the run
  stops immediately by design. It concentrates on the harder discover tasks
  (T13, T16, T18 at both scales) and is what keeps A/B from going higher.

## Per-task diff table (live2)

| Scale | Variant | Task | A | B | B2 |
| --- | --- | --- | --- | --- | --- |
| S100 | base | T01 | ok | ok | ok |
| S100 | base | T02 | x | ok | ok |
| S100 | base | T03 | ok | ok | ~ |
| S100 | base | T04 | ok | ok | ok |
| S100 | base | T05 | ok | ok | ok |
| S100 | base | T06 | ok | ok | ok |
| S100 | base | T07 | ok | ok | ok |
| S100 | base | T08 | ok | ok | ok |
| S100 | base | T09 | ok | ok | ok |
| S100 | base | T10 | ok | ok | ok |
| S100 | base | T11 | ok | ok | ok |
| S100 | base | T12 | ok | ok | ok |
| S100 | base | T13 | x | x | x |
| S100 | base | T14 | x | ok | ok |
| S100 | base | T15 | ok | ok | x |
| S100 | base | T16 | x | x | x |
| S100 | base | T17 | ok | ok | ok |
| S100 | base | T18 | ok | ok | ok |
| S100 | base | T19 | ok | ok | x |
| S100 | base | T20 | ok | ok | ok |
| S100 | base | T21 | ok | T | T |
| S100 | base | T22 | ok | ok | ok |
| S100 | base | T23 | ok | ok | ok |
| S100 | base | T24 | ok | ok | ok |
| S1000 | base | T01 | ok | ok | ok |
| S1000 | base | T02 | ok | x | ok |
| S1000 | base | T03 | ok | ok | ~ |
| S1000 | base | T04 | ok | x | ok |
| S1000 | base | T05 | ok | ok | ok |
| S1000 | base | T06 | ok | ok | ok |
| S1000 | base | T07 | ok | ok | ok |
| S1000 | base | T08 | ok | ok | ok |
| S1000 | base | T09 | ok | ok | ok |
| S1000 | base | T10 | ok | ok | ok |
| S1000 | base | T11 | ok | ok | ok |
| S1000 | base | T12 | ok | ok | ok |
| S1000 | base | T13 | x | x | ~ |
| S1000 | base | T14 | ~ | x | x |
| S1000 | base | T15 | x | ok | ok |
| S1000 | base | T16 | x | x | ~ |
| S1000 | base | T17 | x | ok | x |
| S1000 | base | T18 | x | x | x |
| S1000 | base | T19 | ok | ~ | ok |
| S1000 | base | T20 | T | T | ok |
| S1000 | base | T21 | ok | ok | ok |
| S1000 | base | T22 | ok | ok | ok |
| S1000 | base | T23 | ok | ok | ok |
| S1000 | base | T24 | ok | ok | ok |

Marks: ok = correct load (positive) / clean abstain (negative); ~ = target
directory discovered but no correct load; x = not discovered; T = timeout
(excluded from rates); E = error.

## Follow-ups

- Re-run the adversarial variants (wrong-placement, stale-summary) under the
  fixed window before trusting the old REPORT.md conclusions there — the same
  measurement bias applied, and B2's wrong-placement collapse may look
  different when it gets recovery turns (or worse, if prefetch anchors harder).
- n=1 per cell; if any headline matters, repeat the 144-run matrix at least
  once to bound glm-5.3-flash variance.
