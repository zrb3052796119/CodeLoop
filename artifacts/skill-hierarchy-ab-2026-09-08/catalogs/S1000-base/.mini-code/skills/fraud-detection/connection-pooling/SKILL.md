---
name: connection-pooling
description: Size and tune connection pools for the fraud detection pipeline; covers pool exhaustion, idle reaping, and leak detection.
keywords: [connections, detection, fraud, leaks, pool]
domains: [fraud detection]
---
# Fraud Detection: Connection Pooling

## When to use

Use this skill when work touches connection pooling in the fraud detection pipeline. Skip it for
unrelated subsystems; the steps below assume the fraud detection runtime
conventions.

## Procedure

1. Identify the connection pooling surface in the fraud detection pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the connection pooling changes incrementally, one knob at a time, keeping the
   public interface of the fraud detection pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic connection pooling advice without checking the fraud detection
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard fraud detection smoke checks.
