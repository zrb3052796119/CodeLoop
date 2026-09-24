---
name: observability
description: Instrument the relational database access layer with metrics, logs, and traces; covers RED metrics and alert thresholds.
keywords: [alerts, database, logs, metrics, traces]
domains: [database]
---
# Database: Observability

## When to use

Use this skill when work touches observability in the relational database access layer. Skip it for
unrelated subsystems; the steps below assume the database runtime
conventions.

## Procedure

1. Identify the observability surface in the relational database access layer: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the observability changes incrementally, one knob at a time, keeping the
   public interface of the relational database access layer unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic observability advice without checking the database
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard database smoke checks.
