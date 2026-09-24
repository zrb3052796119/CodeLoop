---
name: error-mapping
description: Map errors from the GraphQL API server to application-level exceptions; covers error taxonomies and retryable-versus-fatal classification.
keywords: [classification, errors, graphql, taxonomy]
domains: [graphql api]
---
# Graphql Api: Error Mapping

## When to use

Use this skill when work touches error mapping in the GraphQL API server. Skip it for
unrelated subsystems; the steps below assume the graphql api runtime
conventions.

## Procedure

1. Identify the error mapping surface in the GraphQL API server: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the error mapping changes incrementally, one knob at a time, keeping the
   public interface of the GraphQL API server unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic error mapping advice without checking the graphql api
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard graphql api smoke checks.
