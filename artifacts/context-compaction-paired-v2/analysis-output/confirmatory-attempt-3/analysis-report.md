# Confirmatory Context-Compaction Analysis

## Analysis question

Does CodeLoop's canonical context compactor reduce cumulative provider input
tokens over five carried-history turns, relative to a matched high-window
control, without losing externally checked task outcomes or protected state?

The inference unit is the generated task-family-by-replicate pair (`n=12`).
The 24 cases use four task families, three replicates, the same frozen
`deepseek-v4-pro` route, and 120 top-level turns. Provider-reported usage is the
token authority. No user repository content was sent.

## Key findings

### Input-token efficiency

Canonical compaction used 4,288,270 cumulative input tokens versus 6,011,480
for control: 1,723,210 fewer tokens, or a 28.67% aggregate reduction. The
paired percentage reduction had mean 21.59% (SD 25.36%), median 22.65%, and IQR
14.30% to 32.35%. Eleven of twelve pairs favored canonical compaction; the
range was -34.96% to 72.92%.

The paired control-minus-canonical difference averaged 143,601 tokens (SD
206,779; median 101,406). A fixed-seed paired bootstrap gave a 95% interval of
50,936 to 268,095 tokens for the mean difference and 7.50% to 35.11% for the
mean percentage reduction. The two-sided exact sign-permutation result was
`p=0.00537`; paired rank-biserial effect size was `0.846`.

The gap was not reliable at turn one. It grew across turns and reached the
Holm-adjusted threshold at turn four: mean control-minus-canonical differences
were 4,035, 47,274, 77,332, 129,471, and 143,601 tokens after turns one through
five. Holm-adjusted `p` values for turns one through four were 0.680, 0.512,
0.261, and 0.039. This supports an amortization pattern rather than immediate
first-turn savings.

### Quality guardrail

Canonical passed 9/12 cases and control passed 10/12. Discordance was one
canonical-only pass and two control-only passes; exact McNemar `p=1.0`. This
small experiment neither establishes a condition difference in success nor
supports equivalence/non-inferiority.

All five failed cases missed only the `canonical-success` oracle after remote
model `IncompleteRead` failures. Their executable tests, exact goal marker,
constraint marker, rejected-approach marker, and run completion checks still
passed. Thus protected-state fidelity held in all 24 cases, including all 12
canonical cases, but the stricter co-primary task-success gate did not pass.
The token reduction therefore cannot be described as proven quality-neutral.

### Operational trade-offs

- Effective/failed compactions: canonical 41/29; control 0/0.
- Model calls: canonical 407, control 371 (9.70% more under canonical).
- Tool calls: canonical 259, control 278 (6.83% fewer under canonical).
- Output tokens: canonical 191,597, control 209,275 (8.45% fewer).
- Model failures: canonical 8, control 3.
- Overflow recoveries: 0 in both conditions.
- Aggregate duration was 5.20% lower under canonical, but the paired median
  difference favored control by 30.9 seconds. A single long control trajectory
  dominated the aggregate, so no speed claim is supported.

The 29 failed compactions and repeated summary timeouts show that token
efficiency is currently stronger than compaction-path reliability. Circuit
breaking prevented observable failure oscillation and every protected marker
survived, but summary/provider retry behavior remains the clearest next repair.

## What changed in the evidence

The deterministic 60-case stress gate had already shown mechanism fidelity but
could not measure provider usage. The complete live run now supplies paired,
provider-observed evidence that savings generally accumulate over turns. It
also overturns the stronger draft claim that compaction is quality-neutral:
the strict success gate contains two control-only discordances and five total
network-affected case failures.

## Claim candidates

- Claim: Canonical compaction reduced provider input tokens in this frozen
  synthetic multi-turn suite.
  - Source evidence: 12 complete matched pairs; 4.288M vs 6.011M input tokens;
    median paired reduction 22.65%; exact `p=0.00537`; rank-biserial 0.846.
  - Allowed wording: "Across 12 matched synthetic coding pairs, canonical
    compaction reduced cumulative provider input tokens by 28.7% in aggregate,
    with a 22.7% median paired reduction."
  - Forbidden stronger wording: "Compaction always reduces tokens" or "These
    percentages generalize to arbitrary repositories and models."
  - Uncertainty: one pair regressed 35.0%; the bootstrap interval is wide and
    all runs used one provider route.
  - Next check: repeat on another provider/model and larger real-task families.
  - Decision: keep.

- Claim: Savings amortize over carried-history turns.
  - Source evidence: the mean paired gap grew from 4,035 tokens at turn one to
    143,601 at turn five; the turn-four contrast survived Holm correction.
  - Allowed wording: "In this suite, the token advantage emerged over repeated
    turns and became statistically detectable by turn four."
  - Forbidden stronger wording: "Four turns is a universal break-even point."
  - Uncertainty: model trajectories and task families were heterogeneous.
  - Next check: vary history size and turn count with the same paired protocol.
  - Decision: keep.

- Claim: Compaction is quality-neutral.
  - Source evidence: canonical 9/12 vs control 10/12; one canonical-only and two
    control-only passes; exact McNemar `p=1.0`; all five failures coincided with
    remote `IncompleteRead` events.
  - Allowed wording: "All executable tests and protected markers survived, but
    the strict case-success gate did not establish quality neutrality."
  - Forbidden stronger wording: "Compaction has no quality cost" or "The
    failures can be excluded because they were network errors."
  - Uncertainty: the experiment was not powered or designed as a
    non-inferiority trial, and provider instability affected both conditions.
  - Next check: harden retry/fallback behavior, then preregister a fresh
    reliability-aware replication without replacing this run.
  - Decision: discard the strong claim; keep the bounded observation.

## Decision

Use the token-efficiency result in the project portfolio with its paired sample
size, uncertainty, and quality caveat. Do not advertise "lossless" or
"quality-neutral" live behavior. Prioritize provider/summary retry isolation
and failed-compaction reduction before claiming the module is fully A-grade.

