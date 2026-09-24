---
name: backpressure
description: Apply backpressure in the message queue brokers and consumers pipelines; covers bounded queues, load shedding, and consumer lag.
keywords: [backpressure, bounded-queue, index, load-shedding, search]
domains: [search index]
---
# Search Index: Backpressure

## When to use

Use this skill when work touches backpressure in the message queue brokers and consumers. Skip it for
unrelated subsystems; the steps below assume the search index runtime
conventions.

## Procedure

1. Identify the backpressure surface in the message queue brokers and consumers: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the backpressure changes incrementally, one knob at a time, keeping the
   public interface of the message queue brokers and consumers unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic backpressure advice without checking the search index
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard search index smoke checks.
