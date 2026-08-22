# Figure Catalog

## Figure 1 — Family-level repository tool calls

- File: `figures/figure-01-family-tool-calls.svg`
- Claim: shows the paired cold and Memory means for each of 16 task families.
- Encodings: x-position is mean repository tool calls over three blocks; orange is cold, blue is Memory, and the joining line is the within-family contrast.
- Caveat: family means hide block-level stochastic variation; exact block rows remain in `pair-level.csv`.

## Figure 2 — Relative efficiency reduction

- File: `figures/figure-02-relative-reduction.svg`
- Claim: summarizes relative reductions in four cost metrics.
- Encodings: the point is the family-pooled percentage reduction and the line is a 95% family-cluster percentile bootstrap interval; positive favors Memory.
- Caveat: percentages can appear large when cold denominators are small, so the statistical appendix also reports absolute savings.

## Figure 3 — Direct-first mechanism matrix

- File: `figures/figure-03-direct-first-heatmap.svg`
- Claim: shows whether each target Turn began with a paired successful `read_file` rather than repository discovery.
- Encodings: rows are task families; columns are block-condition combinations; blue is direct-first and grey is another first action.
- Caveat: journals intentionally omit tool arguments. The matrix proves action type and outcome, not the exact path argument.

All figures are vector SVGs generated deterministically from `family-summary.csv`, `pair-level.csv` and `statistics.json`; no values were transcribed by hand.
