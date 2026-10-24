# Skill-chain evaluator

This directory preserves the evaluator, fixture builder, scoring rubric, and frozen inputs previously stored under `.repair-work/skill-chain-completion-2026-09-08`. The existing regression tests use this version-controlled location so a clean checkout does not depend on an ignored experiment directory. Historical runs and reports remain in their existing locations.

The case and split files and their SHA-256 pins are unchanged. The scorer continues to distinguish observable protocol compliance from attributed semantic correctness; offline scripted runs do not count as live capability evidence.

From the repository root, an offline pipeline smoke run is:

```sh
python -m scripts.skill_chain.eval.runner --output /tmp/skill-chain-smoke
```

The output directory must not already exist. Live evaluation remains an explicit `--live` option and retains the original model guard. Existing run results can be scored without network access with `python -m scripts.skill_chain.score_v2 --batch <run-directory>`.
