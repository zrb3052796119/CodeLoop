# Notes: Frozen Workflow Change Capability

## Baseline

- Starting commit: `3782553` (`refactor(workflow): extract workspace transaction`).
- Previous full-suite evidence: 5,018 passed, 2 skipped.
- `WorkflowWorkspaceTransaction` is now isolated in one leaf module; this turn
  deepens its interface rather than moving implementation again.

## Dependency classification

- Filesystem, trusted local Git, temporary directories, monotonic clock, and
  advisory locking are local-substitutable dependencies.
- Scheduler artifacts, verification policy, audit, and finalization are caller
  concerns and must not cross into the workspace module.

## Findings

- Production has six legacy phase calls, all in `task.py`: two mutually
  exclusive `change_set()` calls, one diff projection, one verification clone,
  and two mutually exclusive commits.
- Direct behavior tests are concentrated in `test_workflow_v2_transaction.py`;
  scheduler integration and structured-routing tests monkeypatch the current
  class methods for ordering, post-apply failure, cancellation, and unknown
  promotion injection.
- No other production module calls the legacy phase methods.
- Baseline for the transaction, scheduler integration, and module seam is
  41 passing tests.
- The frozen capability should own immutable patch bytes, base/patch/target
  digests, cwd-relative interpretation, and projected changed files. Scope is
  consumed once to construct that projection; retaining a second unused copy
  would not strengthen the capability. The transaction retains only lifecycle
  and promotion-truth flags used by outer finalization.
- Verification currently clones the mutable workspace without first proving it
  still matches the frozen patch. The new capability should fail closed on
  drift before cloning, rather than waiting until promotion.

## Interface comparison and decision

- Minimal design: projections are detached read-only properties; only freeze,
  verification, and promotion are phase actions; legacy transaction phase
  methods are deleted.
- Extensible design: closed scope/view/projection/promotion value types provide
  future variation but overstate today's single production strategy.
- Caller-first design: a branchless freeze collapses six production calls and
  gives integration tests two clean injection seams (`freeze` and `promote`).
- Selected hybrid: `transaction.freeze(...) -> FrozenWorkflowChange`, then
  `change_set_document`, `diff_artifact_document`,
  `verification_workspace(...)`, and
  `promote(approved_patch_digest=...)`.
- `allowed_paths` and cwd-relative interpretation bind once at freeze. The
  approved digest remains required at promotion because it represents central
  scheduler authority rather than capability identity.
- Freeze creates a private exact workspace snapshot. Verification copies that
  snapshot; promotion still rejects any post-freeze executor-workspace drift.
- Rejected this turn: public scope/view/policy enums, adapters, registries,
  compatibility shims, and a third approved-change typestate.
- Red proof: `tests/test_frozen_workflow_change.py` fails during collection on
  the missing `FrozenWorkflowChange` export before implementation.

## Implementation and review evidence

- `WorkflowWorkspaceTransaction.freeze()` is the sole mutable-to-frozen
  transition and is one-shot after success. Failed snapshot allocation or copy
  remains retryable and maps local OS failures to `WorkflowIsolationError`.
- The legacy transaction phase methods were deleted without aliases. A
  repository-wide Python search found no production or test callers left.
- `FrozenWorkflowChange` alone projects detached change/diff documents, creates
  verification workspaces, and promotes an externally approved digest.
- Verification is rooted in a private frozen snapshot rather than the mutable
  executor workspace. Promotion still re-stages the executor workspace and
  rejects any later drift.
- Independent review first found that the ordinary full-tree digest excludes
  `.git`, so a tampered index could make a retained-Git oracle inspect a clean
  index while evidence named a non-empty patch. Re-staging closed that index
  gap but exposed the deeper issue: repo-local `config` and `info/attributes`
  could change oracle semantics without changing patch bytes.
- The final design snapshots clean scheduler-created baseline Git metadata
  before Worker access. Freeze, every verification clone, and promotion ignore
  Worker-owned `.git` and attach a fresh copy of that trusted template. Exact
  patch bytes, ordinary workspace state, and all Git control state except the
  legitimately mutable index/object store are checked at each boundary.
- Promotion also verifies the actual parent target digest after a successful
  `git apply`; wrong bytes or entry types can no longer be marked completed.
  Metadata allocation/copy `OSError`s map to stable domain failures while
  deadline and interrupt exceptions continue to propagate unchanged.
- New regressions cover one-shot freeze, failed-freeze retry, OS-error mapping,
  detached projections, use-after-close, mutable-workspace drift, frozen-index
  tampering, required approval digest, and nested structured cwd scoping.
- Focused seam suite: 125 passed. Affected multi-agent suite: 547 passed.
- First pre-review full run: 5,025 passed, 2 skipped. It also reported that an
  external real-home `.mini-code/.env` file changed during the 335-second run;
  no test failed, and the implementation does not read or write that path.
- Final post-review full run: 5,035 passed, 2 skipped in 368.80 seconds, without
  a real-home pollution warning. Selected Ruff checks, byte compilation, legacy
  API search, and `git diff --check` also passed.
