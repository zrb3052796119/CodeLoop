# CodeLoop Context-Compaction Experiment Study

## Status and scope

This document was frozen as a preregistration before inspecting any result
from the new live paired suite. The study asks a deliberately narrow question:
when a coding conversation keeps growing across turns, does CodeLoop's
canonical context compactor reduce provider-observed cumulative input tokens
without reducing externally verified task success or losing protected state?

The live suite sends only generated repositories, generated conversation
history, and generated marker strings. It does not send source code or prompts
from a user project.

### Pilot amendment (recorded after V1 pilot, before confirmatory execution)

The original four-case V1 pilot did not pass its quality gate. Both canonical
cases compacted and both controls did not. Canonical reduced provider input
tokens by 26.0% and 19.9% in the two pairs, but
`port-normalization-r1-canonical` failed the combined protected-state recall
oracle while its control passed. One summary request also timed out and used
the heuristic fallback. The failed manifest, result, sealed journals, and
analysis are retained under `artifacts/context-compaction-paired-v1/` and
`analysis-output/context-compaction-paired-v1/`; they are not replaced or
included in confirmatory inference.

This pilot exposed two defects before the confirmatory run:

1. A stochastic or fallback summary could omit an explicit goal, constraint,
   rejected approach, or exact identifier.
2. One combined three-marker oracle identified a retention failure but could
   not identify which state class failed.

V2 adds a bounded deterministic critical-state anchor section after either a
model or heuristic summary. It extracts generic goal/constraint/decision/
rejection/verification sentences and exact high-entropy identifiers; it is not
special-cased to this experiment's marker strings. V2 also splits goal,
constraint, and rejected-approach recall into three independent oracles. A new
independent replicate-0 pilot must pass before the original 24-case
confirmatory matrix is attempted. V1 remains the pre-repair failure baseline.

## Why the previous evidence was insufficient

The existing deterministic gate is useful: 12 cases force one to five
production compactions and check goals, verified facts, rejected approaches,
the latest user request, loaded Skill output, task-ledger state, and atomic
tool pairs. It proves invariants, but it cannot report provider-billed tokens.

The previous strongest live efficiency result was one replay of one
large-file task: 257,088 to 49,541 cumulative input tokens. That run is retained
as historical evidence, but one stochastic replay cannot separate compaction
from a different model trajectory. The new experiment therefore uses matched
conditions, multiple task families, repeated runs, and per-turn crossover
curves.

## Frozen live design

### Conditions

- `canonical`: the normal production compactor with a 14,000-token local
  context window and the standard 85% high-water policy.
- `high-window-control`: the identical production runtime with a 128,000-token
  local context window. The generated workload is kept below that threshold,
  so this is a no-compaction control without replacing or monkeypatching the
  compactor.

Within a pair, condition is the only intended difference. Repository files,
initial history, prompts, oracles, primary provider route, model, permissions,
and turn budgets are identical. The manifest binds the credential-free runtime
identity to the OpenAI-compatible DeepSeek endpoint and `deepseek-v4-pro` model.

### Experimental units and ordering

The confirmatory matrix contains four coding-task families, three generated
replicates per family, and two conditions: 12 matched pairs / 24 cases. Every
case contains five carried-history turns, for 120 top-level agent turns.

Manifest order alternates which condition runs first by replicate. This does
not make remote inference deterministic, but it prevents every canonical run
from systematically occurring earlier or later than its control.

### Task families

1. Port normalization: repair a boundary/type bug while preserving an API
   contract and a rejected clamping approach.
2. Retry delay: correct one-based exponential backoff, validation, and a cap
   without adding the explicitly rejected jitter behavior.
3. Record parsing: fix comments, empty keys, split-once behavior, and duplicate
   semantics.
4. Cache-key canonicalization: normalize method/path/query ordering without
   applying the explicitly rejected URL decoding step.

Each synthetic repository includes executable `unittest` checks. The final
turn also requires exact recall of a goal, a constraint, and a rejected
approach placed at separated positions in the earliest generated discussion.
This prevents a lossy compressor from receiving credit merely because the
final files happen to pass.

### Turn protocol

1. Inspect the repository, implement the repair, and run tests.
2. Perform an adversarial contract review; fix any issue and rerun tests.
3. Check an edge case anchored in the original discussion and verify again.
4. Inspect the final implementation and run the complete test suite.
5. Call no tools; return the three exact protected markers and the verification
   state in one short response.

### Pilot and stopping rule

A four-case operational pilot (two pairs from two families) is run first. It is
used only to confirm all of the following: both conditions reach the provider,
usage is present, every external oracle passes, canonical cases compact at
least once, controls compact zero times, and journal telemetry can be joined.
Pilot estimates are not pooled into confirmatory statistics.

If those checks pass, the frozen 24-case confirmatory manifest is run exactly
once. Completed failures are retained. A run may be resumed only to execute
manifest cases that have no sealed result; a bad completed result is never
silently replaced. The study stops after the 12 preregistered pairs regardless
of effect size or significance.

## Metrics and analysis plan

### Co-primary outcomes

1. Cumulative provider-observed input tokens through turn five.
2. External-oracle case success, including executable tests and final marker
   recall.

Token reduction is not considered beneficial if the canonical condition loses
a task that its paired control passes.

### Secondary outcomes

- cumulative input tokens after every turn and the first crossover turn;
- output tokens, model calls, paired tool completions, and wall-clock time;
- effective and failed compaction counts;
- estimated tokens released by effective compactions;
- context-overflow recovery observations and model failures;
- exact protected-marker recall.

Provider input/output usage is the authority for efficiency claims.
`context.compacted.tokensFreed` is explicitly labeled an internal estimate and
is used only to explain mechanism behavior.

### Statistics

The inference unit is the matched family-by-replicate pair (`n=12`), never an
individual turn or model call. The report will include every raw pair, means
and standard deviations, medians and interquartile ranges, the median paired
percentage reduction, and a fixed-seed paired bootstrap 95% confidence
interval. Because the small sample does not justify a normality assumption,
the primary token comparison uses a two-sided exact paired sign-permutation
test and paired rank-biserial effect size. Secondary token contrasts use Holm
correction. Paired success discordance is reported with the exact McNemar
test when discordant pairs exist; otherwise the report states the observed
zero-discordance result without claiming equivalence.

## Deterministic evidence tier

Separately from live inference, the study reruns the frozen 12-case production
fidelity gate and adds a scale/round stress matrix. It reports estimated token
reduction and checks summary-of-summary continuity, task-ledger survival,
latest-user reinsertion, loaded-Skill survival, and atomic tool-turn integrity.
An exact pre-upgrade Git revision may be evaluated as historical counterexample
evidence, but it is labeled historical rather than treated as a randomized
control.

## Results

### Deterministic mechanism evidence

The frozen 12-case fidelity gate passed 12/12 with 100% marker recall,
summary-chain continuity, latest-user retention, loaded-Skill retention,
task-ledger retention, and tool-pair integrity.

The new stress matrix passed 60/60 cases across 12/24/48/96 messages per round,
one to five repeated compactions, and head/middle/boundary marker placement.
All six invariants remained at 100%. Relative to retaining the same generated
transcript without compaction, final locally estimated context tokens fell by a
median 92.0% (range 52.0% to 98.3%). This is mechanism evidence, not a claim
about provider-billed tokens.

### V1 pilot failure and V2 repair pilot

V1 produced real token savings in both pairs (26.0% and 19.9%) but failed its
quality gate because one canonical case lost protected state. That negative
result is retained rather than averaged away.

After adding bounded critical-state anchors, the independent V2 pilot passed
4/4 cases and every split recall oracle. Canonical compacted eight times;
controls compacted zero times. Efficiency remained highly variable: one pair
saved 29.0%, while a long canonical model/tool trajectory made the other pair
use 58.7% more input tokens. The pilot therefore validated the mechanism and
quality repair, but did not establish an efficiency effect.

### Confirmatory attempt lineage and deviations

The first frozen confirmatory attempt completed ten clean cases (five pairs)
before DeepSeek returned `Insufficient Balance`. Its 13 sealed results,
including three balance-affected failures, remain under
`confirmatory-results.json`; the earlier five-pair figures remain explicitly
interim and are not pooled below.

After credit restoration, attempt 2 exposed configuration drift before any
primary-model call: the frozen manifest required `deepseek-v4-pro`, while the
user environment selected `deepseek-v4-flash`. Sixteen cases sealed as
pre-call `RuntimeError` failures with zero model calls and no provider usage.
The run was stopped, preserved under `confirmatory-attempt-2-results.json`, and
excluded from efficacy analysis. Attempt 3 used a process-only model override
to satisfy the original frozen profile; neither the manifest nor the user's
`.env` was edited.

Attempt 3 completed all 24 cases / 12 pairs. Its manifest, source, and result
SHA-256 identities are respectively `60a45966...a5893`,
`0aad1ad7...95375`, and `da9f576f...0660`. All journal usage matches the public
result, every canonical case compacted, and every control compacted zero times.

### Complete 12-pair confirmatory result

| Metric | Canonical | Control | Difference |
|---|---:|---:|---:|
| Strict case success | 9/12 | 10/12 | 1 canonical-only, 2 control-only |
| Executable tests and all protected markers | 12/12 | 12/12 | no loss observed |
| Cumulative input tokens | 4,288,270 | 6,011,480 | 1,723,210 fewer (28.67%) |
| Mean input tokens per case | 357,356 | 500,957 | 143,601 fewer |
| Model calls | 407 | 371 | 36 more (9.70%) |
| Tool calls | 259 | 278 | 19 fewer (6.83%) |
| Output tokens | 191,597 | 209,275 | 17,678 fewer (8.45%) |
| Wall time | 3,060.3 s | 3,228.1 s | 167.8 s lower aggregate |
| Effective / failed compactions | 41 / 29 | 0 / 0 | mechanism / reliability |
| Model failures | 8 | 3 | 5 more under canonical |
| Context-overflow recoveries | 0 | 0 | none observed |

The paired percentage reduction had mean 21.59%, median 22.65%, IQR 14.30%
to 32.35%, and range -34.96% to 72.92%; 11/12 pairs favored canonical.
The fixed-seed paired bootstrap 95% interval was 50,936 to 268,095 tokens for
the mean absolute difference and 7.50% to 35.11% for mean percentage
reduction. The two-sided exact sign-permutation result was `p=0.00537`, with
paired rank-biserial effect size `0.846`.

The mean cumulative control-minus-canonical difference was 4,035 tokens after
turn one, then 47,274, 77,332, 129,471, and 143,601 after turns two through
five. Holm-adjusted secondary `p` values for turns one through four were
0.680, 0.512, 0.261, and 0.039. The live token advantage therefore appeared as
an amortized multi-turn effect rather than a reliable first-turn saving.

### Quality and reliability boundary

The token result passes its paired statistical gate, but the co-primary strict
success gate does not establish quality neutrality. Canonical passed 9/12
cases and control 10/12; exact McNemar `p=1.0`, which is neither evidence of a
difference nor evidence of equivalence.

All five failed cases missed only `canonical-success` after remote model
`IncompleteRead` events. Every case still completed its runs, passed executable
tests, and recalled the exact goal, constraint, and rejected-approach markers.
Thus the critical-state-anchor repair held across all 12 canonical cases, but
provider/summary reliability remains a material weakness. Twenty-nine failed
compaction attempts and several summary timeouts were contained by fallback
and circuit breaking without overflow oscillation, yet they increased model
calls and make a "lossless" live claim inappropriate.

Aggregate wall time also should not be presented as a speedup: its 5.2%
reduction was dominated by one very long control trajectory, while the paired
median control-minus-canonical duration was -30.9 seconds.

Figures and strict analysis bundle:

- [Mean cumulative input tokens by turn](../artifacts/context-compaction-paired-v2/analysis-output/confirmatory-attempt-3/cumulative-input-tokens.svg)
- [All 12 paired totals](../artifacts/context-compaction-paired-v2/analysis-output/confirmatory-attempt-3/paired-total-input-tokens.svg)
- [Analysis report](../artifacts/context-compaction-paired-v2/analysis-output/confirmatory-attempt-3/analysis-report.md)
- [Statistical appendix](../artifacts/context-compaction-paired-v2/analysis-output/confirmatory-attempt-3/stats-appendix.md)

### Bounded conclusion

The complete evidence supports three bounded claims:

1. The deterministic 60-case stress matrix and all 12 live canonical cases
   retained the tested goals, constraints, rejected approaches, latest
   instructions, task-ledger state, Skills, and atomic tool turns.
2. In the complete 12-pair live suite, canonical compaction reduced provider
   input tokens by 28.67% in aggregate with a 22.65% median paired reduction;
   the advantage accumulated over repeated turns and the exact paired token
   test was significant.
3. The efficiency benefit is not free: canonical used 9.70% more model calls,
   recorded more model failures, and did not establish strict success
   equivalence. Remote retry/fallback hardening is the next engineering target.

The project may present this as statistically supported token-efficiency
evidence on a frozen synthetic multi-turn benchmark. It must not claim that
compaction always saves tokens, guarantees quality neutrality, or generalizes
unchanged to every model and repository.
