---
name: circuit-breaker
description: Add a circuit breaker around calls from the CI/CD pipeline; covers failure thresholds, half-open probing, and fallback behavior.
keywords: [circuit-breaker, fail-fast, fallback, pipeline]
domains: [ci pipeline]
---
# Ci Pipeline: Circuit Breaker

## When to use

Use this skill when work touches circuit breaking in the CI/CD pipeline. Skip it for
unrelated subsystems; the steps below assume the ci pipeline runtime
conventions.

## Procedure

1. Identify the circuit breaking surface in the CI/CD pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the circuit breaking changes incrementally, one knob at a time, keeping the
   public interface of the CI/CD pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic circuit breaking advice without checking the ci pipeline
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard ci pipeline smoke checks.
