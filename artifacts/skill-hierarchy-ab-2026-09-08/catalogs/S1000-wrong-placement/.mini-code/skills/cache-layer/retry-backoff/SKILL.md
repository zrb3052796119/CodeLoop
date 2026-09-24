---
name: retry-backoff
description: Configure retry and exponential backoff for the object/blob storage service; covers jitter, attempt budgets, and which errors are safe to retry.
keywords: [backoff, cache, jitter, layer, retry, transient-errors]
domains: [cache layer]
---
# Cache Layer: Retry Backoff

## When to use

Use this skill when work touches retry and backoff in the object/blob storage service. Skip it for
unrelated subsystems; the steps below assume the cache layer runtime
conventions.

## Procedure

1. Identify the retry and backoff surface in the object/blob storage service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the retry and backoff changes incrementally, one knob at a time, keeping the
   public interface of the object/blob storage service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic retry and backoff advice without checking the cache layer
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard cache layer smoke checks.
