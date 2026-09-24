---
name: caching-strategy
description: Design the caching policy for the inventory tracking service; covers TTL selection, invalidation, and stampede protection.
keywords: [cache, invalidation, inventory, service, stampede, ttl]
domains: [inventory service]
---
# Inventory Service: Caching Strategy

## When to use

Use this skill when work touches caching policy in the inventory tracking service. Skip it for
unrelated subsystems; the steps below assume the inventory service runtime
conventions.

## Procedure

1. Identify the caching policy surface in the inventory tracking service: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the caching policy changes incrementally, one knob at a time, keeping the
   public interface of the inventory tracking service unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic caching policy advice without checking the inventory service
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard inventory service smoke checks.
