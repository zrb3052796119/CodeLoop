---
name: migration-job-runner
description: Run schema migration jobs from CI with approvals; schedules migrations but does not define expand-contract or rollback procedure.
keywords: [job, migration, pipeline, runner]
domains: [ci pipeline]
---
# Ci Pipeline: Migration Job Runner

## When to use

Use this skill when work touches specialized helper in the CI/CD pipeline. Skip it for
unrelated subsystems; the steps below assume the ci pipeline runtime
conventions.

## Procedure

1. Identify the specialized helper surface in the CI/CD pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the specialized helper changes incrementally, one knob at a time, keeping the
   public interface of the CI/CD pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic specialized helper advice without checking the ci pipeline
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard ci pipeline smoke checks.
