---
name: debugging-playbook
description: Debug production issues in the CRM synchronization connector; covers symptom-to-cause checklists and safe inspection commands.
keywords: [checklist, connector, debugging, playbook, production]
domains: [crm connector]
---
# Crm Connector: Debugging Playbook

## When to use

Use this skill when work touches debugging in the CRM synchronization connector. Skip it for
unrelated subsystems; the steps below assume the crm connector runtime
conventions.

## Procedure

1. Identify the debugging surface in the CRM synchronization connector: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the debugging changes incrementally, one knob at a time, keeping the
   public interface of the CRM synchronization connector unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic debugging advice without checking the crm connector
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard crm connector smoke checks.
