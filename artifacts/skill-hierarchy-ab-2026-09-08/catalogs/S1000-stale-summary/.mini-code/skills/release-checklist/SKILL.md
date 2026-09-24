---
name: release-checklist
description: Pre-release checklist: changelog, version bump, migration dry-run, and rollback plan.
keywords: [checklist, general, release]
---
# Release Checklist

## When to use

Use this skill when work touches release checklist in general project work. Skip it for
unrelated subsystems; the steps below assume the general runtime
conventions.

## Procedure

1. Identify the release checklist surface in general project work: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the release checklist changes incrementally, one knob at a time, keeping the
   public interface of general project work unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic release checklist advice without checking the general
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard general smoke checks.
