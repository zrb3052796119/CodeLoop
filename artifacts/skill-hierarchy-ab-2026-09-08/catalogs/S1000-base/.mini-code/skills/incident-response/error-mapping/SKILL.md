---
name: error-mapping
description: Map errors from the incident response tooling to application-level exceptions; covers error taxonomies and retryable-versus-fatal classification.
keywords: [classification, errors, incident, response, taxonomy]
domains: [incident response]
---
# Incident Response: Error Mapping

## When to use

Use this skill when work touches error mapping in the incident response tooling. Skip it for
unrelated subsystems; the steps below assume the incident response runtime
conventions.

## Procedure

1. Identify the error mapping surface in the incident response tooling: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the error mapping changes incrementally, one knob at a time, keeping the
   public interface of the incident response tooling unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic error mapping advice without checking the incident response
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard incident response smoke checks.
