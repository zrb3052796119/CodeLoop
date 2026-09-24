---
name: retry-backoff
description: Configure retry and exponential backoff for the payment provider integration; covers jitter, attempt budgets, and which errors are safe to retry.
keywords: [backoff, gateway, jitter, payment, retry, transient-errors]
domains: [payment gateway]
---
# Payment Gateway: Retry Backoff

## When to use

Use this skill when work touches retry and backoff in the payment provider integration. Skip it for
unrelated subsystems; the steps below assume the payment gateway runtime
conventions.

## Procedure

1. Identify the retry and backoff surface in the payment provider integration: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the retry and backoff changes incrementally, one knob at a time, keeping the
   public interface of the payment provider integration unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic retry and backoff advice without checking the payment gateway
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard payment gateway smoke checks.
