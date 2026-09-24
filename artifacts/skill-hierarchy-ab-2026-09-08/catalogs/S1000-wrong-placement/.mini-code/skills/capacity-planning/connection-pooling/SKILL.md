---
name: connection-pooling
description: Size and tune connection pools for the capacity planning toolkit; covers pool exhaustion, idle reaping, and leak detection.
keywords: [capacity, connections, leaks, planning, pool]
domains: [capacity planning]
---
# Capacity Planning: Connection Pooling

## When to use

Use this skill when work touches connection pooling in the capacity planning toolkit. Skip it for
unrelated subsystems; the steps below assume the capacity planning runtime
conventions.

## Procedure

1. Identify the connection pooling surface in the capacity planning toolkit: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the connection pooling changes incrementally, one knob at a time, keeping the
   public interface of the capacity planning toolkit unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic connection pooling advice without checking the capacity planning
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard capacity planning smoke checks.
