# Memory pollution & self-correction — analysis report

## Analysis contract

- Inferential unit: task family (n=6); arms compared within families.
- Primary contrasts: clean vs polluted-rule, clean vs polluted-off,
  polluted-off vs polluted-rule, at rounds 1 and 3, for tool calls and
  task input tokens. Exact Wilcoxon with Holm correction over the
  primary family; family-cluster percentile bootstrap 95% CIs.
- Pool endpoints are descriptive rates by arm (no power claim).

## Outcomes

- clean: strict round-3 case success 6/6
- polluted-off: strict round-3 case success 6/6
- polluted-rule: strict round-3 case success 6/6

## Raw per-arm, per-round metrics (family means, n=6)

| arm | round | outcome success | tool calls | tool failures | task model calls | side model calls | input tokens | output tokens | duration s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| clean | 1 | 6/6 | 1.00 | 0.00 | 2.00 | 0.00 | 15465.50 | 244.00 | 12.5 |
| clean | 2 | 6/6 | 1.33 | 0.00 | 2.33 | 0.00 | 18204.67 | 373.00 | 17.3 |
| clean | 3 | 6/6 | 1.00 | 0.00 | 2.00 | 0.00 | 15508.17 | 265.83 | 12.7 |
| polluted-off | 1 | 6/6 | 3.17 | 0.50 | 3.83 | 0.17 | 33244.00 | 1236.33 | 40.4 |
| polluted-off | 2 | 6/6 | 1.50 | 0.00 | 2.33 | 0.00 | 18682.00 | 627.33 | 19.6 |
| polluted-off | 3 | 6/6 | 1.17 | 0.00 | 2.17 | 0.00 | 17138.33 | 368.17 | 13.7 |
| polluted-rule | 1 | 6/6 | 2.00 | 0.17 | 2.67 | 0.17 | 22285.17 | 654.17 | 22.6 |
| polluted-rule | 2 | 6/6 | 2.67 | 0.33 | 3.50 | 0.17 | 29413.50 | 831.83 | 29.8 |
| polluted-rule | 3 | 6/6 | 1.00 | 0.00 | 2.00 | 0.00 | 15734.67 | 360.33 | 15.5 |

## Primary efficiency results

- tool_calls:polluted-rule-minus-clean:round1: mean diff 1.00, 95% CI [0.00, 3.00], exact Wilcoxon p=1.00, Holm p=1.00
- tool_calls:polluted-rule-minus-clean:round3: mean diff 0.00, 95% CI [0.00, 0.00], exact Wilcoxon p=1.00, Holm p=1.00
- tool_calls:polluted-off-minus-clean:round1: mean diff 2.17, 95% CI [0.33, 4.33], exact Wilcoxon p=0.25, Holm p=1.00
- tool_calls:polluted-off-minus-clean:round3: mean diff 0.17, 95% CI [0.00, 0.50], exact Wilcoxon p=1.00, Holm p=1.00
- tool_calls:polluted-rule-minus-polluted-off:round1: mean diff -1.17, 95% CI [-3.83, 1.33], exact Wilcoxon p=0.50, Holm p=1.00
- tool_calls:polluted-rule-minus-polluted-off:round3: mean diff -0.17, 95% CI [-0.50, 0.00], exact Wilcoxon p=1.00, Holm p=1.00
- task_input_tokens:polluted-rule-minus-clean:round1: mean diff 6819.67, 95% CI [186.00, 19985.67], exact Wilcoxon p=0.03, Holm p=0.38
- task_input_tokens:polluted-rule-minus-clean:round3: mean diff 226.50, 95% CI [138.67, 336.33], exact Wilcoxon p=0.03, Holm p=0.38
- task_input_tokens:polluted-off-minus-clean:round1: mean diff 17778.50, 95% CI [2351.83, 34860.00], exact Wilcoxon p=0.03, Holm p=0.38
- task_input_tokens:polluted-off-minus-clean:round3: mean diff 1630.17, 95% CI [200.00, 4388.00], exact Wilcoxon p=0.03, Holm p=0.38
- task_input_tokens:polluted-rule-minus-polluted-off:round1: mean diff -10958.83, 95% CI [-31041.50, 8855.50], exact Wilcoxon p=0.44, Holm p=1.00
- task_input_tokens:polluted-rule-minus-polluted-off:round3: mean diff -1403.67, 95% CI [-4067.33, -16.83], exact Wilcoxon p=0.16, Holm p=1.00

## Pool self-correction endpoints

- staleQuarantinedRate [clean]: n/a
- staleQuarantinedRate [polluted-off]: 0.00
- staleQuarantinedRate [polluted-rule]: 0.00
- staleRenderedRound3Rate [polluted-off]: 0.67
- staleRenderedRound3Rate [polluted-rule]: 0.50
- correctRenderedAnyRoundRate [clean]: 1.00
- correctRenderedAnyRoundRate [polluted-off]: 1.00
- correctRenderedAnyRoundRate [polluted-rule]: 1.00

## Interpretation boundary

- Single block, six families, seeded lessons: this round measures
  mechanism behaviour and directions, not powered effect sizes.
- 'Stale rendered' rates depend on retrieval competition; a stale
  lesson never rendered cannot be corrected by feedback (by design,
  no evidence means no verdict).

## Reproducibility

- Analyzer: scripts/analyze_memory_pollution_study.py
- Bootstrap: 20000 samples, seed 202609091
- turn-level rows: 54; pool rows: 48
