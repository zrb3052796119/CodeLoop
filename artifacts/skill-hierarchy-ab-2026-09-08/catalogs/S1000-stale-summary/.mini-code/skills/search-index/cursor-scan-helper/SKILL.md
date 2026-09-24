---
name: cursor-scan-helper
description: Scroll/scan cursor pagination for exporting large search result sets; not applicable to GraphQL connection specs.
keywords: [cursor, helper, index, scan, search]
domains: [search index]
---
# Search Index: Cursor Scan Helper

## When to use

Use this skill when work touches specialized helper in the full-text search cluster. Skip it for
unrelated subsystems; the steps below assume the search index runtime
conventions.

## Procedure

1. Identify the specialized helper surface in the full-text search cluster: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the specialized helper changes incrementally, one knob at a time, keeping the
   public interface of the full-text search cluster unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic specialized helper advice without checking the search index
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard search index smoke checks.
