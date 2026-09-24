---
name: security-hardening
description: Harden the incident response tooling against abuse; covers secret handling, input validation, and least-privilege access.
keywords: [hardening, incident, least-privilege, response, security, validation]
domains: [incident response]
---
# Incident Response: Security Hardening

## When to use

Use this skill when work touches security hardening in the incident response tooling. Skip it for
unrelated subsystems; the steps below assume the incident response runtime
conventions.

## Procedure

1. Identify the security hardening surface in the incident response tooling: entry points, configuration
   knobs, and existing call sites.
2. Capture the current baseline behavior (logs, metrics, or a small
   reproduction) before changing anything.
3. Apply the security hardening changes incrementally, one knob at a time, keeping the
   public interface of the incident response tooling unchanged.
4. Exercise the failure paths explicitly; happy-path-only verification is the
   most common way this work ships broken.
5. Document the final settings and their rationale next to the configuration.

## Pitfalls

- Applying generic security hardening advice without checking the incident response
  specifics first.
- Tuning several knobs at once so regressions cannot be attributed.
- Forgetting the rollback path for the change.

## Verification

Re-run the baseline capture from step 2 and confirm the target symptom is
gone; then run the standard incident response smoke checks.
