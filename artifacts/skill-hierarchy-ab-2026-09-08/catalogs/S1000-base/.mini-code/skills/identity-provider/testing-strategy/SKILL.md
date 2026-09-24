---
name: testing-strategy
description: Build the test strategy for the upstream identity provider; covers contract tests, fakes versus live instances, and flake control.
keywords: [contract-tests, fakes, flakes, identity, provider, testing]
domains: [identity provider]
---
# Identity Provider: Testing Strategy

## When to use

Use this skill when work touches testing strategy in the upstream identity provider. Skip it for
unrelated subsystems; the steps below assume the identity provider runtime
conventions.

## Procedure

1. Identify the testing strategy surface in the upstream identity provider: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the testing strategy changes incrementally, one knob at a time, keeping the
   public interface of the upstream identity provider unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic testing strategy advice without checking the identity provider
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard identity provider smoke checks.
