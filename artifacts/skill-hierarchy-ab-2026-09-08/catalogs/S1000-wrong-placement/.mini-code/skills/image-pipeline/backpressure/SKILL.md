---
name: backpressure
description: Apply backpressure in the image processing pipeline pipelines; covers bounded queues, load shedding, and consumer lag.
keywords: [backpressure, bounded-queue, image, load-shedding, pipeline]
domains: [image pipeline]
---
# Image Pipeline: Backpressure

## When to use

Use this skill when work touches backpressure in the image processing pipeline. Skip it for
unrelated subsystems; the steps below assume the image pipeline runtime
conventions.

## Procedure

1. Identify the backpressure surface in the image processing pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the backpressure changes incrementally, one knob at a time, keeping the
   public interface of the image processing pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic backpressure advice without checking the image pipeline
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard image pipeline smoke checks.
