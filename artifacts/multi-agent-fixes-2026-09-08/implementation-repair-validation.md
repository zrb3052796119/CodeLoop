# Bounded implementation repair validation

The host now classifies eligible frozen verification failures and review
`changes_required` results as implementation feedback. The scheduler permits
one repair by default, with a host policy range of zero to two across the whole
workflow. A repair receives a new Executor assignment, attempt and run identity,
followed by new verification and review assignments. Model, token, cost and
deadline budgets remain shared with the original workflow.

`ImplementationRepairRequest.v1` freezes the prior patch, failure artifact,
execution/order/diff/verification references and command evidence. The new
Executor receives the exact declared artifact inputs and original order scope
and criteria. Reopening the isolated workspace revokes the prior frozen
capability, preserves the original parent baseline, and requires a newly frozen
cumulative patch. An unchanged patch cannot reuse earlier gate results.

Human-gated routes, permission failures, unavailable/inconclusive verification,
unknown review criteria, newly requested checks and critical review blockers
do not automatically repair. Each returned per-check verification envelope is
validated against its actual current assignment and patch digest before the
parallel results are aggregated.

The complete durable audit retains every failed revision and its immutable
artifacts. If the parent-facing output would exceed 8,000 characters, its
explicitly compacted trace contains the original preliminary stages and only
the final effective implementation/verification/review chain. It never combines
an old approval with the repaired patch. Audit replay and commit-ready identity
checks still use the complete history.

## Verification

```text
python3 -m pytest -q \
  tests/test_workflow_implementation_repair.py \
  tests/test_workflow_repair_safety.py \
  tests/test_scheduler_repair_audit.py \
  tests/test_scheduler_repair_trace.py

59 passed in 8.97s
```

The cases include actual pytest subprocesses against frozen workspaces;
Verifier failure and Reviewer rejection followed by real file changes; a
shared repair allowance; model/token/cost exhaustion and deadline boundaries;
unchanged patches; revoked frozen capabilities; narrowed order enforcement;
stale passing verification replay; full audit replay and evidence tampering;
and compact output retaining only current approval identities.

Ruff passed for `central_scheduler.py`, `workflow_runner.py`,
`workflow_repair.py`, `workflow_workspace.py`, `workflow_finalization.py`,
`test_workflow_implementation_repair.py` and the adjusted existing structured
routing tests.

## Real old-snapshot proof

The exact final integration test bytes were exercised using the same Python
interpreter and command against both current production and the saved real
buggy snapshot `/private/tmp/minicode-multi-agent-buggy-ahhcgrxd`:

```text
python3 -m pytest tests/test_workflow_implementation_repair.py -q -k rewrites_bytes

Fixed:    2 passed, 6 deselected
Baseline: 2 failed, 6 deselected
Status: PROVEN
```

The baseline failures are actual public workflow terminal rejections after the
first failed verification or review; they are not missing-new-API failures.
The file hash and both runs are in `implementation-repair-counterexample.json`.
`implementation-repair-red.txt` records the initial failure behavior and
`implementation-repair-green.txt` records the final focused suite.

The root task owns the broader integration and full-repository test results.
