# Memory Utilization Efficiency: Live Experiment

Date: 2026-08-21

## Verdict

Persistent Memory now demonstrates a repeatable efficiency advantage on the
frozen synthetic path-recovery workload, while retaining direct verification
and fallback safety. The strongest strictly paired three-run phase reduced
model calls by 54.1%, tool calls by 71.9%, input tokens by 53.7% and output
tokens by 50.8%, with both warm and cold conditions completing 12/12 target
Turns successfully.

This is evidence for the tested workload, not a universal causal estimate.
Provider sampling remained stochastic, and two of the 12 final warm validation
Turns still performed discovery before `read_file`.

## Frozen authority and experiment boundary

- Manifest:
  `artifacts/north-star-memory-compaction-20-v3/manifest.json`
- Suite: `minicode-memory-compaction-live-20-2026-08-21-v3`
- Manifest SHA-256:
  `bf39c66b07e34cd3127bb068d587bc0ec1bf047ac1a18831df71458856a2797d`
- The manifest was not edited during this experiment.
- Every provider call used isolated synthetic workspaces. No user project
  source or credential value appears in the public result documents.
- Efficiency comparisons use the second Turn of each learning chain and the
  single Turn of each seeded warm case. The first learning Turn is excluded
  from reuse-cost comparisons because it creates the lesson.

Across all iterative stages, 92/92 live cases and 118/118 live Turns passed.
No result used `--resume`, manual adjudication or oracle changes.

## Product changes

1. The stable agent prompt declares Memory fallible evidence that cannot
   override current instructions or safety policy.
2. The canonical Memory footer was replaced with an equal-token directive to
   verify exact targets before discovery, preserving hard retrieval budgets.
3. When budget permits, the injected Memory block carries an adjacent rule:
   verify a concrete path/command as the first repository tool call, do not run
   broad discovery first, and fall back if direct verification fails.
4. Verified recovery lessons now put the successful invocation first and
   explicitly say not to reuse the failed invocation.

Retrieval ranking, approval, safety filtering, hybrid selection and injection
eligibility were not relaxed.

## Strict paired result

The primary paired phase is replicates 08-10: four warm/cold task pairs repeated
three times, for 12 warm and 12 cold target Turns.

| Metric | Warm | Cold | Difference | Relative change |
|---|---:|---:|---:|---:|
| Successful target Turns | 12/12 | 12/12 | 0 | 0% |
| Model calls | 28 | 61 | -33 | -54.1% |
| Tool calls | 18 | 64 | -46 | -71.9% |
| Input tokens | 186,865 | 403,626 | -216,761 | -53.7% |
| Output tokens | 2,115 | 4,297 | -2,182 | -50.8% |

Warm used fewer model calls, fewer tool calls and fewer input tokens in 11/12
pairs; the remaining auth pair tied on calls and used 932 more input tokens.
The result is directionally consistent across auth, runtime, deploy and schema,
but the repeated prompts are not independent samples from the full coding-task
distribution.

Result hashes:

- Replicate 08:
  `e7721d3cecfe9bdf00bac1aba59b9b594724a2c453f71ea37ee8f013c863598f`
- Replicate 09:
  `f8c995586178bc62ee72fad1bb9e213792d3783bdb07229f2917bc952eddb1d0`
- Replicate 10:
  `24ba6fbca4f2824d11abc8ba2aca7f59359f7e74eabb3948b32428bd0e283728`

## Final corrected-target validation

The paired phase exposed three warm attempts that directly read a target which
the lesson itself described as failed. The final change made corrected or
succeeded targets authoritative for the verification attempt and explicitly
retired failed targets.

Three warm-only validation replicates then produced:

- 12/12 cases and 18/18 Turns passed.
- 12/12 intended Memory injections observed.
- 28 model calls, 17 tool calls, 187,167 input tokens and 1,686 output tokens.
- Zero failed tool calls.
- 10/12 target Turns began with `read_file`.

The two non-direct starts were one `file_line_count` before a schema read and
one deploy run with broad discovery. Therefore the prompt rule materially
improves behavior but does not make stochastic model compliance absolute.

Result hashes:

- Replicate 11:
  `d23012cb9889ce8d0f238607368ded99dc283500dbda8babe4a0e054d467738d`
- Replicate 12:
  `5480ff0d96a9434e82ce9458c004fd7c4e6304425722bcda82978492c50338b0`
- Replicate 13:
  `575bee2b8cfe1de79e1a2140c3ca3b88476c13987bbd8bccc7b382866919bb8e`

## Regression and safety verification

- Frozen retrieval and semantic evaluator gates remained green after the
  prompt changes.
- Recovery extraction tests prove the successful target is rendered before the
  failed target and that failed invocations are explicitly retired.
- Full repository verification: 4,094 passed, 2 skipped in 296.46 seconds.
- Scoped Ruff and whitespace checks pass for the changed production and focused
  test files.

## Remaining limitations

- The live tasks are synthetic path-recovery tasks; they do not establish the
  same savings for architectural design, debugging, refactoring or write-heavy
  tasks.
- Qwen sampling creates substantial run-to-run variance. Aggregate repeated
  evidence is much stronger than the original one-run comparison, but it is
  not a randomized provider experiment.
- The adjacent execution rule is budget-gated. Extremely small Memory budgets
  retain the equal-token short footer but may omit the detailed rule.
- Direct-first compliance is 10/12 in the final sample, not 100%. A future
  deterministic tool planner could enforce exact-target verification outside
  model prose if that guarantee becomes necessary.
