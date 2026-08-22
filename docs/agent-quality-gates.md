# MiniCode Agent Quality Gates

MiniCode separates regression protection from grade promotion. A green unit
suite proves that implemented behavior still works; it does not by itself prove
that the agent's routing, long-context fidelity, or real-task success is good
enough for an A rating.

## Commands

Run the checked-in accepted A baseline used by pull-request CI:

```bash
python3 -m scripts.evaluate_agent_quality --profile current
```

Check the declared A target:

```bash
python3 -m scripts.evaluate_agent_quality --profile a \
  --north-star-results artifacts/my-fresh-run.json
```

The checked-in A evidence makes both commands exit 0. Supplying a fresh
`--north-star-results` file re-evaluates the same sealed manifest without
requiring identical result bytes. Add `--include-cases` for per-case
diagnostics. Both commands are deterministic, make zero remote calls, and
print one JSON report to stdout.

## What is measured

### Skill routing

The frozen 60-case set contains 40 positive/explicit prompts and 20 abstention
or adversarial hard negatives, including bilingual and ordinary-language name
collisions. It measures top-1 accuracy, abstention, exact required-Skill
selection, and forbidden selection.

### Context compaction

The evaluator runs the production `AutoCompactDispatcher` through one to five
forced compaction rounds across 12 cases. Sentinels represent the goal,
verified facts,
rejected approaches, loaded Skill instructions, and latest user instruction.
The gate also checks prior-summary chaining, tool-call/result integrity, and
non-negative token savings. Every frozen case also carries distinct task-ledger
sentinels; the gate requires the marked parent-owned ledger projection itself,
not merely a copied marker in an LLM summary, to survive every round.

### North-star tasks

The frozen manifest records 50 real-environment synthetic-repository tasks in
10 categories, with 30 workspace-writing cases. The result evidence contains
complete Run, duration, model-call, token, verification, and oracle telemetry.
The A profile requires at least:

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
| `artifacts/agent-quality-baseline.json` | Accepted A baseline projection |

The deterministic case sets and north-star manifest have SHA-256 identities in
both profiles. The current profile also pins its recorded result file so the A
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
5. Keep the A claim only while `--profile a` exits 0 on the sealed data and the
   live task run has complete evidence; never lower the A thresholds to match
   an implementation.
