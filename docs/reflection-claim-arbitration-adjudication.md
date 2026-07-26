# Reflection Claim Arbitration Adjudication

Strict exact scores remain unchanged. Adjudicated scoring additionally recognizes manually labeled legal split/secondary expressions and deterministic same-chain redundancy.

## Exact/Adjudicated Differences

- `precision-positive-cross-decision-110`: exact FP/FN 2/1; adjudicated FP/FN 0/0.
- `precision-positive-lock-recovery-103`: exact FP/FN 1/0; adjudicated FP/FN 0/0.
- `precision-positive-multilingual-recovery-102`: exact FP/FN 1/0; adjudicated FP/FN 0/0.
- `precision-positive-two-verifications-104`: exact FP/FN 1/0; adjudicated FP/FN 0/0.

## Arbitration

- Frozen controls `holdout-verified-recovery-007` and `holdout-timeout-fallback-032`: production `gap_fill` keeps the Rule recovery without an LLM call; explicit `replace` with the weaker error-pattern fixture records a regression.
- `precision-negative-generic-warning-009` selected `llm_gap_fill`; gap attempt=True; gap success=False; replace regression=False.
- `precision-negative-unverified-failed-001` selected `llm_gap_fill`; gap attempt=True; gap success=False; replace regression=False.
- `precision-negative-unverified-modification-006` selected `llm_gap_fill`; gap attempt=True; gap success=False; replace regression=False.
- `precision-positive-gap-recovery-105` selected `llm_gap_fill`; gap attempt=True; gap success=False; replace regression=False.
- `precision-positive-gap-recovery-106` selected `llm_gap_fill`; gap attempt=True; gap success=False; replace regression=False.
