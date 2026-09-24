---
name: circuit-breaker
description: Add a circuit breaker around calls from the order processing pipeline; covers failure thresholds, half-open probing, and fallback behavior.
keywords: [circuit-breaker, fail-fast, fallback, order, processing]
domains: [order processing]
---
# Order Processing: Circuit Breaker

## When to use

Use this skill when work touches circuit breaking in the order processing pipeline. Skip it for
unrelated subsystems; the steps below assume the order processing runtime
conventions.

## Procedure

1. Identify the circuit breaking surface in the order processing pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the circuit breaking changes incrementally, one knob at a time, keeping the
   public interface of the order processing pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic circuit breaking advice without checking the order processing
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard order processing smoke checks.
