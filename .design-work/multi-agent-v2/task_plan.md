# Task Plan: Multi-Agent Collaboration V2

## Goal

Define and then implement a minimal, centrally orchestrated multi-agent runtime
whose role interfaces, scheduling rules, hand-offs, conflict resolution,
fallbacks, and audit trail are explicit and testable.

## Phases

- [x] Phase 1: Capture design goals and non-negotiable constraints
- [x] Phase 2: Audit current role, lifecycle, result, budget, and workflow seams
- [x] Phase 3: Define the minimal role model and typed role interfaces
- [x] Phase 4: Define the scheduler state machine and dispatch policy
- [x] Phase 5: Define conflict resolution, escalation, fallback, and termination
- [x] Phase 6: Map the design onto current modules and identify migrations
- [x] Phase 7: Review the design with the user before implementation
- [x] Phase 8: Implement with contract and counterexample tests
- [x] Phase 9: Run verification and document operational evidence

## Non-Negotiable Constraints

1. The parent agent is the sole scheduler, authority holder, result aggregator,
   and user-facing decision maker.
2. Prefer one agent when one role can complete the task safely and verifiably.
3. Spawn additional agents only for independent evidence gathering, a
   write/review separation, or explicit conflict adjudication.
4. Agent hand-offs use typed, bounded data. Free-form prose is presentation,
   not scheduling or approval authority.
5. Only read-only work may overlap. Workspace mutation is serialized and
   transactionally isolated.
6. Every dispatch, transition, retry, conflict, verdict, timeout, and fallback
   must be attributable to one task, one agent run, and one scheduler decision.
7. High-risk or irreversible actions require human approval regardless of
   model consensus.

## Key Questions

1. Which current roles should remain primitive roles, and which should become
   scheduler phases or specializations?
2. What exact conditions justify delegation or parallelism?
3. What evidence must cross each role seam?
4. When is a conflict eligible for voting, expert adjudication, or human review?
5. Which failures may be retried, degraded to the parent, or must terminate?

## Decisions Made

- Central orchestration is authoritative; agents do not negotiate the workflow.
- Role count will be minimized before adding any expert specialization.
- The current `workflow` concept is treated as an orchestrator, not a peer agent.
- Normal Worker Roles are Analyst, Executor, and Reviewer; Adjudicator is an
  exceptional escalation role selected with a domain expertise profile.
- Verification remains a deterministic module and cannot be replaced by model
  self-report.
- Majority voting is restricted to low-risk, finite-choice, independently
  sealed judgments and can never override policy or deterministic evidence.
- Existing repository-level `task_plan.md` and `notes.md` belong to another
  completed experiment and are not modified by this design effort.

## Errors Encountered

- None.

## Status

**Complete** — the core runtime, contracts, failure paths, and durable audit
projection are implemented. Final evidence: 457 affected tests passed; the full
repository suite passed with 4871 tests and 2 skips; the independent release
review found no blocker; Ruff, syntax compilation, and `git diff --check` also
passed.
