# Reflection LLM Shadow Performance

Local deterministic measurements exclude real provider/network latency.

| Path | Median | p95 | Peak |
| --- | ---: | ---: | ---: |
| `eligibility_gate` | 0.0008 ms | 0.0009 ms | 1.1 KiB |
| `allowlisted_envelope` | 0.1233 ms | 0.1381 ms | 11.0 KiB |
| `rule_synthesis_validation_value` | 0.0612 ms | 0.0682 ms | 7.9 KiB |
| `validator_value_only` | 0.0006 ms | 0.0007 ms | 1.0 KiB |
| `scripted_llm_parse_validation_value` | 0.2232 ms | 0.2382 ms | 15.3 KiB |

- Eligibility median below 1 ms: `True`
- Rule synthesis/validation/value median below 1 ms: `True`
- Timeout: `15.0` s; input/output bytes: `24576` / `32768`; output tokens: `1200`; claims: `8`.
- Shadow persistence completes before the optional model comparison. The configured provider timeout bounds the remaining synchronous diagnostic work.
- Scripted holdout latency and token/cost fields validate reporting only; real-provider distributions remain unmeasured.
