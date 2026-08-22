# Figure Catalog

## Figure 1 — Family-level target tool calls

- File: `figures/figure-01-family-tool-calls.svg`
- Claim: compares paired Memory and cold means for each of 12 families.
- Encoding: x-position is the three-block mean; blue is Memory, orange is cold.
- Caveat: family means hide block-level variance; exact observations remain in `pair-level.csv`.

## Figure 2 — Relative efficiency effects

- File: `figures/figure-02-relative-reduction.svg`
- Claim: summarizes percentage savings and family-cluster uncertainty.
- Encoding: points are pooled relative effects; lines are 95% percentile bootstrap intervals; positive favors Memory.
- Caveat: relative tool-failure effects can be unstable when the cold denominator is small; absolute effects are in the statistical appendix.

## Figure 3 — Strict target success matrix

- File: `figures/figure-03-target-success-heatmap.svg`
- Claim: exposes every pass and failure by family, block and condition.
- Encoding: green requires all target gates plus an in-Turn successful verifier; red means at least one gate failed.
- Caveat: it does not distinguish different failure severities; `turn-level.csv` contains failed-oracle labels and tool metrics.

All figures are deterministic vector SVGs generated from the first-attempt Run Journals; no plotted value was transcribed manually.
