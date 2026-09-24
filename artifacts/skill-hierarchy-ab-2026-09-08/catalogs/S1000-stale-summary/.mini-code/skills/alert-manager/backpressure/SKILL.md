---
name: backpressure
description: Apply backpressure in the alert routing manager pipelines; covers bounded queues, load shedding, and consumer lag.
keywords: [alert, backpressure, bounded-queue, load-shedding, manager]
domains: [alert manager]
---
# Alert Manager: Backpressure

## When to use

Use this skill when work touches backpressure in the alert routing manager. Skip it for
unrelated subsystems; the steps below assume the alert manager runtime
conventions.

## Procedure

1. Identify the backpressure surface in the alert routing manager: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the backpressure changes incrementally, one knob at a time, keeping the
   public interface of the alert routing manager unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic backpressure advice without checking the alert manager
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard alert manager smoke checks.
