# Hierarchical Skill Discovery — Paired A/B Harness (2026-09-08)

Experiment: does an advisory, intent-parser-derived "Task clues" prompt section
(arm B) improve hierarchical skill discovery over plain hierarchical directory
cards (arm A), at two catalog scales (~100 and ~1000 skills) and under two
adversarial catalog conditions?

Both arms are treated as **black boxes configured purely via env vars**. This
harness was authored before the arm-B implementation landed and never reads
it; B cells currently measure arm-A behavior until the flags are implemented.

## Arms

| Arm | Env vars (on top of base) |
| --- | --- |
| A | *(base only)* |
| B | `MINI_CODE_SKILL_INTENT_HINTS=1` |
| B2 | `MINI_CODE_SKILL_INTENT_HINTS=1` + `MINI_CODE_SKILL_INTENT_PREFETCH=1` |

Base env for every run: `MINI_CODE_SKILL_SELECTION_MODE=model` and
`MINI_CODE_SKILL_FLAT_CATALOG_CHARS=1000` (config.py clamps this budget to
>= 1000, far below any generated flat rendering, so every run escalates to
the hierarchical "Available skill directories:" projection; the child asserts
`prompt_mode == "hierarchical"` on every run).

## Layout

- `tasks.json` — 24 fresh tasks (19 positive, 5 negative), authored
  2026-09-08 before the B implementation: single-stage colloquial EN/ZH,
  explicit qualified-name mentions, multi-stage conditionals, paraphrased
  prompts, and negatives (unrelated chat, generic coding, concept question,
  quoted-topic translation, explicit skill opt-out). Each positive task
  carries its base-variant target, acceptable alternatives, one hand-authored
  plausible-but-wrong decoy, and expected lure directories.
- `catalog_gen.py` — deterministic (seeded) generator. Structural
  confusability: 6 patterns (retry-backoff, circuit-breaker, pagination,
  idempotency, rate-limiting, timeout-tuning) exist in *every* directory with
  descriptions sharing most vocabulary, plus per-task handcrafted decoys.
  Variants: `base`, `wrong-placement` (each target swapped into a
  wrong-but-plausible directory; qualified names stay valid), `stale-summary`
  (directory cards of target-hosting directories rewritten to omit target
  topics). Emits `catalog-manifest.json` with per-variant `task_targets`.
- `runner.py` — snapshot/build/validate/run orchestrator + `child` probe.
- `score.py` — metrics + per-task diff table.
- `snapshot/minicode/` — pinned byte-copy of production code under test;
  every child asserts `minicode` resolves from here. Refresh deliberately.
- `catalogs/<scale>-<variant>/` — generated workspaces (`.mini-code/skills/`
  + manifest).
- `home/` — sandbox HOME: hardlinked `.env`/`settings.json` (same inodes as
  ~/.mini-code; satisfies env_file.py's no-symlink/0600 checks without
  duplicating secret bytes), empty real `skills/` dir, nothing else — so
  user-scope skills can't contaminate the catalog and probe runs can't write
  to the real skill ledger/sessions.
- `smoke-live/`, `live-smoke/` — offline and 1-task live smoke evidence.
- `live/` — real experiment output (created by run-all).

## How a run works

Per task x scale x variant x arm, `runner.py` spawns a fresh subprocess
(`runner.py child`) with the arm env, sandboxed HOME, and
`PYTHONPATH=snapshot`. The child: loads runtime config (asserts
`skillSelection.mode=model`), builds the full core tool registry against the
catalog workspace (no MCP, no user interaction), runs the production
`route_skill_turn` model-first path with the runtime's hierarchical budgets,
builds the real system prompt, then loops the model (live: glm-5.3-flash via
`create_model_adapter`, 0 retries, 2048 max output tokens, per-call deadline;
offline: `--fake SCENARIO` scripted adapter).

**Multi-step window** (the fix vs the 2026-09-08 probe, which stopped at the
first non-load call): `browse_skill_directory`, `search_skills`, and
`load_skill` — all read-only — are dispatched for up to `--max-steps` model
calls (default 8). The loop stops after the first batch containing a
successful load, on an assistant-only reply, on budget exhaustion, or after a
batch containing non-skill tool requests (those are recorded as blocked and
never executed, but the batch's skill calls still run). Raw evidence per run:
`run.json` (every model call with usage/latency, every tool call with args,
ok, output hash, seen qualified names/directories, load body digests),
`system-prompt.txt`, `messages-at-stop.json`, child stdout/stderr.

## Running the real experiment (once arm B lands)

```bash
cd artifacts/skill-hierarchy-ab-2026-09-08
python3 runner.py snapshot --refresh     # re-pin AFTER arm B is merged
python3 runner.py build --all            # catalogs (skip if present)
python3 runner.py validate               # tasks x manifests consistency
python3 runner.py run-all --out live     # 24 tasks x 2 scales x 3 variants x 3 arms = 432 runs
python3 score.py --live live             # metrics + diff table
```

Useful subsets: `--scales S100 --variants base --arms A B --tasks T01 T02`.
Each live run costs ~1-8 glm-5.3-flash calls; ~432 runs is the full matrix.
If a full sweep is too slow, drop `stale-summary`/`wrong-placement` to a
second pass (`--variants base`).

## Scoring (score.py)

Per arm x scale x variant over valid runs (timeouts/errors excluded and
reported separately, never counted as correct or as clean non-triggers):

- **dir discovery** — target directory browsed or seen in a result page
- **correct load** — digest-verified load of target or acceptable alternative
- **rescue** — correct load where the target directory was NOT in the first
  discovery step (cross-directory recovery via search/later browsing)
- **direct load** — correct load with no discovery step (explicit-name tasks)
- **negative false triggers / clean abstains**
- **body delivery** — sha256-verified bodies appended to the conversation;
  `confirmed_handoff` stays False by construction since the window closes at
  first load
- mean steps / browse / search / load calls, mean latency, token sums

Targets resolve through each variant's manifest (`task_targets`), so
wrong-placement runs score against the relocated qualified name.

## Known limitations

- One run per cell by default; glm-5.3-flash is nondeterministic. Re-run for
  stability before drawing conclusions.
- The window closes at the first successful load: stage-2 skills of the 4
  multi-stage tasks are recorded in tasks.json (`stages`) but not scored.
- Mixed batches: non-skill calls in a batch end the run after the batch's
  skill calls; in production the turn would continue. In-window load rates
  are lower bounds for both arms identically.
- `browse_skill_directory`/`search_skills` outputs are parsed for
  "seen" entries with a line-format regex; a future output-format change must
  be reflected in `_parse_seen_entries` (discovery metrics only, loads are
  digest-verified independently).
- Catalogs are synthetic: confusability is engineered (shared pattern
  vocabulary + decoys), not sampled from real skill libraries.
- The probe executes no task actions; "correct load" measures discovery and
  selection, not downstream task success.
- Credentials come from the hardlinked `home/.mini-code/.env`; if the real
  file is replaced (new inode) after sandbox creation, delete
  `home/.mini-code/.env` and re-run any command to re-link.
