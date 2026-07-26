# Reflection LLM Shadow Accuracy

Independent holdout: **34 cases**. The provider is a deterministic offline scripted fixture; no network model was called.

| Mode / branch | Validator claim P | R | F1 | Value P | R | F1 | Low-value false-write |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rule | 39.4% | 54.2% | 45.6% | 100.0% | 91.3% | 95.5% | 0.0% |
| llm_shadow / rule | 39.4% | 54.2% | 45.6% | 100.0% | 91.3% | 95.5% | 0.0% |
| llm_shadow / LLM | 88.2% | 62.5% | 73.2% | 100.0% | 60.9% | 75.7% | 0.0% |
| llm with fallback | 92.0% | 95.8% | 93.9% | 100.0% | 95.7% | 97.8% | 0.0% |

## Safety And Validity

- Validator unsupported accepted claims: **0**
- Invalid evidence references after parsing/validation: **0**
- Epistemic mismatches accepted: **0**
- Forbidden accepted claims: **0**
- Candidate root-cause overclaims: **1**; persistable root-cause overclaims: **0**
- Eligibility/call/fallback rates: **70.6% / 64.7% / 52.9%**
- Scripted latency average/median/P95: **4.0 / 4.0 / 4.0 ms**
- Average input/output tokens: **320.0 / 125.2**; estimated scripted total cost: **$0.0040**

## Comparative Cases

LLM-only correct cases after Validator: `holdout-confirmed-root-cause-011`, `holdout-correction-with-policy-014`, `holdout-cross-decision-001`, `holdout-multilingual-decision-002`, `holdout-multiple-errors-one-fix-009`, `holdout-old-plan-disproved-004`, `holdout-one-recovery-two-verifications-010`, `holdout-project-constraint-005`, `holdout-stable-verification-rule-012`, `holdout-three-event-policy-013`

Rule-only correct cases after Validator: `holdout-causal-trap-025`, `holdout-implicit-causal-language-023`, `holdout-invalid-reference-fallback-034`, `holdout-malformed-fallback-031`, `holdout-provider-fallback-033`, `holdout-timeout-fallback-032`, `holdout-tool-call-fallback-030`, `holdout-unfamiliar-tool-error-016`

## Interpretation Boundary

Scripted candidates demonstrate that the architecture can improve cross-event claim recall while retaining deterministic safety gates. They do not establish that any real provider will produce these candidates reliably.

## Reproduce

```bash
python3 scripts/evaluate_reflection_llm.py --dataset tests/fixtures/reflection_llm_holdout --output artifacts/reflection-accuracy-llm-shadow.json --markdown docs/reflection-accuracy-llm-shadow.md --comparison docs/reflection-llm-shadow-comparison.md
```
