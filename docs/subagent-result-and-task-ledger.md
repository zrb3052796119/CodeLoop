# Sub-agent Result Contract and Task Ledger

## Structured hand-back

Every successful `task` invocation returns a bounded JSON result after its
human-readable narrative. Direct `explore`, `plan`, and `general` agents write
the report through the turn-scoped mailbox; the parent runtime validates it
and supplies identity and outcome fields. A `workflow` derives the same shape
from its phase reports and authoritative review verdict.

The public v1 result contains:

- `subagentId`, `agentType`, and `outcome`, owned by the parent runtime;
- `contractStatus`: `reported`, `derived`, or explicit `fallback`;
- `summary` (maximum 4,000 characters);
- bounded `files` entries with `path` and `action`;
- bounded `risks`;
- `verification.status` and bounded `verification.checks`.

Malformed, oversized, missing, or extra report fields do not become evidence.
The runtime emits a fallback with empty files/risks and `inconclusive`
verification instead of guessing.

New `subagent.completed` observations use schema v3 and carry the same
`subagentId` plus content-free counters and `resultContractStatus`. The ID
joins three independently bounded views:

1. the immediate structured ToolResult;
2. the parent Run's content-free completion event;
3. the sub-agent sidecar Run summary.

Legacy v1/v2 completion events remain readable. Workflow sidecars label their
limit as `phases`; direct agents label it as `model_turns`.

## Asynchronous read-only lifecycle

The `task` tool keeps its backward-compatible synchronous default and adds
three explicit lifecycle operations:

- `action=spawn` returns a versioned status object and opaque `subagentId`
  immediately;
- `action=poll` returns the current status and, at a terminal state, the
  bounded ToolResult;
- `action=cancel` idempotently requests cooperative cancellation. A running
  worker first reports non-terminal `cancelling`; `poll` reports `cancelled`
  only after the worker acknowledges the request. A late request cannot
  relabel an already emitted successful completion.

Only `explore` and `plan` may be spawned asynchronously. `general` and
`workflow` retain synchronous execution because Python cannot forcibly stop a
thread blocked in a provider call, and letting a write-capable worker outlive
its parent could mutate the workspace after cancellation.

The registry is owned by one top-level agent turn, shared by its nested loops,
and bounded to 16 jobs with four workers and 12,000 characters per terminal
result. IDs from another turn fail closed. On every turn exit, the owner sets
all unfinished cancellation events and performs bounded cleanup. An agent that
does not need overlapping work can continue using the original input without
an `action` field.

## Compaction-immune task ledger

`TaskLedger` is parent-owned runtime state, projected as one marked system
message that compaction engines preserve without asking an LLM to summarize
it. A new turn replaces the previous projection rather than accumulating
ledgers.

The ledger stores only:

- the current user goal, verbatim and bounded;
- sentences containing explicit constraint language;
- closed verification observations emitted by actual verifier tools;
- failed attempt identities reduced to `tool + error code`.

Arbitrary model prose, paths, command output, and untyped success claims cannot
enter verified facts. Full compact, repeated compact, reactive recovery, and
the legacy compactor all preserve system projections, so the ledger remains
outside the lossy summary chain while staying bounded.
