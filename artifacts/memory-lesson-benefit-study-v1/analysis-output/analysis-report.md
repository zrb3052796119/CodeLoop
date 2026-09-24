# Memory lesson benefit — analysis report

## Analysis contract

- Inferential unit: task family (n=6); two blocks as nested repeats.
- Primary contrasts: cold − warm on tool calls and task input tokens
  (Holm-corrected exact Wilcoxon; family-cluster bootstrap CIs).
- Learning-chain and reviewer endpoints are descriptive rates.

## Raw per-condition target-turn metrics (cell means, 12 cells each)

| condition | outcome success | tool calls | tool failures | task model calls | side model calls | input tokens | output tokens | duration s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| warm | 12/12 | 2.50 | 0.08 | 3.25 | 0.00 | 26,365.33 | 628.08 | 22.2 |
| cold | 12/12 | 6.67 | 0.50 | 5.42 | 0.17 | 43,404.58 | 564.42 | 26.5 |

## Paired savings (cold − warm, family means)

| metric | warm | cold | mean saving | 95% CI | relative | Wilcoxon p | Holm p |
|---|---:|---:|---:|---|---:|---:|---:|
| tool_calls:cold-minus-warm | 2.50 | 6.67 | 4.17 | [2.92, 5.17] | 62.50% | 0.03 | 0.06 |
| task_input_tokens:cold-minus-warm | 26,365.33 | 43,404.58 | 17,039.25 | [6,915.42, 24,477.58] | 39.26% | 0.06 | 0.06 |
| task_output_tokens:cold-minus-warm | 628.08 | 564.42 | -63.67 | [-180.67, 59.17] | -11.28% | 0.22 | n/a |
| duration_ms:cold-minus-warm | 22,212.33 | 26,452.67 | 4,240.33 | [-2,845.75, 11,162.67] | 16.03% | 0.44 | n/a |

## Learning-chain validity (12 warm cases)

- source-failure: 12/12
- lesson-written: 12/12
- lesson-covers-target: 11/12
- lesson-attributed: 11/12

## LLM contribution reviewer behaviour

- review model calls: 0
- review tokens (in/out): 0 / 0
- review sources: {}
- verdicts: {}
- downgrade reasons: {}

## Interpretation boundary

- Six synthetic families, single model, one day: directions and
  mechanism behaviour, not powered effect sizes.
- Savings are measured on target turns only; the warm learning
  turn's cost is reported separately in turn-level.csv.
- Lesson 'covers target' is a content containment check, not a
  semantic correctness proof.
