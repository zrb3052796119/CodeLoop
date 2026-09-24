---
name: pagination
description: Implement pagination over list APIs of the secrets management backend; covers cursor versus offset trade-offs and stable ordering.
keywords: [cursor, manager, offset, ordering, pagination, secrets]
domains: [secrets manager]
---
# Secrets Manager: Pagination

## When to use

Use this skill when work touches pagination in the secrets management backend. Skip it for
unrelated subsystems; the steps below assume the secrets manager runtime
conventions.

## Procedure

1. Identify the pagination surface in the secrets management backend: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the pagination changes incrementally, one knob at a time, keeping the
   public interface of the secrets management backend unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic pagination advice without checking the secrets manager
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard secrets manager smoke checks.
