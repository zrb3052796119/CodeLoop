# CodeLoop Multi-Agent Collaboration V2 — Role and Orchestration Contract

Status: core implementation and operational verification complete
Scope: production `minicode/tools/task.py` path
Design goal: minimal roles, central scheduling, typed hand-offs, bounded
conflict resolution, fail-closed recovery, and end-to-end traceability

## 1. Architectural decision

CodeLoop V2 has one user-facing Parent Agent, one deterministic Central
Scheduler, three normal Worker Roles, and one exceptional escalation role:

1. **Analyst** — read-only upstream evidence and planning.
2. **Executor** — isolated workspace mutation.
3. **Reviewer** — read-only semantic acceptance of a produced Change Set.
4. **Adjudicator** — on-demand expert resolution of material conflicts only.

The Central Scheduler and Deterministic Verifier are modules, not Agents.
`workflow` is a scheduler-owned flow, not a Worker Role. Domain expertise is a
profile/Skill selected for Analyst, Reviewer, or Adjudicator; it is not a reason
to create a permanent new role.

The normal path uses the fewest roles justified by the task. Adjudicator is not
part of a successful default path.

## 2. Canonical vocabulary

- **Parent Agent** — sole conversational authority. It interprets the user,
  proposes task intent, receives scheduler outcomes, and writes the final answer.
- **Central Scheduler** — deterministic state machine inside the parent runtime.
  It validates assignments, selects flows, dispatches Workers, aggregates typed
  outputs, enforces gates, and records transitions.
- **Worker Role** — one capability-bounded model invocation selected by the
  Scheduler. Workers never claim tasks or negotiate execution order.
- **Assignment** — immutable, versioned input for one Worker Run.
- **Agent Run** — one attempt to execute one Assignment.
- **Finding** — evidence-backed, read-only claim produced by an Analyst.
- **Implementation Order** — locked instructions and acceptance criteria passed
  to an Executor.
- **Change Set** — isolated workspace delta produced by an Executor.
- **Verification Result** — command/file/oracle evidence produced by the
  Deterministic Verifier, never by model self-report.
- **Verdict** — typed Reviewer or Adjudicator judgment. A verdict is an input to
  Scheduler policy, not permission to bypass it.
- **Conflict** — two valid typed claims or verdicts that cannot both hold.
- **Escalation** — Scheduler transition to a stronger authority.
- **Run Trace** — correlated scheduler decisions, Agent Runs, artifacts, and
  terminal outcome for one user task.
- **Workflow Request Artifact** — canonical `WorkflowRequestArtifact.v1` that
  binds the public request and its digest into every V2 hand-off.
- **Acceptance Evidence Matrix** — Scheduler-owned criterion → deterministic
  check → sealed evidence mapping consumed by the Reviewer.
- **Commit Ready Record** — write-once `SchedulerCommitReady.v1` persisted
  before parent-workspace promotion and bound to request, base, patch,
  verification, review, approval, and the complete artifact set.
- **Final Scheduler Audit** — sealed Run-scoped manifest plus canonical
  artifacts, revalidated on both write and read.

## 3. System authorities

### 3.1 Parent Agent

**Owns**

- user intent and latest user instruction;
- task-level decomposition and final synthesis;
- proposing the initial task profile to the Scheduler;
- presenting approval requests and terminal outcomes to the user.

**May**

- solve simple tasks directly;
- request one centrally validated flow;
- summarize accepted Findings, Change Sets, Verification Results, and Verdicts.

**Must not**

- let Workers talk directly to the user;
- accept free-form prose as verification, approval, or scheduling authority;
- bypass Scheduler gates because a Worker sounds confident;
- promote an isolated Change Set before all required gates pass.

### 3.2 Central Scheduler — not an Agent

**Owns**

- workflow state, ordering, dependency readiness, concurrency, retries, budgets,
  timeouts, escalation, commit eligibility, and terminal status;
- Worker selection and role capability enforcement;
- validation and aggregation of every typed hand-off;
- conflict classification and resolution route;
- immutable transition/audit events.

**Must be deterministic for the same normalized inputs and policy version.**
The Parent may propose a route, but the Scheduler validates and may reject it.

### 3.3 Deterministic Verifier — not an Agent

**Owns**

- selecting policy-approved checks from risk and changed-file signals;
- executing commands/oracles under permission controls;
- recording command identity, exit status, duration, and artifact references;
- returning `passed`, `failed`, or `inconclusive`.

It does not interpret product requirements and does not edit files.

## 4. Common Assignment interface

Every Worker receives the same parent-owned envelope. Unknown or extra fields
fail validation.

```json
{
  "assignmentVersion": 1,
  "workflowId": "wf_...",
  "taskId": "task_...",
  "assignmentId": "asg_...",
  "role": "analyst|executor|reviewer|adjudicator",
  "mode": "role-specific closed enum",
  "objective": "one bounded objective",
  "scope": {
    "readRoots": ["..."],
    "writePaths": ["..."],
    "forbiddenPaths": ["..."]
  },
  "constraints": [{"id": "c1", "kind": "...", "text": "..."}],
  "acceptanceCriteria": [
    {"id": "ac1", "kind": "fact|behavior|quality|safety", "text": "..."}
  ],
  "inputRefs": ["artifact_or_evidence_id"],
  "risk": "low|medium|high|critical",
  "budget": {
    "deadlineAt": "RFC3339 timestamp",
    "maxModelCalls": 0,
    "maxInputTokens": 0,
    "maxOutputTokens": 0
  },
  "expectedResultType": "closed versioned type"
}
```

Assignment invariants:

- It is self-contained; a Worker cannot see the parent conversation.
- One Assignment has one objective and one role.
- Identity, role, scope, risk, budgets, and criteria are Scheduler-owned.
- Workers may report a blocker but may not expand scope or budget.
- Every retry keeps `assignmentId` and receives a new `agentRunId/attemptId`.

Implemented cardinality and size bounds:

- one Assignment may reference at most 128 input artifacts;
- one public Workflow Request may declare at most 40 acceptance criteria and
  40 distinct verification checks, with at most 80 criterion-to-check links;
- one Analyst Report may contain at most 64 evidence snapshots and 16,000
  canonical JSON bytes; all other role results retain the 18,000-byte bound;
- duplicate verification command vectors are rejected before dispatch;
- the worst legal audited route (Analyst and Reviewer each retry once, 64
  Analyst evidence snapshots, 40 checks, and a Human gate) consumes 125 of the
  128 available artifact slots, leaving three slots of safety margin.

## 5. Worker Role contracts

### 5.1 Analyst

The Analyst owns the whole read-only front half: investigate, establish facts,
identify unknowns, and produce a candidate Implementation Order when a change
is needed. This merges the current `explore` and `plan` responsibilities.

**Modes**

- `investigate` — answer evidence questions.
- `prepare_change` — investigate and propose a bounded Implementation Order.
- `assess_claim` — return one sealed ballot for an eligible simple conflict.

**May**

- read/search files, symbols, references, AST, policies, Skills, and bounded
  mailbox inputs;
- compare implementation and tests;
- identify affected files, risks, assumptions, and missing evidence;
- propose ordered implementation steps and verification needs.

**Must not**

- modify files or run state-changing commands;
- approve a Change Set;
- declare tests passed without a Verifier Result;
- ask another Worker for help or create sub-assignments;
- hide uncertainty behind a confident summary.

**Input**

- common Assignment;
- explicit questions or change objective;
- immutable repository snapshot/reference;
- optional prior evidence references.

**Output: `AnalystReport.v1`**

```json
{
  "resultVersion": 1,
  "assignmentId": "asg_...",
  "status": "completed|inconclusive|failed",
  "findings": [{
    "claimId": "claim_...",
    "statement": "...",
    "evidence": [{"path": "...", "lineStart": 1, "lineEnd": 1}],
    "confidence": "high|medium|low"
  }],
  "unknowns": ["..."],
  "risks": [{"severity": "low|medium|high|critical", "statement": "...", "evidenceRefs": ["claim_..."]}],
  "implementationOrderCandidate": {
    "required": true,
    "steps": [{"id": "s1", "action": "...", "allowedPaths": ["..."]}],
    "verificationRequests": ["..."]
  },
  "ballot": null
}
```

For `assess_claim`, `implementationOrderCandidate` is absent and `ballot` is:

```json
{"questionId": "q1", "choice": "A|B|ABSTAIN", "evidenceRefs": ["claim_..."], "confidence": "high|medium|low"}
```

**Handoff gate: Analyst → Scheduler/Executor**

- schema and assignment identity valid;
- every material fact has source evidence;
- all `high/critical` risks are explicit;
- unknowns that affect acceptance criteria are resolved or escalated;
- planned paths fit the Assignment scope;
- execution is forbidden when status is not `completed`.

### 5.2 Executor

The Executor owns the write phase only. It receives a locked Implementation
Order and produces one isolated Change Set.

**Mode**: `implement`.

**May**

- read the scoped workspace and accepted upstream artifacts;
- modify only allowed paths inside a disposable transaction;
- report necessary deviations before promotion.

**Must not**

- reinterpret the user goal or redesign the accepted plan silently;
- modify the parent workspace directly;
- spawn Workers;
- run commands, tests, or network operations (those belong to the Verifier);
- approve its own output;
- treat self-run checks as authoritative Verification Results;
- continue after a scope, policy, deadline, or permission violation.

**Input**

- common Assignment;
- `ImplementationOrder.v1` with locked steps and allowed paths;
- accepted evidence/plan references;
- baseline workspace digest.

**Worker output: bounded Executor declaration**

The Worker declares step status, deviations, and blockers only. `artifactRefs`
and `selfChecks` must be empty. It cannot mint a patch digest or Change Set.
After the Worker returns, the Scheduler observes the isolated filesystem and
binds that declaration to the Scheduler-owned `ExecutionResult.v1` below.

**Scheduler-bound output: `ExecutionResult.v1`**

```json
{
  "resultVersion": 1,
  "assignmentId": "asg_...",
  "status": "completed|blocked|failed",
  "changeSet": {
    "workspaceRef": "isolated://...",
    "baseDigest": "sha256:...",
    "patchDigest": "sha256:...",
    "files": [{"path": "...", "action": "created|modified|deleted"}]
  },
  "stepResults": [{"stepId": "s1", "status": "completed|skipped|failed", "artifactRefs": []}],
  "deviations": [{"stepId": "s1", "reason": "...", "impact": "low|medium|high"}],
  "selfChecks": [],
  "blockers": ["..."]
}
```

**Handoff gate: Executor → Verifier**

- completed status and valid Change Set digest;
- actual changed paths are a subset of allowed paths;
- baseline digest still matches;
- no unresolved high-impact deviation;
- every planned step is accounted for;
- failed/timeout runs never expose a promotable Change Set.

### 5.3 Reviewer

The Reviewer owns semantic inspection of a completed Change Set. It is
independent from the Executor, read-only, and normally runs after deterministic
verification.

**Modes**

- `change_review` — inspect implementation against criteria.
- `risk_review` — inspect a high-risk design or proposed operation before write.

**May**

- read original criteria, accepted Analyst evidence, Change Set, source diff,
  and Verifier Results;
- map every criterion to evidence;
- request additional checks through the Scheduler;
- report blocking and non-blocking findings.

**Must not**

- edit the Change Set;
- run unapproved commands itself;
- approve when required verification failed or is missing;
- resolve ambiguous user intent;
- override policy, permissions, or human-approval gates.

**Input**

- common Assignment;
- original goal and acceptance criteria;
- accepted upstream evidence/order;
- `ExecutionResult.v1` and `VerificationResult.v1`.

**Output: `ReviewVerdict.v1`**

```json
{
  "resultVersion": 1,
  "assignmentId": "asg_...",
  "verdict": "approved|changes_required|inconclusive",
  "criteria": [{"criterionId": "ac1", "status": "satisfied|failed|unknown", "evidenceRefs": ["..."]}],
  "blockingFindings": [{"id": "f1", "severity": "high|critical", "statement": "...", "evidenceRefs": ["..."]}],
  "warnings": [{"id": "w1", "severity": "low|medium", "statement": "...", "evidenceRefs": ["..."]}],
  "requestedChecks": ["..."],
  "confidence": "high|medium|low"
}
```

**Handoff gate: Reviewer → Scheduler commit decision**

- verdict `approved`;
- no blocking findings;
- every required criterion is `satisfied`;
- required Verification Result is `passed`;
- no unresolved conflict, high-risk deviation, or human gate;
- Scheduler, not Reviewer, performs the final commit transition.

### 5.4 Adjudicator — exceptional role

The Adjudicator resolves one normalized material conflict. It is selected with
an expertise profile such as `architecture`, `security`, `testing`, `data`, or
`concurrency`. Profiles alter Skills/model route, not the result interface. In
the current implementation it is a host-owned, read-only coordinator port, not
a normal workflow Worker and never receives a mutation or commit capability.

**Mode**: `resolve_conflict`.

**May**

- inspect the normalized Conflict Package and cited evidence;
- request one bounded evidence refresh through the Scheduler;
- select an option, synthesize compatible parts, abstain, or require a human.

**Must not**

- modify code;
- see or rely on private chain-of-thought;
- override deterministic failed checks, explicit user constraints, permissions,
  or human-only policies;
- broaden the original objective.

**Input**

- content-free `ConflictPortRequest` correlation envelope;
- canonical frozen `ConflictPackage.v1` copy;
- immutable evidence references already present in that package.

**Output: `ConflictResolution`**

```json
{
  "status": "resolved|escalated|waiting_human",
  "route": "adjudicator",
  "choice": "one frozen choice or null",
  "reason": "bounded reason code"
}
```

Bare string decisions and unknown choices fail closed. The Scheduler accepts
an adjudication only if its route matches the called port and it resolves the
scoped conflict without violating a stronger authority.

## 6. Central scheduling policy

### 6.1 Minimal-dispatch rule

The Scheduler uses this precedence:

1. **Parent only** when the task is simple, within current context, low risk,
   and needs neither independent evidence nor workspace mutation.
2. **One Analyst** for a complex read-only question or when the Parent lacks
   reliable repository evidence.
3. **One Executor + Verifier** is a policy route for a local, low-risk change
   with a Scheduler-owned verification recipe. The public V2 request path keeps
   Reviewer enabled until such a trusted recipe registry is available.
4. **Analyst → Executor → Verifier → Reviewer** for public V2 changes and for cross-module, ambiguous,
   medium/high-risk, public-interface, permission, security, concurrency,
   migration, or user-requested-review changes.
5. Insert **Human after Analyst and before Executor** for critical changes and
   high-risk changes whose normalized profile requires confirmation. Critical
   read-only work is `Analyst → Human` and never reaches an Executor.
6. **Multiple Analysts** only when scopes are independent or a conflict is
   explicitly eligible for voting.
7. **Adjudicator** only after deterministic resolution and eligible voting
   cannot resolve a material conflict.
8. **Human** for critical risk, irreversible/external actions, permission
   expansion, unresolved requirement ambiguity, or exhausted adjudication.

No Worker may cause another Worker to be dispatched. Workers return requests;
the Scheduler alone decides whether they create a new Assignment.

### 6.2 Concurrency rule

- Only Analyst Assignments over the same immutable snapshot may overlap.
- Parallel Analyst Assignments must have disjoint `questionId/scope` or be
  sealed ballots for one voting round.
- Executors are globally serialized per workspace.
- Reviewer starts only after Executor and required Verifier runs are terminal.
- Adjudicator starts only after the Conflict Package is frozen.
- Parallel Workers never exchange notes before their results are sealed.

### 6.3 State machine

```text
RECEIVED
  -> CLASSIFIED
  -> DIRECT ------------------------------------------> COMPLETED
  -> ANALYSIS_QUEUED -> ANALYZING -> ANALYSIS_GATE
       -> CONFLICTED -> RESOLVING -> ANALYSIS_GATE
       -> EXECUTION_QUEUED
  -> EXECUTING -> CHANGE_GATE
       -> VERIFYING -> VERIFICATION_GATE
       -> REVIEW_QUEUED -> REVIEWING -> REVIEW_GATE
       -> COMMIT_READY -> COMMITTING -> COMPLETED

Any non-terminal state may transition to:
  RETRYING | FALLBACK | WAITING_HUMAN | CANCELLED | TIMED_OUT | FAILED
```

The current implementation retries only explicitly retryable malformed/failed
Analyst and Reviewer attempts. Patch repair/reverify/rereview is deliberately
not implemented yet: a failed Verification Result or `changes_required`
Reviewer verdict terminates fail-closed and discards the snapshot.

Every transition is allowed by a closed policy table and records a `ruleId`.

### 6.4 Review triggers

Reviewer is mandatory when any of these holds:

- risk is `high` or `critical`;
- public interface, authorization, security, data schema/migration, concurrency,
  permissions, packaging, or deletion is affected;
- more than one module ownership area changes;
- Executor reports a material deviation;
- Verifier is inconclusive but policy permits review rather than immediate stop;
- user explicitly requests review;
- a prior repair attempt failed.

Reviewer may be skipped only for a scoped low-risk change whose deterministic
criteria all pass and whose policy has no review trigger.

## 7. Conflict resolution

### 7.1 Conflict priority

The Scheduler resolves conflicts in this order:

1. explicit user constraint or human decision;
2. safety/permission policy;
3. deterministic Verifier or immutable source evidence;
4. evidence-quality comparison;
5. eligible low-risk vote;
6. expert Adjudicator;
7. human confirmation or terminal `inconclusive`.

A lower layer cannot override a higher layer. A model vote can never overturn a
failed executable check or explicit safety policy.

### 7.2 Conflict types

- **Fact conflict** — refresh targeted source evidence first.
- **Plan conflict** — compare constraints/criteria; adjudicate material tradeoffs.
- **Review conflict** — a blocking Review Verdict prevents commit until repaired
  or adjudicated.
- **Risk conflict** — use the highest credible severity by default.
- **Requirement conflict** — ask the human; Agents cannot invent intent.
- **Verification conflict** — deterministic result wins; repair or stop.

### 7.3 Voting eligibility

Majority voting is allowed only when all conditions hold:

- the question is low risk, closed, and has finite choices;
- each ballot evaluates the same frozen question/evidence snapshot;
- at least three valid sealed ballots exist;
- voters did not communicate before sealing;
- no ballot lacks required evidence;
- model/evidence independence policy is satisfied;
- the result cannot authorize a write, permission change, or high-risk action by
  itself.

Two disagreeing Agents do not create a majority. If independence cannot be
demonstrated, the Scheduler skips voting and escalates. `ABSTAIN` is not a vote
for either side. A winner needs more than half of all valid non-abstaining
ballots and at least two votes.

## 8. Failure, fallback, and graceful termination

| Failure point | Default response | Fallback | Promotion rule |
|---|---|---|---|
| Analyst transient failure | Retry same Assignment once | Backup model/profile; then Parent handles only if safe | Inconclusive analysis cannot authorize execution |
| Analyst evidence conflict | Targeted evidence refresh | Vote if eligible, otherwise Adjudicator | Unresolved material conflict blocks next phase |
| Executor fails before/while writing | Cancel and discard snapshot | Current implementation terminates; clean-snapshot retry is deferred | Partial Change Set is never promoted |
| Executor violates scope | Immediate fail-closed | No blind retry; return to Parent/Analyst | Treat as policy failure |
| Verifier check fails | Terminate and discard snapshot | Repair loop is deferred | Failed required check blocks commit |
| Verifier unavailable | Mark inconclusive and terminate | Infrastructure retry is deferred | Required verification cannot be waived by a Worker |
| Reviewer fails/malformed | Fail-closed | One backup Reviewer | No valid approval means no commit |
| Reviewer requests changes | Terminate and discard snapshot | Repair/reverify/rereview is deferred | Old approval cannot survive a new patch digest |
| Adjudicator unavailable/inconclusive | Stop escalation | Human confirmation | Never silently choose a side |
| Global deadline/budget exhausted | Stop dispatch and request cancellation | Return bounded partial evidence | Unapproved snapshot is discarded |

Graceful termination procedure:

1. freeze the workflow in a terminal-intent state;
2. stop new dispatches;
3. cancel queued jobs and signal running read-only jobs cooperatively;
4. wait for a bounded finalization barrier;
5. discard every unapproved isolated Change Set;
6. seal partial typed results as `inconclusive`, never `passed`;
7. emit the exact terminal reason and last completed state to the Parent.

## 9. Traceability contract

Every record carries:

- `workflowId` — one user task execution;
- `taskId` — normalized task identity;
- `assignmentId` — one immutable role assignment;
- `agentRunId` — one Worker attempt (maps from current `subagentId`);
- `attemptId` — retry ordinal;
- `decisionId` and `parentDecisionId` — scheduler transition chain;
- `artifactId/digest` — evidence, plan, patch, verification, and verdict identity;
- `policyVersion` and `ruleId` — why the transition was permitted;
- timestamps, model-call/token/tool counters, status, and bounded error code.

Required scheduler events:

- `workflow.classified`
- `scheduler.decision`
- `assignment.dispatched`
- `agent.started`
- `agent.completed`
- `handoff.validated` / `handoff.rejected`
- `conflict.detected`
- `conflict.resolved` / `conflict.escalated`
- `fallback.applied`
- `verification.completed`
- `review.completed`
- `commit.decided`
- `workflow.terminated`

Audit records store reason codes and evidence references, not hidden
chain-of-thought or unrestricted prompts.

## 10. Mapping from the current runtime

| Current concept | V2 disposition |
|---|---|
| `explore` | Merge into Analyst `investigate` mode |
| `plan` | Merge upstream planning into Analyst; split review into Reviewer |
| `general` | Rename/narrow to Executor |
| `workflow` task role | Replace with Scheduler-owned flow definition |
| three prompt-only workflow explorers | Use scoped Analyst Assignments only when dispatch policy justifies them |
| generic sub-agent result | Keep common envelope; add role-specific result union |
| async lifecycle/finalization barrier | Reuse and extend with Assignment/attempt identity |
| shared Agent Turn budget/deadline | Reuse as Scheduler-owned global budget plus per-stage reservations |
| sub-agent journal/observation | Reuse; add scheduler transition and artifact correlation events |
| `VerificationController` | Reuse as basis of Deterministic Verifier planning |
| `agent_protocol.py` autonomous claiming/messages | Do not integrate; deprecate or quarantine from production path |
| `task_graph.py` | Either adapt behind the one Scheduler interface or retire; never expose a second scheduler |
| `DecisionAuditor` | Replace generic reasoning with bounded scheduler decision records |

## 11. Non-negotiable invariants

1. One scheduler per workflow.
2. No Worker-created work.
3. No direct Worker-to-Worker scheduling or free-form negotiation.
4. No concurrent writers in one workspace.
5. No model self-report promoted to deterministic verification.
6. No Executor self-approval.
7. No commit without matching patch digest across verification and review.
8. No vote for high-risk, irreversible, permission, or requirement decisions.
9. No malformed/missing typed result silently treated as success.
10. No parent finalization while owned Worker results remain unobserved.

## 12. Implemented core and explicit deferrals

Implemented in the production `task.py` path:

- closed `WorkflowRequest`, Assignment, Analyst, Executor, Verifier, Reviewer,
  conflict, approval, evidence-matrix, commit-ready, and audit contracts;
- deterministic route/attempt/run/decision identities and content-free
  transition traces;
- capability-enforced read-only Analyst/Reviewer and isolated, path-bounded,
  command-denied Executor transactions;
- frozen base/patch/diff binding, deterministic verification evidence, exact
  criterion-to-check matrix, and independent semantic Reviewer approval;
- Analyst-discovered unknowns terminate for request revision; newly discovered
  high/critical risks terminate for Scheduler-owned reclassification;
- human gates after Analyst for configured high-risk and all critical routes,
  with stable approval-request identity and exact request/base/analysis binding;
- write-once pre-commit `SchedulerCommitReady.v1`, parent promotion only after
  it is durable, and a final Run-scoped audit that is revalidated against the
  commit-ready record during both persistence and offline reads;
- truthful post-commit cancellation/deadline reporting and mandatory delivery
  of the finalized state-changing tool result;
- bounded conflict coordination with authority precedence, exactly three
  sealed ballots for eligible low-risk votes, one Adjudicator fallback, and
  human-only critical/requirement resolution.

Explicitly deferred or host-owned:

- The host must provide the actual human approval UI/store. The runtime exposes
  a stable provider seam and returns `waiting_human`; it does not fabricate an
  approval.
- `ConflictCoordinator.execute` is implemented and tested, but automatic
  detection and insertion of conflict subflows into the main task workflow is
  not yet wired. Until then, task-path conflicts terminate at their current
  gate and cannot silently authorize a commit.
- Independent ballot execution is a host scheduling obligation. The
  coordinator issues distinct voter/partition identities, requires each sealed
  ballot to echo them, and restricts citations to frozen evidence references;
  it still cannot prove hardware/model independence from dispatch metadata
  alone.
- Patch repair, clean-snapshot Executor retry, and verifier infrastructure retry
  are not implemented. These paths terminate fail-closed and discard the
  isolated snapshot.
- The public scoped-change fast route remains disabled until a trusted,
  Scheduler-owned check-recipe registry exists; public V2 changes retain a
  Reviewer.
- A commit-ready record without a final audit is reported as
  `commit_state_unknown`. The current pre-commit record does not include enough
  post-crash filesystem evidence to infer whether promotion happened.
- Audit/approval provider callbacks are host interfaces and must themselves be
  bounded and cancellation-aware; Python cannot preempt an arbitrary blocking
  callback safely.
- Native verifier sandbox strength is platform-dependent. The Darwin adapter
  and fail-closed permission/toolchain gates are implemented; unsupported host
  backends must not be described as equivalent isolation.

## 13. Design acceptance tests

Implementation is not complete until contract tests prove at least:

- Parent-only routing for a simple task;
- exactly one Analyst for a complex read-only task;
- no unnecessary Analyst/Reviewer for an eligible simple low-risk change;
- Analyst → Executor → Verifier → Reviewer ordering for a high-risk change;
- parallel Analysts cannot write or exchange pre-result messages;
- Executor cannot modify a forbidden path or spawn a Worker;
- failed/malformed hand-offs block the next phase;
- a failed deterministic check cannot be overridden by voting/review prose;
- two-way disagreement does not masquerade as majority;
- eligible 2-of-3 low-risk vote resolves deterministically;
- complex conflict reaches Adjudicator, critical conflict reaches human;
- Worker timeout uses bounded retry/fallback and records the exact failing step;
- Executor timeout discards its isolated snapshot;
- new patch digest invalidates old verification and approval;
- global timeout cancels pending work and emits a traceable terminal outcome;
- every final result joins task, assignment, Agent Run, decision, and artifact IDs;
- the maximum legal request/result shape remains auditable within the fixed
  artifact-channel capacity;
- non-UTF-8 and JSON-expansive frozen diffs preserve an exact bounded digest
  representation without allowing truncated evidence to authorize commit.
