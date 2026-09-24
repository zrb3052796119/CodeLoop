# Workflow dependency validation repair

Changed production behavior: promotion revalidates the complete snapshotted
parent file tree, including dependencies outside the declared write paths,
while holding the process and cross-process promotion locks. Empty patches now
use the same locked dependency gate before reporting promotion completed.
Declared write paths still bound freeze and reconcile the bytes written by an
apply; they no longer serve as evidence of read independence.

Concurrent disjoint-write execution is still admitted, but after one candidate
changes the shared parent, another stale candidate must rerun from a fresh
snapshot. This is intentionally conservative because the implementation has no
complete observed read set. No new dependency inference from `readRoots` is
assumed, and no claim is made about arbitrary dependencies outside the parent
snapshot.

## Red/green evidence

- Before the fix, both the same-process and actual separate-process write-skew
  cases failed with `DID NOT RAISE WorkflowIsolationError`.
- After the complete-baseline fix, those two cases passed.
- The empty-patch case was added next and failed for the same missing rejection;
  adding the locked baseline check made all three new cases pass.
- The exact final new test and updated scope-semantic tests were run against
  the real saved buggy snapshot `/private/tmp/minicode-multi-agent-buggy-ahhcgrxd`
  using the `test-counterexample` skill verifier. Fixed: **17 passed**. Baseline:
  **5 failed, 12 passed**. All five failures are the expected missing stale
  dependency rejections, not import or setup failures. The verifier result is
  **PROVEN** in `write-skew-counterexample.json`, including test-byte hashes.

## Focused suite

```text
python3 -m pytest -q \
  tests/test_workflow_dependency_regression.py \
  tests/test_workflow_write_scope_registry.py \
  tests/test_workflow_workspace_transaction.py \
  tests/test_workflow_v2_transaction.py \
  tests/test_frozen_workflow_change.py \
  tests/test_workflow_workspace_module.py \
  tests/test_workflow_v2_scheduler_integration.py \
  tests/test_workflow_v2_structured_routing.py

157 passed in 19.97s
```

The installed `ruff` executable checked `workflow_workspace.py`,
`workflow_scope_registry.py`, and both changed test files: **All checks passed**.
