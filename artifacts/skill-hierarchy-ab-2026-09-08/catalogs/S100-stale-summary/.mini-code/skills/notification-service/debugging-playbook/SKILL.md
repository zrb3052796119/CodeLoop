---
name: debugging-playbook
description: Debug production issues in the email/SMS/push notification service; covers symptom-to-cause checklists and safe inspection commands.
keywords: [checklist, debugging, notification, playbook, production, service]
domains: [notification service]
---
# Notification Service: Debugging Playbook

## When to use

Use this skill when work touches debugging in the email/SMS/push notification service. Skip it for
unrelated subsystems; the steps below assume the notification service runtime
conventions.

## Procedure

1. Identify the debugging surface in the email/SMS/push notification service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the debugging changes incrementally, one knob at a time, keeping the
   public interface of the email/SMS/push notification service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic debugging advice without checking the notification service
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard notification service smoke checks.
