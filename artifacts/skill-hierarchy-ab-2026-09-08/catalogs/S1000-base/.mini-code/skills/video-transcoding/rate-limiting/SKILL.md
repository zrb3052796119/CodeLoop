---
name: rate-limiting
description: Apply rate limiting and throttling to the video transcoding farm; covers token buckets, quota tiers, and backoff signaling.
keywords: [quota, rate-limit, throttle, token-bucket, transcoding, video]
domains: [video transcoding]
---
# Video Transcoding: Rate Limiting

## When to use

Use this skill when work touches rate limiting in the video transcoding farm. Skip it for
unrelated subsystems; the steps below assume the video transcoding runtime
conventions.

## Procedure

1. Identify the rate limiting surface in the video transcoding farm: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the rate limiting changes incrementally, one knob at a time, keeping the
   public interface of the video transcoding farm unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic rate limiting advice without checking the video transcoding
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard video transcoding smoke checks.
