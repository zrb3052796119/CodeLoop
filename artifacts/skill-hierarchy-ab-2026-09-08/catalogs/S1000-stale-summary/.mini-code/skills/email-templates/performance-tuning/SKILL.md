---
name: performance-tuning
description: Profile and tune the performance of the transactional email template system; covers bottleneck isolation, benchmark harnesses, and regression guards.
keywords: [benchmark, bottleneck, email, performance, profiling, templates]
domains: [email templates]
---
# Email Templates: Performance Tuning

## When to use

Use this skill when work touches performance tuning in the transactional email template system. Skip it for
unrelated subsystems; the steps below assume the email templates runtime
conventions.

## Procedure

1. Identify the performance tuning surface in the transactional email template system: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the performance tuning changes incrementally, one knob at a time, keeping the
   public interface of the transactional email template system unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic performance tuning advice without checking the email templates
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard email templates smoke checks.
