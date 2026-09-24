---
name: multipart-retry-generic
description: Generic retry policy for multipart HTTP form posts; does not cover resumable chunked uploads or part-level recovery.
keywords: [client, generic, http, multipart, retry]
domains: [http client]
---
# Http Client: Multipart Retry Generic

## When to use

Use this skill when work touches specialized helper in the outbound HTTP client layer. Skip it for
unrelated subsystems; the steps below assume the http client runtime
conventions.

## Procedure

1. Identify the specialized helper surface in the outbound HTTP client layer: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the specialized helper changes incrementally, one knob at a time, keeping the
   public interface of the outbound HTTP client layer unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic specialized helper advice without checking the http client
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard http client smoke checks.
