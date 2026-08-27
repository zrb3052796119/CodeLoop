# Frozen Workflow Change Capability Report

Status: complete

## Selected interface

`WorkflowWorkspaceTransaction.freeze(...) -> FrozenWorkflowChange` is the only
mutable-to-frozen transition. The capability owns detached change-set and diff
documents, verification workspace creation, and
`promote(approved_patch_digest=...)`. The transaction no longer exposes
`change_set`, `frozen_diff_artifact`, `verification_workspace`, or `commit`.

The scheduler still owns dispatch order, human approval, the approved digest,
audit/trace projection, promotion timing, and final user-visible truth.

## State and trust ownership

- Worker-visible transaction: mutable ordinary workspace only.
- Frozen capability: exact patch bytes, patch/base/target digests, scoped file
  projection, cwd-relative interpretation, and private frozen worktree.
- Scheduler-owned Git template: created before Worker access and copied fresh
  for freeze, each verification check, and promotion.
- Parent truth: promotion-completed/unknown flags remain on the transaction for
  outer finalization and interrupted-apply reconciliation.

Every trusted metadata clone is byte/state checked before use. After staging,
the patch must match the frozen binary patch and Git control state must match
the clean template (excluding only index/index.lock/object data). A successful
parent apply is not reported completed until the actual target digest matches.

## Compatibility decision

No compatibility aliases were retained. Repository-wide Python search found
no old phase calls. Change-set and frozen-diff JSON fields, digest formulas,
structured cwd-relative paths, scheduler error projection, and promotion truth
flags remain compatible.

## Review fixes

Three independent review passes found and closed:

1. A frozen `.git/index` could make an oracle inspect different staged state.
2. `.git/config` or `.git/info/attributes` could preserve the same patch bytes
   while changing `git diff --cached --check` semantics.
3. Worker Git settings such as `core.symlinks=false` or `core.autocrlf=true`
   could make promotion report success with wrong parent entry types or bytes.
4. Promotion metadata allocation errors escaped the workflow domain boundary.

Regression tests cover these attacks, wrong post-apply state, approval digest
mismatch, failed-freeze retry, use-after-close, and nested structured scope.
Final independent review reported no remaining P0-P2 findings.

## Verification

- Focused capability/caller suite: 125 passed.
- Affected multi-agent suite: 547 passed.
- Full suite: 5,035 passed, 2 skipped in 368.80 seconds.
- Ruff `E9,F,I`, Python byte compilation, legacy API search, and
  `git diff --check`: passed.

## Residual trade-off

Clean Git metadata is copied and hashed at each trust boundary, adding local
I/O in exchange for deterministic oracle and promotion semantics. Preventing a
fully privileged same-user process from racing after the final integrity check
would require OS-level process or mount isolation and remains outside this
module's local-filesystem contract.

Conventional commit: `refactor(workflow): bind frozen change capability`.
