# MiniCode Agent Quality Gates

MiniCode separates regression protection from grade promotion. A green unit
suite proves that implemented behavior still works; it does not by itself prove
that the agent's routing, long-context fidelity, or real-task success is good
enough for an A rating.

## Commands

Run the checked-in regression floor used by pull-request CI:

```bash
python3 scripts/evaluate_agent_quality.py --profile current
```

Check the declared A target:

```bash
python3 scripts/evaluate_agent_quality.py --profile a \
  --north-star-results artifacts/my-fresh-run.json
```

The A command is expected to exit 1 until every promotion check passes. Add
`--include-cases` for per-case diagnostics. Both commands are deterministic,
make zero remote calls, and print one JSON report to stdout.

## What is measured

### Skill routing

The frozen set contains positive, explicit, abstain, bilingual, and hard-
negative prompts. It measures top-1 accuracy, abstention, exact required-Skill
selection, and forbidden selection. The current baseline intentionally retains
known failures rather than selecting only easy prompts.

### Context compaction

The evaluator runs the production `AutoCompactDispatcher` through two or three
forced compaction rounds. Sentinels represent the goal, verified facts,
rejected approaches, loaded Skill instructions, and latest user instruction.
The gate also checks prior-summary chaining, tool-call/result integrity, and
non-negative token savings.

### North-star tasks

The seed manifest records the four repaired real-environment acceptance
scenarios from 2026-08-19. It is evidence ingestion, not a claim that four
tasks are representative. The A profile requires at least:

- 50 tasks in 8 categories;
- 20 workspace-writing tasks;
- 80% task and verified-success rates;
- 90% oracle pass rate and 70% in every category;
- zero unsafe-action cases;
- at least 95% Run, duration, model-call, and token telemetry coverage.

Live or manually approved runners may execute tasks differently, but their
result file must join exactly to the frozen manifest and use the same result
contract. Missing cases, unknown cases, duplicate IDs, invalid counters, or
unrecognized oracle evidence fail closed.

## Files and change control

| File | Authority |
|---|---|
| `tests/fixtures/agent_quality/skill-routing.json` | Frozen routing cases and Skill catalog |
| `tests/fixtures/agent_quality/compaction-fidelity.json` | Frozen repeated-compaction cases |
| `tests/fixtures/agent_quality/north-star-manifest.json` | Frozen real-task identities, categories, and oracles |
| `tests/fixtures/agent_quality/north-star-baseline-results.json` | Recorded baseline outcomes and evidence |
| `artifacts/agent-quality-contract.json` | Current and A thresholds plus dataset digests |
| `artifacts/agent-quality-baseline.json` | Accepted B baseline projection |

The deterministic case sets and north-star manifest have SHA-256 identities in
both profiles. The current profile also pins its recorded result file so the B
baseline cannot drift silently. The A profile intentionally accepts a fresh
result file against the sealed manifest; otherwise every valid nightly run
would fail only because its evidence bytes changed. Changing a case still
requires explicit review of the fixture, contract digest, and baseline
artifact. `tests/test_agent_quality_evaluator.py` rejects stale projections.

## Promotion discipline

1. Freeze or extend cases before tuning production behavior.
2. Preserve the old results and run the new implementation on the same set.
3. Review aggregate and per-category regressions, not only the overall rate.
4. Update the accepted baseline only for an intentional, explained change.
5. Claim A only when `--profile a` exits 0 on the sealed data and the live task
   run has complete evidence; never lower the A thresholds to match an
   implementation.
