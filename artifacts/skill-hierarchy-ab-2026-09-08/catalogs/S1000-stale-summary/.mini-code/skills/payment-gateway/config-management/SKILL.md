---
name: config-management
description: Manage configuration of the payment provider integration across environments; covers validation, defaults, and safe rollout of changes.
keywords: [config, defaults, environments, gateway, payment, rollout]
domains: [payment gateway]
---
# Payment Gateway: Config Management

## When to use

Use this skill when work touches configuration in the payment provider integration. Skip it for
unrelated subsystems; the steps below assume the payment gateway runtime
conventions.

## Procedure

1. Identify the configuration surface in the payment provider integration: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the configuration changes incrementally, one knob at a time, keeping the
   public interface of the payment provider integration unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic configuration advice without checking the payment gateway
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard payment gateway smoke checks.
