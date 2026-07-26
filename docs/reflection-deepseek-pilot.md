# Reflection LLM Real-Provider Pilot

- Kind/model/provider: **real_provider_holdout / deepseek-chat / custom**
- Selected / eligible / called: **34 / 24 / 8**
- Parser success: **75.0%**
- Validator P/R/F1: **33.3% / 20.0% / 25.0%**
- Value P/R/F1: **66.7% / 40.0% / 50.0%**
- Low-value false writes / invalid references / epistemic mismatches: **1 / 0 / 0**
- Root-cause candidate/final overclaim: **1 / 0**
- Latency average/median/P95: **2339.5 / 2234.6 / 3739.0 ms**
- Usage sources: **{"provider": 8}**
- Tokens / estimated cost: **{"cache_creation": 0, "cache_read": 3840, "input": 7159, "output": 2049} / $0.001845**
- Fallbacks: **{"all_llm_claims_rejected": 3, "invalid_semantic_key": 2}**
- Sample insufficient: **True**

## Case IDs

`holdout-causal-trap-025`, `holdout-timeout-fallback-032`, `holdout-ambiguous-error-022`, `holdout-unverified-recovery-024`, `holdout-invalid-reference-fallback-034`, `holdout-redacted-secret-error-028`, `holdout-provider-fallback-033`, `holdout-verified-recovery-007`, `holdout-multilingual-decision-002`, `holdout-tool-call-fallback-030`, `holdout-partial-recovery-008`, `holdout-multiple-errors-one-fix-009`, `holdout-security-note-029`, `holdout-malformed-fallback-031`, `holdout-project-constraint-005`, `holdout-search-only-019`, `holdout-weak-library-026`, `holdout-unfamiliar-tool-error-016`, `holdout-prompt-injection-log-027`, `holdout-implicit-causal-language-023`, `holdout-green-test-020`, `holdout-indirect-correction-003`, `holdout-format-only-021`, `holdout-routine-success-017`, `holdout-old-plan-disproved-004`, `holdout-one-recovery-two-verifications-010`, `holdout-unfamiliar-dependency-006`, `holdout-correction-with-policy-014`, `holdout-three-event-policy-013`, `holdout-limited-fix-scope-015`, `holdout-confirmed-root-cause-011`, `holdout-read-only-018`, `holdout-cross-decision-001`, `holdout-stable-verification-rule-012`
