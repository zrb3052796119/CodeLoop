---
name: connection-pooling
description: Size and tune connection pools for the video transcoding farm; covers pool exhaustion, idle reaping, and leak detection.
keywords: [connections, leaks, pool, transcoding, video]
domains: [video transcoding]
---
# Video Transcoding: Connection Pooling

## When to use

Use this skill when work touches connection pooling in the video transcoding farm. Skip it for
unrelated subsystems; the steps below assume the video transcoding runtime
conventions.

## Procedure

1. Identify the connection pooling surface in the video transcoding farm: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the connection pooling changes incrementally, one knob at a time, keeping the
   public interface of the video transcoding farm unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic connection pooling advice without checking the video transcoding
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard video transcoding smoke checks.
