# DeepSeek Reflection Calibration Adjudication

No parser repair, reference mapping, Validator relaxation, or memory-path change was introduced.

| Case | Baseline | Calibrated | Adjudication |
|---|---|---|---|
| `holdout-redacted-secret-error-028` | invalid_semantic_key | accepted | Schema/prompt ambiguity reproduced; calibrated key parsed. The exact error_pattern became valid while the generated verification_rule remained rejected. |
| `holdout-verified-recovery-007` | invalid_semantic_key | accepted | Schema failure removed, but only the error_pattern passed. The expected recovery is still a claim-level false negative; no Validator weakening is justified. |
| `holdout-causal-trap-025` | all_llm_claims_rejected | accepted | Grounded error_pattern passed and matched; the unstable verification_rule was rejected. This is the intended fail-closed split. |
| `holdout-timeout-fallback-032` | all_llm_claims_rejected | accepted | All-rejected fallback was removed, but the expected recovery still failed grounding while an extra error_pattern passed. Claim precision remains insufficient. |
| `holdout-provider-fallback-033` | all_llm_claims_rejected | accepted | Preserving DecisionEvidence wording changed all-rejected to one valid, matched decision. |
| `holdout-unverified-recovery-024` | invalid_semantic_key | llm_value_rejected | Replay proved error_pattern could launder value from an unverified failed recovery. ValueGate now rejects unverified_recovery_context. |
| `holdout-partial-recovery-008` | llm_value_rejected | llm_value_rejected | Independent replay showed the same laundering pattern for unknown outcome; the same general ValueGate rule rejects it. |

## Decision

- Keep Parser fail closed; no normalization, retry, or ID repair.
- Keep Validator thresholds unchanged; its rejections prevented unsupported recovery/root-cause persistence.
- Keep the new ValueGate condition because two independent captured responses reproduce the same bypass pattern and controls remain accepted.
- Do not enter 200-500 shadow expansion: negative sample count and Validator precision gates fail.
