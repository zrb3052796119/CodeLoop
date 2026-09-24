---
name: config-management
description: Manage configuration of the message queue brokers and consumers across environments; covers validation, defaults, and safe rollout of changes.
keywords: [config, defaults, environments, message, queue, rollout]
domains: [message queue]
---
# Message Queue: Config Management

## When to use

Use this skill when work touches configuration in the message queue brokers and consumers. Skip it for
unrelated subsystems; the steps below assume the message queue runtime
conventions.

## Procedure

1. Identify the configuration surface in the message queue brokers and consumers: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the configuration changes incrementally, one knob at a time, keeping the
   public interface of the message queue brokers and consumers unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic configuration advice without checking the message queue
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard message queue smoke checks.
