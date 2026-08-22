# Analysis Report: Non-Path Persistent Memory

## Analysis contract

- Design: 12 independent synthetic families in four non-path strata, three randomized provider blocks, 36 paired target comparisons and 72 target Turns.
- Inferential unit: family (`n=12`); blocks are nested stochastic repeats.
- Target success requires the external content/command/marker gates and a successful `run_command` in the target Turn. An independent post-Run verifier cannot conceal failure to verify inside the agent Turn.
- All first-attempt formal-study observations are included. The four-case development smoke is not pooled.

## Outcome

Memory achieved strict target execution success in 32/36 Turns (88.9%) versus 28/36 (77.8%) for cold controls. Full case-chain success was 32/36 versus 28/36. Memory was injected in 36/36 intended target Turns and in 0 cold Turns.

The result is favorable but not perfect: Memory had 4 strict failures and cold had 8. A pre-registered exact-source-string oracle rejected two semantically correct `expired-session` fixes—one per condition—even though the independent unittest, in-Turn verifier and marker passed. The transparent semantic sensitivity count is therefore 33/36 versus 29/36; the strict count remains primary. The other failures were mostly command-shape or working-directory mistakes rejected by the permission layer.

## Results by lesson stratum

- `code-repair`: Memory 8/9, cold 8/9; mean tool calls 7.67 vs 9.56.
- `command-recovery`: Memory 9/9, cold 8/9; mean tool calls 2.67 vs 7.44.
- `project-constraint`: Memory 6/9, cold 7/9; mean tool calls 11.33 vs 11.00.
- `verification-rule`: Memory 9/9, cold 5/9; mean tool calls 8.33 vs 18.00.

## Primary efficiency endpoints

- Repository tool calls: 34.8% reduction (family-cluster 95% CI 15.5% to 50.4%); exact Wilcoxon p=0.0093, Holm-adjusted p=0.0186.
- Task input tokens: 39.9% reduction (family-cluster 95% CI 7.2% to 60.3%); exact Wilcoxon p=0.0522, Holm-adjusted p=0.0522.

## Secondary endpoints

- Task model calls: 29.5% reduction (family-cluster 95% CI 8.5% to 46.0%); exact Wilcoxon p=0.0400, Holm-adjusted p=0.1201.
- Task output tokens: 45.4% reduction (family-cluster 95% CI 5.7% to 66.8%); exact Wilcoxon p=0.1294, Holm-adjusted p=0.2588.
- Tool failures: 65.5% reduction (family-cluster 95% CI 4.3% to 90.4%); exact Wilcoxon p=0.1504, Holm-adjusted p=0.2588.
- Elapsed duration (ms): 42.5% reduction (family-cluster 95% CI 13.7% to 61.1%); exact Wilcoxon p=0.0161, Holm-adjusted p=0.0645.

## Lesson creation and reuse

All 24/24 learned-condition creation Turns wrote a durable lesson; 24/24 also met the strict learning evidence gate. A creation Turn used a mean 41728 task input tokens. Treating the whole useful creation Turn as overhead gives a descriptive break-even of 1.10 comparable reuses when the denominator is positive.

The stored examples are genuinely non-path: corrected command invocations, code-repair actions, project compatibility constraints and required verification commands. This study therefore extends the earlier path-only evidence boundary, but only for these synthetic task forms.

## Interpretation boundary

The randomized paired result supports the claim that relevant approved Memory can improve non-path engineering work in these four strata. It does not prove general benefit on arbitrary repositories, multi-file architecture work or unseen tool families. Provider behavior was noisy, the same high-level unittest mechanism appears across families, and 12 families give limited power for small effects. Safety denials are correct behavior, while repeated attempts after denial remain an agent-efficiency defect.

## Reproducibility authority

- Manifest: `artifacts/non-path-memory-study-v2/manifest.json` (SHA-256 `26c1c63ccdf737860bbc3a287b7848827f26715207ba2e4ff1e1fdb06287b85f`)
- First-attempt result: `artifacts/non-path-memory-study-v2/full-first-attempt.json` (SHA-256 `56b7c9375ec26a693a3fcc37298fe4c6a75fbd7014d3deaa9ab6f1d218a2ca44`)
- Bootstrap: 20,000 family-cluster samples, seed `20260822`.
