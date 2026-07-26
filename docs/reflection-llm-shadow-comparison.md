# Reflection LLM Shadow Comparison

## What Improved

The scripted LLM branch raised Validator-stage claim recall from **54.2%** to **62.5%** and precision from **39.4%** to **88.2%**. The gains are concentrated in bilingual and multi-event policies where one reusable claim combines two or three explicit DecisionEvidence records.

Experimental `llm` with deterministic fallback reached claim F1 **93.9%** and value F1 **97.8%**, while low-value false-write remained **0.0%**.

## What Failed

The causal-trap case generated one unsupported confirmed root-cause candidate from an error followed by a passing test. `ReflectionClaimValidator` rejected it for missing decision/recovery evidence, failed statement grounding, missing full-chain references, and missing targeted-test limitations. No root-cause overclaim became persistable.

The shadow LLM branch also loses recall whenever eligibility declines a call or a scripted provider/parser failure occurs. The production `llm` branch recovers those cases through unchanged rule fallback; shadow reporting deliberately does not hide them.

## Configuration

The default remains `reflectionSynthesizerMode: "rule"`. `llm_shadow` evaluates a non-production candidate; `llm` is experimental. `allowRemoteReflectionModel` defaults to `false`, so a remote reflection model is not contacted without an explicit opt-in.

```json
{
  "reflectionSynthesizerMode": "llm_shadow",
  "reflectionModel": "local-model-name",
  "reflectionLLMTimeoutSeconds": 15,
  "reflectionLLMMaxOutputTokens": 1200,
  "reflectionLLMMaxInputBytes": 24576,
  "reflectionLLMMaxOutputBytes": 32768,
  "reflectionLLMMaxClaims": 8,
  "allowRemoteReflectionModel": false
}
```

## Failure Accounting

- Timeout rate: **2.9%**
- Parse failure rate: **11.8%**
- Provider failure rate: **2.9%**
- Tool-call rejection rate: **2.9%**
- Fallback reasons: `all_llm_claims_rejected`=1, `input_safety_rejected`=2, `invalid_evidence_id`=1, `no_durable_signal_candidate`=6, `non_json_wrapper`=1, `provider_error`=1, `provider_timeout`=1, `routine_low_value_task`=3, `routine_verification_only`=1, `tool_call_rejected`=1

## Recommendation

Keep `rule` as the default and continue `llm_shadow` evaluation. The offline holdout proves isolation, validation, and potential semantic benefit, but it is not sufficient evidence for default or broad production `llm` activation because no real model distribution was measured.
