---
name: retry-backoff
description: Configure retry and exponential backoff for the CRM synchronization connector; covers jitter, attempt budgets, and which errors are safe to retry.
keywords: [backoff, connector, jitter, retry, transient-errors]
domains: [crm connector]
---
# Crm Connector: Retry Backoff

## When to use

Use this skill when work touches retry and backoff in the CRM synchronization connector. Skip it for
unrelated subsystems; the steps below assume the crm connector runtime
conventions.

## Procedure

1. Identify the retry and backoff surface in the CRM synchronization connector: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the retry and backoff changes incrementally, one knob at a time, keeping the
   public interface of the CRM synchronization connector unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic retry and backoff advice without checking the crm connector
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard crm connector smoke checks.
