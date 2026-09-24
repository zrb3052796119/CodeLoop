---
name: timeout-tuning
description: Set timeout budgets for the upstream identity provider; covers connect/read splits, deadline propagation, and slow-call diagnostics.
keywords: [deadline, identity, provider, slow-calls, timeout]
domains: [identity provider]
---
# Identity Provider: Timeout Tuning

## When to use

Use this skill when work touches timeout budgets in the upstream identity provider. Skip it for
unrelated subsystems; the steps below assume the identity provider runtime
conventions.

## Procedure

1. Identify the timeout budgets surface in the upstream identity provider: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the timeout budgets changes incrementally, one knob at a time, keeping the
   public interface of the upstream identity provider unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic timeout budgets advice without checking the identity provider
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard identity provider smoke checks.
