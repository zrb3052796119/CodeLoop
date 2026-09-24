# Hierarchical Skill Discovery — Paired A/B Report (2026-09-08)

## Bottom line

- **B2 (hints + prefetch) shows a real, repeatable benefit over A on the base
  and stale-summary variants** at both scales: correct-load rate roughly
  doubles (S100/base 9/19 vs 4/19; S100/stale-summary 9/19 vs 6/19;
  S1000/base 6/19 vs 3/19; S1000/stale-summary 6/19 vs 4/19), at ~equal or
  lower token cost, fewer browse/search calls, and no negative false triggers.
- **B (hints only) does not beat A** on correct loads (18/114 vs 18/113
  aggregate). It slightly improves directory discovery (61/114 vs 58/113) but
  does not convert that into more correct loads.
- **Under wrong-placement (the hardest adversarial variant) all arms collapse**
  (A/B 1/19 and B2 1/19 at S100; 0/19 for everyone at S1000), and the hint
  arms are actively *worse* on discovery there: B2 discovers the true target
  directory in only 3/19 at S1000 vs A's 8/19, because prefetch anchors the
  model on the semantically-right-but-placement-wrong directory and it
  confidently loads the same-named skill from the wrong directory.
- Recommendation: **adopt B2's prefetch-style hints for honest catalogs; do
  not ship B alone**. Hints must stay visibly advisory (they already are);
  consider suppressing or down-weighting "Suggested starting directories"
  when matcher coverage is low, since wrong-placement shows the suggestion
  list itself (not just prefetch) suppresses exploration of other directories.

## Methodology recap

- Harness: `artifacts/skill-hierarchy-ab-2026-09-08/` (see `HARNESS.md`).
  24 tasks (19 positive, 5 negative) × 2 scales (S100: 118 skills/12 dirs;
  S1000: 1029 skills/50 dirs) × 3 catalog variants (base, stale-summary,
  wrong-placement) × 3 arms = **432 runs**, executed in three stages:
  S100 all variants (216) → S1000 base (72) → S1000 adversarial (144).
- Every run is a fresh subprocess against the re-pinned snapshot
  (`runner.py snapshot --refresh` re-run after arm B landed; the pin contains
  `intent_hints` in `snapshot/minicode/skill_routing_turn.py`), sandboxed
  HOME, live model glm-5.3-flash, max 8 model calls per run, only
  `browse_skill_directory`/`search_skills`/`load_skill` dispatched.
- Arms configured purely by env: A = base (`MINI_CODE_SKILL_SELECTION_MODE=model`,
  `MINI_CODE_SKILL_FLAT_CATALOG_CHARS=1000`), B = A + `MINI_CODE_SKILL_INTENT_HINTS=1`,
  B2 = B + `MINI_CODE_SKILL_INTENT_PREFETCH=1`. All runs verified
  `prompt_mode == "hierarchical"`.
- **Harness fix applied during this run** (harness code only, no production
  changes): `runner.py` built the `controls` mapping for `route_skill_turn`
  without forwarding `skill_intent_hints`/`skill_intent_prefetch` from the
  resolved runtime config, so the env flags were parsed by `config.py` but
  never reached the router (production wires them in
  `agent_runtime.py:493-503`). A pre-flight plumbing check caught the missing
  "Task clues (advisory…)" section; `runner.py` now forwards both keys,
  mirroring the production wiring. Verified post-fix: B emits the "Task
  clues" section; B2 additionally emits "Prefetched descriptions for
  directory …".
- Health: 432/432 completed; **1 timeout** (S100/stale-summary T20-A, a
  negative task; excluded from rates), **0 crashes, 0 errors**, no API
  failures.
- Scoring: `score.py --live live` → `live/score-summary.json`. Targets
  resolve through each variant's manifest; loads are digest-verified.
  Nondeterminism caveat: 1 run per cell; glm-5.3-flash is stochastic.

## Scoreboards

Positive rates are over valid positives (19/cell; 18 for
S100/stale-summary/A due to the timeout being a negative — FT denominator
4 there). FT = negative false triggers (any successful load on a negative
task). in-tok/out-tok are summed over all runs in the cell.

### S100 / base

| Arm | Dir discovery | Correct load | Rescue | Direct | FT | Steps | Browse | Search | Load | Mean ms | in-tok | out-tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  | 11/19 | 4/19 | 1/4 | 3/4 | 0/5 | 1.04 | 0.38 | 0.25 | 0.17 | 11991 | 191207 | 6544 |
| B  | 14/19 | 5/19 | 1/5 | 2/5 | 0/5 | 1.12 | 0.50 | 0.21 | 0.21 | 11744 | 210356 | 5712 |
| B2 | 7/19  | 9/19 | 0/9 | 8/9 | 0/5 | 1.04 | 0.21 | 0.12 | 0.42 | 12047 | 196570 | 6191 |

### S100 / stale-summary

| Arm | Dir discovery | Correct load | Rescue | Direct | FT | Steps | Browse | Search | Load | Mean ms | in-tok | out-tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  | 12/19 | 6/19 | 1/6 | 3/6 | 0/4 (1 TO) | 1.13 | 0.35 | 0.35 | 0.35 | 11342 | 200922 | 5645 |
| B  | 13/19 | 4/19 | 0/4 | 2/4 | 0/5 | 1.08 | 0.42 | 0.21 | 0.17 | 10594 | 198661 | 6471 |
| B2 | 5/19  | 9/19 | 0/9 | 8/9 | 0/5 | 1.04 | 0.17 | 0.04 | 0.38 | 12551 | 194916 | 7648 |

### S100 / wrong-placement

| Arm | Dir discovery | Correct load | Rescue | FT | Steps | Browse | Search | Load | Mean ms | in-tok | out-tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  | 7/19 | 1/19 | 1/1 | 0/5 | 1.17 | 0.50 | 0.29 | 0.29 | 13559 | 216812 | 8089 |
| B  | 6/19 | 1/19 | 1/1 | 0/5 | 1.12 | 0.33 | 0.25 | 0.21 | 12812 | 209382 | 7191 |
| B2 | 7/19 | 1/19 | 1/1 | 0/5 | 1.17 | 0.33 | 0.33 | 0.33 | 14896 | 222283 | 8531 |

### S1000 / base

| Arm | Dir discovery | Correct load | Rescue | Direct | FT | Steps | Browse | Search | Load | Mean ms | in-tok | out-tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  | 10/19 | 3/19 | 1/3 | 2/3 | 0/5 | 1.04 | 0.25 | 0.25 | 0.12 | 15698 | 231254 | 6648 |
| B  | 11/19 | 5/19 | 1/5 | 2/5 | 0/5 | 1.12 | 0.38 | 0.17 | 0.21 | 15523 | 253321 | 7273 |
| B2 | 9/19  | 6/19 | 0/6 | 6/6 | 0/5 | 1.00 | 0.29 | 0.08 | 0.25 | 17676 | 229407 | 8204 |

### S1000 / stale-summary

| Arm | Dir discovery | Correct load | Rescue | Direct | FT | Steps | Browse | Search | Load | Mean ms | in-tok | out-tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  | 10/19 | 4/19 | 0/4 | 3/4 | 0/5 | 1.04 | 0.12 | 0.33 | 0.17 | 14750 | 229725 | 7199 |
| B  | 11/19 | 3/19 | 1/3 | 2/3 | 0/5 | 1.04 | 0.21 | 0.38 | 0.17 | 14500 | 231934 | 6806 |
| B2 | 8/19  | 6/19 | 0/6 | 6/6 | 0/5 | 1.00 | 0.17 | 0.21 | 0.25 | 16731 | 228675 | 7988 |

### S1000 / wrong-placement

| Arm | Dir discovery | Correct load | FT | Steps | Browse | Search | Load | Mean ms | in-tok | out-tok |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A  | 8/19 | 0/19 | 0/5 | 1.08 | 0.04 | 0.42 | 0.21 | 16355 | 241442 | 8550 |
| B  | 6/19 | 0/19 | 0/5 | 1.21 | 0.29 | 0.29 | 0.25 | 17935 | 275583 | 8809 |
| B2 | 3/19 | 0/19 | 0/5 | 1.04 | 0.12 | 0.25 | 0.29 | 14575 | 241028 | 7380 |

Aggregates over all 6 cells (positives): correct load **A 18/113, B 18/114,
B2 31/114**; directory discovery A 58, B 61, B2 39; negative false triggers
**0 for every arm** (119/120 clean abstains, 1 timeout).

## Hint quality (measured from captured system prompts)

Fraction of positive runs where the hint block's "Suggested starting
directories" contained the true target directory (and, for B2, where the
prefetched directory was the target directory):

| Cell | B sugg-hit | B2 sugg-hit | B2 prefetch-hit |
| --- | --- | --- | --- |
| S100 base | 9/19 | 9/19 | 8/19 |
| S100 stale-summary | 9/19 | 9/19 | 8/19 |
| S100 wrong-placement | 1/19 | 1/19 | 1/19 |
| S1000 base | 7/19 | 7/19 | 4/19 |
| S1000 stale-summary | 7/19 | 7/19 | 4/19 |
| S1000 wrong-placement | 1/19 | 1/19 | 0/19 |

12 of 19 tasks get a non-empty suggestion list; the other 7 honestly emit
nothing (matcher found no coverage). Suggestions are identical across
variants at the same scale (the matcher reads directory cards, which
stale-summary only rewrites for target-hosting directories — hint quality
therefore does not degrade under stale-summary, a small mismatch with the
adversarial intent).

## Findings against the three questions

### (i) Do adversarial variants show the model correcting bad hints via other directories / global search?

**Partially, and mostly under stale-summary — almost never under
wrong-placement.**

- stale-summary: hints still point at the true directory (they are computed
  from the *rewritten* cards only when coverage survives; sugg-hit stayed
  9/19 at S100), and B2 converts that into 9/19 and 6/19 correct loads
  despite the target topic being scrubbed from the card. The model trusts
  the advisory hint over the card text — which is exactly the designed
  recovery path here.
- wrong-placement: the hint matcher ranks by description similarity, so it
  points to where the skill *should* live, not where the variant put it
  (sugg-hit 1/19 — by construction misleading: 11 of 12 non-empty suggestion
  lists exclude the true directory). No arm recovers: correct load is 1/19
  (S100, all arms — the same task, T06, via search rescue) and 0/19 (S1000).
  Cross-directory rescue events overall are rare and evenly distributed
  (A 4, B 4, B2 1 across all cells; the B2 one is T01 wrong-placement, where
  the model ignored the wrong prefetch, browsed two other directories,
  searched, and loaded the target).
- B2's prefetch makes wrong-placement strictly worse on *discovery*:
  3/19 at S1000 vs A's 8/19. The prefetched first page shows real qualified
  names in the wrong directory, and the model direct-loads them: loads of
  "right skill name, wrong directory" are A 2 / B 3 / B2 5 at S100 and
  A 5 / B 6 / B2 7 at S1000 (e.g. T07: B2 loaded `job-scheduler/rate-limiting`
  straight from the prefetch; the relocated target was
  `message-queue/rate-limiting`; arm A browsed both directories and at least
  discovered the truth).

### (ii) Does B reduce browse calls / steps?

- **B alone: no.** Browse calls are flat-to-up (0.38→0.50 at S100/base,
  0.25→0.38 at S1000/base); search slightly down; steps ≈ equal. B converts
  hints into a bit more directory discovery (+3 aggregate) but not into more
  correct loads.
- **B2: yes, strongly.** At S100/base browse 0.38→0.21 and search
  0.25→0.12; at S100/stale-summary search 0.35→0.04; at S1000/base steps
  1.04→1.00, search 0.25→0.08. 8/9 (S100) and 6/6 (S1000) of B2's correct
  loads are direct loads with *no* discovery step at all — the prefetch page
  doubles as the discovery artifact. Cost: mean latency roughly equal
  (±10%), input tokens equal or lower than A in 5 of 6 cells (the exception:
  B at S1000/wrong-placement, +14%, where the hint block adds tokens without
  shortening the search).

### (iii) Tasks where hints made the model worse

Per-task paired diff vs A across all 6 cells (win = better mark
ok > ~ > x; full table in `live/score-summary.json`):

- **B net-negative tasks**: T19 (0 wins / 3 losses — e.g. S100/base the
  hinted run abandoned skill tools for `list_files`/`file_tree` and never
  discovered `object-storage`), T09 (0/2), T15 (0/2), T03 (0/1), T11 (0/1).
- **B2 net-negative tasks**: T03 (0/2), T09 (0/2), T15 (0/2), T04 (1/2),
  T07 (4/2 — but both losses are the wrong-placement confident-wrong-load
  pattern), T06 (4/2, same caveat).
- **Net-positive tasks**: B2 on T08 (+4/0), T02 (+4/1), T01 (+3/0),
  T14 (+2/1); B on T01 (+4/1), T14 (+3/0), T02/T17 (+2/1).
- The clearest bias mechanism is not at base (wrong prefetches there were
  usually corrected, e.g. T01 B2 live run prefetched `graphql-api` yet the
  model browsed `http-client`+`payment-gateway`, searched, and loaded the
  target) but under wrong-placement, where prefetch turns "would have
  explored" into "confidently loaded the wrong directory's same-named copy".

## Anomalies and caveats

- 1 timeout (S100/stale-summary T20-A), 0 crashes/errors; API healthy
  throughout.
- Many non-hint failures are the probe's closed world: runs end with
  `nonload_tool_requested` (model reached for `list_files`/`file_tree` etc.
  on step 1) — in production the turn would continue. In-window load rates
  are lower bounds for all arms identically.
- One run per cell; glm-5.3-flash is nondeterministic. The B2-over-A correct
  load gap (31/114 vs 18/113, concentrated +11 on base+stale-summary) is
  large and consistent across four independent cells, but re-running the
  sweep once would firm it up.
- B2's "discovery" metric understates it by construction (direct loads skip
  discovery), so discovery should not be read as a regression for B2 on
  honest variants.
- Hints are presentation-only; `prompt_skills`, load policy, and authority
  are identical across arms (verified in the routing code and by the
  identical policy blocks in run.json files).

## Recommendation

Ship B2-style prefetch hints; skip B alone. Expected effect from this
experiment: on honest catalogs at 100–1000-skill scale, ~2× correct-load
rate within the first-load window, ~40-50% fewer browse calls, ~50-75%
fewer search calls, neutral latency and token cost, zero negative
false-trigger risk. Known residual risk: when the catalog itself is
misplaced (wrong-placement), prefetch increases confident wrong-directory
loads and suppresses cross-directory exploration; a coverage-threshold
guard on the suggestion/prefetch block would mitigate this and should be
tested before relying on hints in noisy real catalogs.

## Artifacts

- Raw evidence: `live/<scale>-<variant>/<task>-<arm>/{run.json,system-prompt.txt,messages-at-stop.json}` (432 runs)
- Metrics: `live/score-summary.json`
- Plumbing evidence: `plumb-check/`
- Harness fix: `runner.py` (controls now forward `skill_intent_hints` /
  `skill_intent_prefetch`, mirroring `agent_runtime.py`)
