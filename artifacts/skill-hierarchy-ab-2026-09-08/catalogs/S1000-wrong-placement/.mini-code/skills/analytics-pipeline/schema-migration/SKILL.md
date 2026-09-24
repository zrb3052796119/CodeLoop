---
name: schema-migration
description: Plan and execute schema or contract migrations for the product analytics pipeline; covers expand-contract, backfills, and rollback.
keywords: [analytics, backfill, expand-contract, migration, pipeline, rollback]
domains: [analytics pipeline]
---
# Analytics Pipeline: Schema Migration

## When to use

Use this skill when work touches schema migrations in the product analytics pipeline. Skip it for
unrelated subsystems; the steps below assume the analytics pipeline runtime
conventions.

## Procedure

1. Identify the schema migrations surface in the product analytics pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the schema migrations changes incrementally, one knob at a time, keeping the
   public interface of the product analytics pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic schema migrations advice without checking the analytics pipeline
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard analytics pipeline smoke checks.
