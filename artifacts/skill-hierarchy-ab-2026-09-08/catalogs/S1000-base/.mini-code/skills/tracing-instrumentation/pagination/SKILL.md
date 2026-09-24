---
name: pagination
description: Implement pagination over list APIs of the distributed tracing instrumentation; covers cursor versus offset trade-offs and stable ordering.
keywords: [cursor, instrumentation, offset, ordering, pagination, tracing]
domains: [tracing instrumentation]
---
# Tracing Instrumentation: Pagination

## When to use

Use this skill when work touches pagination in the distributed tracing instrumentation. Skip it for
unrelated subsystems; the steps below assume the tracing instrumentation runtime
conventions.

## Procedure

1. Identify the pagination surface in the distributed tracing instrumentation: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the pagination changes incrementally, one knob at a time, keeping the
   public interface of the distributed tracing instrumentation unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic pagination advice without checking the tracing instrumentation
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard tracing instrumentation smoke checks.
