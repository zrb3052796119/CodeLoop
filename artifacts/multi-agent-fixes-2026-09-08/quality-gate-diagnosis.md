# Read-only attribution of the quality-gate failures

The three reported quality tests fail because the frozen Skill-routing
contract already disagrees with the pre-fix working tree. This is not a new
multi-agent regression. No production source, fixture, contract or frozen
baseline was changed during this diagnosis.

## Direct evidence

The current quality evaluation and an evaluation importing the complete
`minicode` package from the saved pre-fix snapshot
`/private/tmp/minicode-multi-agent-buggy-ahhcgrxd` produced the same canonical
report hash:

```text
fb4a48124bbc91a4cc88d1a5cf9a515e8ba13cc62589205f635d3b52317e9d25
```

Both have exactly these failing `current` checks:

1. `datasets.skillRoutingJoinedSha256.equals`.
2. `skillRouting.top1Accuracy.min`.

The `a` profile passes both before and after the multi-agent changes. The
joined-routing metrics, compaction, North Star and multi-agent-cost metrics
match the checked-in baseline. The evaluator successfully imports and runs;
there is no dependency/import error in these failures.

The pre-fix package paths and complete comparison are recorded in
`quality-gate-old-snapshot.json`; current case diagnostics are in
`quality-gate-diagnosis.json`.

## Two independent pre-existing mismatches

The joined Skill fixture has the same bytes as the pre-fix snapshot, but its
normalized digest differs from the frozen contract/baseline:

```text
Frozen expected: 5ef97de86a76bdfc30be9bfd6e74adc7fd7e5e2d5c664a99c8ba257dbd40f808
Fixture actual:  77e0c658eee6065c3aafcd6277de5c0207b5d70a70c291be2890f3119a193b2f
```

Thus the CRLF test correctly normalizes line endings, but still encounters
the unrelated frozen-dataset mismatch. The baseline-matching test fails at
its dataset comparison. The CLI test fails its first `current` assertion;
the separately evaluated `a` profile itself passes.

Legacy Skill top-1 accuracy is **0.925**, versus a frozen/current floor of
**1.0**. The same three cases fail using the pre-fix package:

| Case | Expected | Actual |
|---|---|---|
| `positive-database-02` | `database-tuning` | no selection |
| `positive-deploy-01` | `deployment-automation` | no selection |
| `positive-database-zh-02` | `database-tuning` | `performance-profiling` |

The prompts concern database transaction/query latency, Docker deployment
automation, and Chinese SQL slow-query/index optimization. They exercise
`SkillRouter.route(parse_intent(...))`, not the workflow scheduler or repair
loop.

There are concurrent working-tree edits to `skill_router.py` and
`skill_routing_turn.py` outside this task's ownership (model-first Skill
selection additions). Those do not account for this discrepancy either:
the saved original versions and current versions yield the exact same full
quality report hash.

## Reproduction

```text
/Users/zhourunbo/miniconda3/bin/python -m pytest -q \
  tests/test_agent_quality_evaluator.py::test_checked_in_quality_baseline_matches_the_frozen_suite \
  tests/test_agent_quality_evaluator.py::test_quality_cli_passes_current_and_a_profiles

2 failed in 24.12s
```

The complete failure output is in `quality-gate-diagnosis.txt`. The interpreter
was Python 3.13.13. Python 3.12.8 produced the same report hash, checks and failed
cases, as recorded in `quality-gate-python312.json`; the discrepancy is not
specific to the 3.13 test interpreter. These diagnostic artifacts do not replace
or update any frozen quality contract.
