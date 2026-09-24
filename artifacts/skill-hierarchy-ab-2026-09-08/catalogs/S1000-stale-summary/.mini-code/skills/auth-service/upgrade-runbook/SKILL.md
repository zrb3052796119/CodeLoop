---
name: upgrade-runbook
description: Upgrade the dependencies of the authentication and token service safely; covers compatibility checks, staged rollout, and rollback.
keywords: [auth, compatibility, service, staged-rollout, upgrade]
domains: [auth service]
---
# Auth Service: Upgrade Runbook

## When to use

Use this skill when work touches upgrades in the authentication and token service. Skip it for
unrelated subsystems; the steps below assume the auth service runtime
conventions.

## Procedure

1. Identify the upgrades surface in the authentication and token service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the upgrades changes incrementally, one knob at a time, keeping the
   public interface of the authentication and token service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic upgrades advice without checking the auth service
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard auth service smoke checks.
