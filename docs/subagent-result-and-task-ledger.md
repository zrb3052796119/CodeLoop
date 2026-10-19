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

Ordinary child runs receive an invocation-owned result channel. The host
reserves both the result key and its enumeration-detail key; siblings cannot
replace them, including by supplying the same author string. The owner may
correct a malformed report during the bounded reminder. Returning or failing
closes its channel and seals the result. Shared collaboration notes remain
editable, but their eviction cannot discard a protected formal result.

The reminder continues the completed transcript and preserves the original
findings. Its additional calls count in the final journal and result header.
Strict workflow roles retain their own result schema and repair protocol.

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

Only `explore` and `plan` may be spawned asynchronously. Workflow stages run
synchronously with their bounded permission and validation sequence; the
legacy `general` entry also retains a synchronous interface.

The registry is owned by one top-level agent turn. Child loops create their
own registry rather than waiting on the parent's sibling jobs. The registry is
and bounded to 16 jobs with four workers and 12,000 characters per terminal
result. IDs from another turn fail closed. On every turn exit, the owner sets
all unfinished cancellation events and performs bounded cleanup. An agent that
does not need overlapping work can continue using the original input without
an `action` field.

Completion and delivery are separate. `poll`, `cancel`, and the finalization
barrier project terminal results without consuming them. The owning Agent
Loop acknowledges a result only after inserting it into its transcript;
abandoned timeout threads cannot acknowledge it. Direct lifecycle embedders
must call `acknowledge_delivery((subagent_id, ...))` after receiving and
retaining terminal results. The normal `task` tool/Agent Loop path handles
this automatically with a private runtime receipt.

Live tool labels include the logical workflow role and short invocation ID,
for example `reviewer:5cdbdd5a▸read_file`. This distinguishes workflow roles
that share the same model route, and simultaneous runs of the same type.

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
