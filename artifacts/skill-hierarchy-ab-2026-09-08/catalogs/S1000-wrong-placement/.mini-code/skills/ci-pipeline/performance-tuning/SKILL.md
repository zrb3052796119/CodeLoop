---
name: performance-tuning
description: Profile and tune the performance of the CI/CD pipeline; covers bottleneck isolation, benchmark harnesses, and regression guards.
keywords: [benchmark, bottleneck, performance, pipeline, profiling]
domains: [ci pipeline]
---
# Ci Pipeline: Performance Tuning

## When to use

Use this skill when work touches performance tuning in the CI/CD pipeline. Skip it for
unrelated subsystems; the steps below assume the ci pipeline runtime
conventions.

## Procedure

1. Identify the performance tuning surface in the CI/CD pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the performance tuning changes incrementally, one knob at a time, keeping the
   public interface of the CI/CD pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic performance tuning advice without checking the ci pipeline
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard ci pipeline smoke checks.
