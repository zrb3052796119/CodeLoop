---
name: schema-migration
description: Plan and execute schema or contract migrations for the cron/background job scheduler; covers expand-contract, backfills, and rollback.
keywords: [backfill, expand-contract, migration, rollback, scheduler]
domains: [job scheduler]
---
# Job Scheduler: Schema Migration

## When to use

Use this skill when work touches schema migrations in the cron/background job scheduler. Skip it for
unrelated subsystems; the steps below assume the job scheduler runtime
conventions.

## Procedure

1. Identify the schema migrations surface in the cron/background job scheduler: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the schema migrations changes incrementally, one knob at a time, keeping the
   public interface of the cron/background job scheduler unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic schema migrations advice without checking the job scheduler
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard job scheduler smoke checks.
