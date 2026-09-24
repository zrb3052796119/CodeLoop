---
name: upgrade-runbook
description: Upgrade the dependencies of the subscription billing engine safely; covers compatibility checks, staged rollout, and rollback.
keywords: [billing, compatibility, staged-rollout, subscription, upgrade]
domains: [subscription billing]
---
# Subscription Billing: Upgrade Runbook

## When to use

Use this skill when work touches upgrades in the subscription billing engine. Skip it for
unrelated subsystems; the steps below assume the subscription billing runtime
conventions.

## Procedure

1. Identify the upgrades surface in the subscription billing engine: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the upgrades changes incrementally, one knob at a time, keeping the
   public interface of the subscription billing engine unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic upgrades advice without checking the subscription billing
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard subscription billing smoke checks.
