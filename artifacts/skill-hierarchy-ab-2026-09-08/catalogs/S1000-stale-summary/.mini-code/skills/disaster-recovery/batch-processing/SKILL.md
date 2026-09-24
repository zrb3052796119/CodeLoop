---
name: batch-processing
description: Batch operations in the disaster recovery tooling for throughput; covers batch sizing, partial-failure handling, and ordering guarantees.
keywords: [batch, disaster, partial-failure, recovery, throughput]
domains: [disaster recovery]
---
# Disaster Recovery: Batch Processing

## When to use

Use this skill when work touches batching in the disaster recovery tooling. Skip it for
unrelated subsystems; the steps below assume the disaster recovery runtime
conventions.

## Procedure

1. Identify the batching surface in the disaster recovery tooling: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the batching changes incrementally, one knob at a time, keeping the
   public interface of the disaster recovery tooling unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic batching advice without checking the disaster recovery
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard disaster recovery smoke checks.
