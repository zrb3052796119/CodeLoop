---
name: error-mapping
description: Map errors from the centralized configuration service to application-level exceptions; covers error taxonomies and retryable-versus-fatal classification.
keywords: [classification, config, errors, service, taxonomy]
domains: [config service]
---
# Config Service: Error Mapping

## When to use

Use this skill when work touches error mapping in the centralized configuration service. Skip it for
unrelated subsystems; the steps below assume the config service runtime
conventions.

## Procedure

1. Identify the error mapping surface in the centralized configuration service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the error mapping changes incrementally, one knob at a time, keeping the
   public interface of the centralized configuration service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic error mapping advice without checking the config service
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard config service smoke checks.
