---
name: duplicate-session-cleanup
description: Reap duplicate sessions created by double logins; cleans up after the fact instead of preventing duplicate calls.
keywords: [cleanup, duplicate, session, store]
domains: [session store]
---
# Session Store: Duplicate Session Cleanup

## When to use

Use this skill when work touches specialized helper in the distributed session store. Skip it for
unrelated subsystems; the steps below assume the session store runtime
conventions.

## Procedure

1. Identify the specialized helper surface in the distributed session store: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the specialized helper changes incrementally, one knob at a time, keeping the
   public interface of the distributed session store unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic specialized helper advice without checking the session store
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard session store smoke checks.
