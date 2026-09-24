---
name: dead-letter-handling
description: Handle poison or dead-lettered items in the product analytics pipeline; covers quarantine, replay, and alerting.
keywords: [analytics, dead-letter, pipeline, poison, quarantine, replay]
domains: [analytics pipeline]
---
# Analytics Pipeline: Dead Letter Handling

## When to use

Use this skill when work touches dead-letter handling in the product analytics pipeline. Skip it for
unrelated subsystems; the steps below assume the analytics pipeline runtime
conventions.

## Procedure

1. Identify the dead-letter handling surface in the product analytics pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the dead-letter handling changes incrementally, one knob at a time, keeping the
   public interface of the product analytics pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic dead-letter handling advice without checking the analytics pipeline
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard analytics pipeline smoke checks.
