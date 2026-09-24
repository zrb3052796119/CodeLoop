---
name: security-hardening
description: Harden the product analytics pipeline against abuse; covers secret handling, input validation, and least-privilege access.
keywords: [analytics, hardening, least-privilege, pipeline, security, validation]
domains: [analytics pipeline]
---
# Analytics Pipeline: Security Hardening

## When to use

Use this skill when work touches security hardening in the product analytics pipeline. Skip it for
unrelated subsystems; the steps below assume the analytics pipeline runtime
conventions.

## Procedure

1. Identify the security hardening surface in the product analytics pipeline: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the security hardening changes incrementally, one knob at a time, keeping the
   public interface of the product analytics pipeline unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic security hardening advice without checking the analytics pipeline
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard analytics pipeline smoke checks.
