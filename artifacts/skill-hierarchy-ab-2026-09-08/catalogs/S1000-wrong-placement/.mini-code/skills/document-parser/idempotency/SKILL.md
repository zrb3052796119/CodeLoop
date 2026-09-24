---
name: idempotency
description: Make operations in the document parsing service idempotent; covers idempotency keys, dedup windows, and safe replay.
keywords: [dedup, document, exactly-once, idempotency, parser, replay]
domains: [document parser]
---
# Document Parser: Idempotency

## When to use

Use this skill when work touches idempotency in the document parsing service. Skip it for
unrelated subsystems; the steps below assume the document parser runtime
conventions.

## Procedure

1. Identify the idempotency surface in the document parsing service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the idempotency changes incrementally, one knob at a time, keeping the
   public interface of the document parsing service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic idempotency advice without checking the document parser
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard document parser smoke checks.
