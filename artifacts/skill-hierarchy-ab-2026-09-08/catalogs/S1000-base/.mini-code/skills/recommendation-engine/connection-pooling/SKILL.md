---
name: connection-pooling
description: Size and tune connection pools for the recommendation serving engine; covers pool exhaustion, idle reaping, and leak detection.
keywords: [connections, engine, leaks, pool, recommendation]
domains: [recommendation engine]
---
# Recommendation Engine: Connection Pooling

## When to use

Use this skill when work touches connection pooling in the recommendation serving engine. Skip it for
unrelated subsystems; the steps below assume the recommendation engine runtime
conventions.

## Procedure

1. Identify the connection pooling surface in the recommendation serving engine: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the connection pooling changes incrementally, one knob at a time, keeping the
   public interface of the recommendation serving engine unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic connection pooling advice without checking the recommendation engine
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard recommendation engine smoke checks.
