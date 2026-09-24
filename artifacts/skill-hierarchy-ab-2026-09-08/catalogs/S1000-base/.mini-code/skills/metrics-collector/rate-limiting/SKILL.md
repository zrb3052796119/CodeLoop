---
name: rate-limiting
description: Apply rate limiting and throttling to the metrics collection stack; covers token buckets, quota tiers, and backoff signaling.
keywords: [collector, metrics, quota, rate-limit, throttle, token-bucket]
domains: [metrics collector]
---
# Metrics Collector: Rate Limiting

## When to use

Use this skill when work touches rate limiting in the metrics collection stack. Skip it for
unrelated subsystems; the steps below assume the metrics collector runtime
conventions.

## Procedure

1. Identify the rate limiting surface in the metrics collection stack: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the rate limiting changes incrementally, one knob at a time, keeping the
   public interface of the metrics collection stack unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic rate limiting advice without checking the metrics collector
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard metrics collector smoke checks.
