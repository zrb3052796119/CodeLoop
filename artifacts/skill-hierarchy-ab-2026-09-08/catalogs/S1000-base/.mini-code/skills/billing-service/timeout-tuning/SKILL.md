---
name: timeout-tuning
description: Set timeout budgets for the billing and invoicing service; covers connect/read splits, deadline propagation, and slow-call diagnostics.
keywords: [billing, deadline, service, slow-calls, timeout]
domains: [billing service]
---
# Billing Service: Timeout Tuning

## When to use

Use this skill when work touches timeout budgets in the billing and invoicing service. Skip it for
unrelated subsystems; the steps below assume the billing service runtime
conventions.

## Procedure

1. Identify the timeout budgets surface in the billing and invoicing service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the timeout budgets changes incrementally, one knob at a time, keeping the
   public interface of the billing and invoicing service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic timeout budgets advice without checking the billing service
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard billing service smoke checks.
