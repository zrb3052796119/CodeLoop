---
name: retry-backoff
description: Configure retry and exponential backoff for the fraud detection pipeline; covers jitter, attempt budgets, and which errors are safe to retry.
keywords: [backoff, detection, fraud, jitter, retry, transient-errors]
domains: [fraud detection]
---
# Fraud Detection: Retry Backoff

## When to use

Use this skill when work touches retry and backoff in the fraud detection pipeline. Skip it for
unrelated subsystems; the steps below assume the fraud detection runtime
conventions.

## Procedure

1. Identify the retry and backoff surface in the fraud detection pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the retry and backoff changes incrementally, one knob at a time, keeping the
   public interface of the fraud detection pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic retry and backoff advice without checking the fraud detection
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard fraud detection smoke checks.
