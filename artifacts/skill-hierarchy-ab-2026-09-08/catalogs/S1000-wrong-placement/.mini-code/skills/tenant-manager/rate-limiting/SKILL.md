---
name: rate-limiting
description: Apply rate limiting and throttling to the multi-tenant management service; covers token buckets, quota tiers, and backoff signaling.
keywords: [manager, quota, rate-limit, tenant, throttle, token-bucket]
domains: [tenant manager]
---
# Tenant Manager: Rate Limiting

## When to use

Use this skill when work touches rate limiting in the multi-tenant management service. Skip it for
unrelated subsystems; the steps below assume the tenant manager runtime
conventions.

## Procedure

1. Identify the rate limiting surface in the multi-tenant management service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the rate limiting changes incrementally, one knob at a time, keeping the
   public interface of the multi-tenant management service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic rate limiting advice without checking the tenant manager
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard tenant manager smoke checks.
