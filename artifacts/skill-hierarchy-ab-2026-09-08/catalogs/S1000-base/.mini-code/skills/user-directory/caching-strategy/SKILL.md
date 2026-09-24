---
name: caching-strategy
description: Design the caching policy for the user directory service; covers TTL selection, invalidation, and stampede protection.
keywords: [cache, directory, invalidation, stampede, ttl, user]
domains: [user directory]
---
# User Directory: Caching Strategy

## When to use

Use this skill when work touches caching policy in the user directory service. Skip it for
unrelated subsystems; the steps below assume the user directory runtime
conventions.

## Procedure

1. Identify the caching policy surface in the user directory service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the caching policy changes incrementally, one knob at a time, keeping the
   public interface of the user directory service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic caching policy advice without checking the user directory
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard user directory smoke checks.
