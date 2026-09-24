# Concurrent workflow write-skew probe

Run on 2026-09-08 from the repository root:

```text
python3 artifacts/multi-agent-review-2026-09-08/probe_concurrent_write_skew.py
Isolated acceptance checks: A=True, B=True
Promotions completed: A=True, B=True
Combined parent acceptance check: False
```

Exit code: `0`. The script asserts that both exact frozen verification
workspaces pass the same acceptance check, both promotions succeed, and the
combined parent fails that acceptance check. It uses the real
`WorkflowWorkspaceTransaction` and `FrozenWorkflowChange` implementations, with
all mutable inputs confined to a temporary directory. It does not call a model
or change production code.

## Finding: concurrent workflows validate only their write scope

Priority: P1 when concurrent workflow callers share a parent workspace.

The scheduler admits workflows using only declared write paths
(`minicode/workflow_runner.py:1562`). It passes those same paths as the promotion
baseline scope (`minicode/workflow_runner.py:1658`). Promotion rechecks only
that scope (`minicode/workflow_workspace.py:812`), although verification can read
files outside it.

The probe's shared invariant is `a + b <= 1`. Both workflows snapshot `a=0, b=0`.
A writes `a=1`; B writes `b=1`. Each resulting snapshot satisfies the invariant.
Their write paths do not overlap, so A's promotion does not invalidate B's
write-scope baseline. B then promotes successfully, producing `a=1, b=1`.

This deterministic interleaving represents concurrent workflow sessions or
callers that snapshot before either promotes. A single parent loop making
strictly sequential workflow calls does not trigger it. The probe demonstrates
the workspace transaction boundary rather than an end-to-end model workflow.

Recommended change: track declared or observed read dependencies and their
baseline fingerprints, rejecting promotion if another workflow changes a
dependency; alternatively, rerun acceptance checks against the final merged
parent candidate under a promotion lock. Keep disjoint-write concurrency only
where read dependencies or a final integration check establish independence.

## Separate design observation: there is no implementation repair cycle

`workflow_runner.py:3746` returns non-retryable verification results, and
`central_scheduler.py:852` can retry only the current stage. Reviewer rejection
terminates execution at `central_scheduler.py:932`. Thus failed verification or
`changes_required` never sends the concrete failure back to Executor within the
same workflow. Existing orchestration tests intentionally assert these terminal
paths, so this is a product design limitation rather than a violation of the
current contract.

Recommended improvement: a bounded repair cycle (for example at most two patch
revisions) with failure evidence handed to Executor, a newly frozen patch
digest, and all dependent verification/review gates repeated. Keep
infrastructure retries, result-format repair, implementation repair, and
scope/risk reclassification as distinct scheduler decisions.
