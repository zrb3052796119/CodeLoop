---
name: debugging-playbook
description: Debug production issues in the document parsing service; covers symptom-to-cause checklists and safe inspection commands.
keywords: [checklist, debugging, document, parser, playbook, production]
domains: [document parser]
---
# Document Parser: Debugging Playbook

## When to use

Use this skill when work touches debugging in the document parsing service. Skip it for
unrelated subsystems; the steps below assume the document parser runtime
conventions.

## Procedure

1. Identify the debugging surface in the document parsing service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the debugging changes incrementally, one knob at a time, keeping the
   public interface of the document parsing service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic debugging advice without checking the document parser
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard document parser smoke checks.
