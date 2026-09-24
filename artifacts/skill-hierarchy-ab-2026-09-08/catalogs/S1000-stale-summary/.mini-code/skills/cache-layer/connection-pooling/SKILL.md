---
name: connection-pooling
description: Size and tune connection pools for the application cache layer; covers pool exhaustion, idle reaping, and leak detection.
keywords: [cache, connections, layer, leaks, pool]
domains: [cache layer]
---
# Cache Layer: Connection Pooling

## When to use

Use this skill when work touches connection pooling in the application cache layer. Skip it for
unrelated subsystems; the steps below assume the cache layer runtime
conventions.

## Procedure

1. Identify the connection pooling surface in the application cache layer: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the connection pooling changes incrementally, one knob at a time, keeping the
   public interface of the application cache layer unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic connection pooling advice without checking the cache layer
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard cache layer smoke checks.
