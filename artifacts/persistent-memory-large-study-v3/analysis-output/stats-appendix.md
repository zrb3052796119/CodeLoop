# Statistical Appendix

## Methods

For each family, condition metrics were averaged across three provider blocks. Paired family differences are cold minus Memory. Exact two-sided Wilcoxon signed-rank tests use midranks for ties and enumerate every sign assignment; exact sign tests ignore zero differences. Hodges-Lehmann estimates use all Walsh averages. Confidence intervals are percentile intervals from 20,000 deterministic family-cluster bootstrap resamples. The two primary Wilcoxon tests are Holm-adjusted together; the four secondary tests form a separate exploratory Holm family.

## Exact family-level results

| Metric | Cold mean | Memory mean | Mean saving | HL saving | 95% CI saving | Wilcoxon p | Holm p | Rank-biserial | Sign +/−/0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Repository tool calls | 5.00 | 1.04 | 3.96 | 4.00 | [3.38, 4.48] | 0.000031 | 0.000061 | 1.000 | 16/0/0 |
| Task input tokens | 32077.88 | 13602.31 | 18475.56 | 18096.25 | [15377.63, 21412.04] | 0.000031 | 0.000061 | 1.000 | 16/0/0 |
| Task model calls | 4.81 | 2.04 | 2.77 | 2.67 | [2.31, 3.21] | 0.000031 | 0.000122 | 1.000 | 16/0/0 |
| Task output tokens | 289.88 | 114.46 | 175.42 | 182.83 | [150.29, 197.42] | 0.000031 | 0.000122 | 1.000 | 16/0/0 |
| Tool failures | 0.02 | 0.00 | 0.02 | 0.00 | [0.00, 0.06] | 1.0000 | 1.0000 | 1.000 | 1/0/15 |
| Elapsed duration (ms) | 6828.10 | 3137.44 | 3690.67 | 3691.17 | [3064.48, 4296.98] | 0.000031 | 0.000122 | 1.000 | 16/0/0 |

## Multiplicity and estimands

The primary family contains repository tool calls and task input tokens. Model calls, output tokens, tool failures and elapsed duration are exploratory. Success and injection are reported as pre-registered gates and exact counts, not converted into a post-hoc superiority test. Relative reduction is `(cold mean − Memory mean) / cold mean`; absolute saving remains the more stable estimand when the cold denominator is small.

## Missingness and exclusions

There is no missing provider-usage or journal record among the 96 target Turns. No observation was excluded. The one oracle failure is retained. The failed V1 and V2 smoke manifests are design-development artifacts and are not pooled into V3 efficacy estimates.

## Dependence and limitations

The 48 block pairs are displayed descriptively but not treated as independent. The 16 families share the same high-level read-and-report mechanism, so even family-level inference may understate dependence relative to a heterogeneous real coding benchmark. Bootstrap intervals reflect sampled family variation, not prompt, model-version or deployment drift.
