---
name: observability
description: Instrument the log collection pipeline with metrics, logs, and traces; covers RED metrics and alert thresholds.
keywords: [alerts, logging, logs, metrics, pipeline, traces]
domains: [logging pipeline]
---
# Logging Pipeline: Observability

## When to use

Use this skill when work touches observability in the log collection pipeline. Skip it for
unrelated subsystems; the steps below assume the logging pipeline runtime
conventions.

## Procedure

1. Identify the observability surface in the log collection pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the observability changes incrementally, one knob at a time, keeping the
   public interface of the log collection pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic observability advice without checking the logging pipeline
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard logging pipeline smoke checks.
