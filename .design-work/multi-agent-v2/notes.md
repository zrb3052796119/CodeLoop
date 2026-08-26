# Notes: Multi-Agent Collaboration V2

## User Design Intent

- Clear role ownership matters more than role count.
- One agent should solve a task whenever additional roles add no independent
  evidence, safety separation, or verification value.
- Collaboration must be centrally scheduled rather than negotiated by models.
- Ordering, triggers, aggregation, conflict handling, fallback, timeout, and
  traceability must be engineering contracts.
- Conflicts may use bounded voting for simple homogeneous judgments, expert
  adjudication for complex disputes, and human approval for high-risk cases.

## Baseline Facts Verified

- Production dispatch is implemented by `minicode/tools/task.py`; primitive
  task roles are `explore`, `plan`, and `general`.
- `workflow` is a synchronous orchestrator over plan, execute, and review
  phases, not a distinct model role.
- Only read-only `explore` and `plan` tasks may run asynchronously.
- Direct and workflow results use a bounded structured hand-back, but the same
  generic payload cannot express role-specific authority.
- The parent Turn owns lifecycle, shared budgets, cancellation, and finalization.
- `minicode/agent_protocol.py` contains an unintegrated persistent-team model
  with autonomous task claiming, broadcasts, and help messages. No production
  imports were found; its collaboration model conflicts with central dispatch.
- `minicode/task_graph.py` is likewise not connected to the production task
  runtime and should not become a second scheduler accidentally.
- `DecisionAuditor` is used by the Agent Loop/Pipeline, but records generic
  reasoning strings rather than a bounded scheduler transition contract.
- `VerificationController` already plans deterministic verification and should
  remain a non-Agent module; models must not make their own checks authoritative.

## Candidate Vocabulary

- **Parent Agent**: sole user-facing authority and scheduling caller.
- **Scheduler**: deterministic module that owns task states and dispatch rules;
  it is not an LLM role.
- **Worker Role**: a capability-bounded model invocation selected by Scheduler.
- **Assignment**: immutable typed input for one Worker Run.
- **Finding**: source-grounded read-only evidence returned by a Worker.
- **Change Set**: isolated workspace delta produced by an Executor.
- **Verdict**: typed review or adjudication decision with explicit authority.
- **Escalation**: Scheduler transition to a stronger authority, not free-form
  agent-to-agent conversation.
- **Run Trace**: correlated state transitions and evidence for one assignment.

## Open Design Risks

- Current `plan` serves both planning and review, weakening role independence.
- Current `general` combines implementation, analysis, and verification.
- Prompt-only `architecture/tests/risks` explore facets do not have capability-
  enforced specialization.
- Majority voting is unsafe when voters share the same prompt, evidence, model,
  or correlated failure mode.

## Implemented Runtime Facts

- Public V2 requests are canonicalized into a sealed request artifact and each
  Worker receives a closed Assignment through a scheduler-owned artifact
  channel; malformed/open hand-offs fail closed.
- Analyst and Reviewer are read/command/write restricted. Executor writes only
  to an isolated transaction and cannot execute commands or mint authoritative
  Change Set evidence.
- The Verifier runs Scheduler-selected checks against the exact frozen patch;
  the Reviewer must cite the Scheduler-owned acceptance evidence matrix and
  approve the same patch digest.
- High/critical risk discovered by the Analyst cannot dynamically rewrite the
  active route: the request terminates for Scheduler-owned reclassification.
  Unresolved unknowns terminate for request revision.
- Parent promotion is serialized behind a durable, write-once
  `SchedulerCommitReady.v1`. The final audit rejoins and revalidates that record
  on write and every later read, including commit-requested failure/timeout
  states; tampering, deletion, or an orphaned seal fails closed.
- V2 terminal causes use a closed stage/outcome policy and a three-way evidence
  modality: Scheduler-owned null references, exact failure receipts, or typed
  role authority. Retry causes must equal the immediately preceding failed
  attempt, so a trace and receipt cannot mutually certify an invented cause.
- Artifact cardinality is budgeted end-to-end. At the legal maximum (one failed
  Analyst attempt, one failed Reviewer attempt, 64 Analyst evidence snapshots,
  40 verification checks, and a Human gate), the final audit uses 125 of 128
  slots. Request/result/link byte and count limits keep all derived artifacts
  representable, with three slots of safety margin.
- The frozen diff is byte-exact and bounded. Non-UTF-8 or JSON-expansive diffs
  use canonical base64 previews; truncated evidence remains replayable for a
  failed audit but can never authorize commit.
- Cancellation/deadline after a successful promotion cannot erase the truthful
  `changesCommitted=true` result; the agent loop marks that tool result for
  mandatory delivery and skips later serial state-changing calls. Uncommitted
  deadline outcomes also persist their final audit before returning failure.
- In-process interruption during parent promotion reconciles the exact target,
  exact baseline, or an explicit unknown state. Empty directories are excluded
  from the promotion digest because a Git patch cannot carry them; a second
  interruption during reconciliation preserves `commit_state_unknown`.
- The Conflict Coordinator is a bounded standalone execution entry point with
  strict typed authority ports, 2-of-3 eligible voting, one expert call, and
  human-only critical/requirement handling. Automatic task-path conflict
  insertion remains deferred.
- Repair/reverify/rereview, clean-snapshot Executor retry, a product human-
  approval UI, and post-crash applied/not-applied inference remain explicit
  deferrals rather than implicit fallback behavior.

## Proposed Minimal Role Direction

- Keep the Parent Agent as the sole semantic and user-facing authority.
- Add a deterministic Central Scheduler module; it is not an Agent.
- Merge `explore` and `plan` into one read-only **Analyst** role that owns the
  entire upstream evidence/plan hand-off.
- Narrow `general` into one write-capable **Executor** role.
- Create one read-only **Reviewer** role with approval authority over an
  isolated change set but no mutation authority.
- Provide an on-demand **Adjudicator** role only for unresolved material
  conflicts; select expertise through a profile/Skill rather than multiplying
  permanent role names.
- Keep verification as deterministic Scheduler-owned execution, not a Worker
  Agent role.

## Verification Evidence

- Focused scheduler/audit/structured/transaction suite: 92 passed.
- Expanded affected-module regression after final boundary hardening: 457
  passed.
- Independent release review reran its selected 140 tests and found no blocking
  issue.
- Full repository suite after all changes: 4871 passed, 2 skipped.
- Ruff and `git diff --check`: passed.
