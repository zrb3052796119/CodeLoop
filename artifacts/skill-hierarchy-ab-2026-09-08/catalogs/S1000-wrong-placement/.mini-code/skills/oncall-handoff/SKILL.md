---
name: oncall-handoff
description: Run a structured oncall handoff: open incidents, silenced alerts, and pending follow-ups.
keywords: [general, handoff, oncall]
---
# Oncall Handoff

## When to use

Use this skill when work touches oncall handoff in general project work. Skip it for
unrelated subsystems; the steps below assume the general runtime
conventions.

## Procedure

1. Identify the oncall handoff surface in general project work: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the oncall handoff changes incrementally, one knob at a time, keeping the
   public interface of general project work unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic oncall handoff advice without checking the general
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard general smoke checks.
