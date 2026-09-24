# Statistical Appendix

## Design and provenance

- Unit of analysis: matched task-family-by-replicate pair.
- Sample size: 12 pairs / 24 cases / 120 top-level turns.
- Conditions: canonical 14,000-token context window versus matched 128,000-token
  no-compaction/high-window control.
- Runtime: OpenAI-compatible DeepSeek endpoint, frozen model
  `deepseek-v4-pro`.
- Manifest SHA-256: `60a45966a78545eed31e2bf6c54963b4e5c2e7ac5da0b5f8bd7cee3cf65a5893`.
- Source SHA-256: `0aad1ad7a04c5698210eaf201f2aa9f6b63dcc94f101bf003b7b421523e95375`.
- Result SHA-256: `da9f576fa002c941c30003dbccbd8ac651c3be27e18d339adec3157458a00660`.
- Usage audit: complete for all cases and identical between sealed journals and
  public results.

## Methods

Small-sample paired differences were not assumed normal. The primary token
contrast uses a two-sided exact sign-permutation test over the 12 paired
control-minus-canonical differences. Effect size is paired rank-biserial
correlation. Confidence intervals use 20,000 paired bootstrap samples with
fixed seed `20260825`. Turn-one through turn-four secondary contrasts use Holm
correction; turn five is the preregistered primary endpoint. Binary success
discordance uses the exact McNemar test.

No case, outlier, model failure, or negative pair was excluded. Attempt 1
(balance exhaustion) and attempt 2 (pre-call runtime-profile mismatch) are
separate infrastructure artifacts and are not pooled into attempt 3.

## Primary input-token outcome

| Statistic | Canonical | Control | Paired control - canonical |
|---|---:|---:|---:|
| Total | 4,288,270 | 6,011,480 | 1,723,210 |
| Mean | 357,356 | 500,957 | 143,601 |
| SD | 97,799 | 217,734 | 206,779 |
| Median | 355,247 | 405,126 | 101,406 |
| Q1 | 303,641 | 387,367 | 57,239 |
| Q3 | 389,534 | 584,860 | 144,162 |
| Min / max | 181,847 / 535,438 | 290,067 / 987,783 | -102,645 / 720,271 |

- Aggregate reduction: 28.67%.
- Paired percentage reduction: mean 21.59%, SD 25.36%, median 22.65%,
  IQR 14.30% to 32.35%, range -34.96% to 72.92%.
- Bootstrap 95% CI, mean absolute difference: 50,936 to 268,095 tokens.
- Bootstrap 95% CI, mean paired percentage reduction: 7.50% to 35.11%.
- Exact two-sided sign-permutation: `p=0.00537109375`.
- Paired rank-biserial: `0.8461538462`.

## Per-turn cumulative differences

Positive values favor canonical compaction.

| Turn | Mean difference | Median | Exact p | Holm-adjusted p |
|---:|---:|---:|---:|---:|
| 1 | 4,035 | -635 | 0.68018 | 0.68018 |
| 2 | 47,274 | 36,394 | 0.25586 | 0.51172 |
| 3 | 77,332 | 54,596 | 0.08691 | 0.26074 |
| 4 | 129,471 | 96,687 | 0.00977 | 0.03906 |
| 5 | 143,601 | 101,406 | 0.00537 | primary endpoint |

## Success and fidelity

| Outcome | Canonical | Control |
|---|---:|---:|
| Strict case success | 9/12 | 10/12 |
| Exact executable tests | 12/12 | 12/12 |
| Goal marker recall | 12/12 | 12/12 |
| Constraint marker recall | 12/12 | 12/12 |
| Rejected-approach recall | 12/12 | 12/12 |

Discordance: one canonical-only pass and two control-only passes; exact McNemar
`p=1.0`. The five failed cases each missed only `canonical-success` after a
remote model `IncompleteRead`; no failed case lost tests or protected markers.
The design has no non-inferiority margin, so a non-significant McNemar result is
not evidence of equivalence.

## Supporting metrics

| Metric | Canonical | Control | Aggregate change |
|---|---:|---:|---:|
| Model calls | 407 | 371 | +9.70% |
| Tool calls | 259 | 278 | -6.83% |
| Output tokens | 191,597 | 209,275 | -8.45% |
| Duration | 3,060.3 s | 3,228.1 s | -5.20% aggregate |
| Model failures | 8 | 3 | +5 failures |
| Effective compactions | 41 | 0 | mechanism check |
| Failed compactions | 29 | 0 | reliability cost |
| Overflow recoveries | 0 | 0 | none observed |

Duration does not support a speed claim: the paired median
control-minus-canonical difference was -30.9 seconds, while one 1,037.9-second
control case dominated the aggregate total.

## Limitations

- One provider route and model were used.
- Remote `IncompleteRead` failures were concentrated in part of the run and
  affected both conditions; order alternation reduces but cannot remove this
  time-varying nuisance.
- Four generated task families do not represent all real coding work.
- The bootstrap interval describes variation within these 12 frozen pairs, not
  future model versions or repositories.
- Failed compactions were frequent even though circuit breaking prevented
  observed overflow/retry oscillation.

