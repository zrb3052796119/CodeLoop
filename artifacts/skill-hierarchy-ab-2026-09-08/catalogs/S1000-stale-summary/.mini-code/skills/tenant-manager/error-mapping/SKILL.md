---
name: error-mapping
description: Map errors from the multi-tenant management service to application-level exceptions; covers error taxonomies and retryable-versus-fatal classification.
keywords: [classification, errors, manager, taxonomy, tenant]
domains: [tenant manager]
---
# Tenant Manager: Error Mapping

## When to use

Use this skill when work touches error mapping in the multi-tenant management service. Skip it for
unrelated subsystems; the steps below assume the tenant manager runtime
conventions.

## Procedure

1. Identify the error mapping surface in the multi-tenant management service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the error mapping changes incrementally, one knob at a time, keeping the
   public interface of the multi-tenant management service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic error mapping advice without checking the tenant manager
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard tenant manager smoke checks.
