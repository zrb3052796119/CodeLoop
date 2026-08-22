# Statistical Appendix

## Methods

Each family is averaged across three provider blocks. Paired differences are cold minus Memory. Exact two-sided Wilcoxon signed-rank tests enumerate all sign assignments after zero removal. Confidence intervals are deterministic percentile intervals from 20,000 family-cluster bootstrap resamples. The two primary endpoints form one Holm family; four secondary endpoints form a separate exploratory Holm family.

## Family-level results

| Metric | Cold mean | Memory mean | Mean saving | 95% CI saving | Relative reduction | Wilcoxon p | Holm p | Sign +/−/0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Repository tool calls | 11.50 | 7.50 | 4.00 | [1.56, 6.89] | 34.8% | 0.0093 | 0.0186 | 10/2/0 |
| Task input tokens | 85913.53 | 51631.58 | 34281.94 | [4221.56, 77963.90] | 39.9% | 0.0522 | 0.0522 | 8/4/0 |
| Task model calls | 9.97 | 7.03 | 2.94 | [0.69, 5.67] | 29.5% | 0.0400 | 0.1201 | 8/4/0 |
| Task output tokens | 1408.39 | 769.03 | 639.36 | [51.64, 1479.45] | 45.4% | 0.1294 | 0.2588 | 9/3/0 |
| Tool failures | 2.42 | 0.83 | 1.58 | [0.06, 3.50] | 65.5% | 0.1504 | 0.2588 | 9/3/0 |
| Elapsed duration (ms) | 20500.00 | 11786.92 | 8713.08 | [2083.94, 17180.21] | 42.5% | 0.0161 | 0.0645 | 10/2/0 |

## Missingness, multiplicity and limits

There are no missing Run Journals or provider-usage records across 72 formal target Turns. No formal observation was excluded or replaced. The separate four-case smoke is design-development evidence only. Success counts are gates rather than post-hoc superiority tests. Bootstrap intervals capture family variation within this suite, not future model-version or repository drift.
