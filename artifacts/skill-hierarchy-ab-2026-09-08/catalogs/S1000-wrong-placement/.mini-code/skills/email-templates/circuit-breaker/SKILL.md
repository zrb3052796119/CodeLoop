---
name: circuit-breaker
description: Add a circuit breaker around calls from the transactional email template system; covers failure thresholds, half-open probing, and fallback behavior.
keywords: [circuit-breaker, email, fail-fast, fallback, templates]
domains: [email templates]
---
# Email Templates: Circuit Breaker

## When to use

Use this skill when work touches circuit breaking in the transactional email template system. Skip it for
unrelated subsystems; the steps below assume the email templates runtime
conventions.

## Procedure

1. Identify the circuit breaking surface in the transactional email template system: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the circuit breaking changes incrementally, one knob at a time, keeping the
   public interface of the transactional email template system unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic circuit breaking advice without checking the email templates
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard email templates smoke checks.
