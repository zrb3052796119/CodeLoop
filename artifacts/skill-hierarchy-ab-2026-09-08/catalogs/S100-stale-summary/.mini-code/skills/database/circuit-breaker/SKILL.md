---
name: circuit-breaker
description: Add a circuit breaker around calls from the relational database access layer; covers failure thresholds, half-open probing, and fallback behavior.
keywords: [circuit-breaker, database, fail-fast, fallback]
domains: [database]
---
# Database: Circuit Breaker

## When to use

Use this skill when work touches circuit breaking in the relational database access layer. Skip it for
unrelated subsystems; the steps below assume the database runtime
conventions.

## Procedure

1. Identify the circuit breaking surface in the relational database access layer: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the circuit breaking changes incrementally, one knob at a time, keeping the
   public interface of the relational database access layer unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic circuit breaking advice without checking the database
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard database smoke checks.
