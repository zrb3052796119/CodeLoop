---
name: pagination
description: Implement pagination over list APIs of the shipping carrier integrations; covers cursor versus offset trade-offs and stable ordering.
keywords: [cursor, integration, offset, ordering, pagination, shipping]
domains: [shipping integration]
---
# Shipping Integration: Pagination

## When to use

Use this skill when work touches pagination in the shipping carrier integrations. Skip it for
unrelated subsystems; the steps below assume the shipping integration runtime
conventions.

## Procedure

1. Identify the pagination surface in the shipping carrier integrations: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the pagination changes incrementally, one knob at a time, keeping the
   public interface of the shipping carrier integrations unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic pagination advice without checking the shipping integration
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard shipping integration smoke checks.
