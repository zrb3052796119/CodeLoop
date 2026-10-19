"""Deterministic central scheduling policy for multi-agent workflows.

The scheduler is deliberately not an Agent.  It converts a bounded task
profile into an ordered dispatch plan and, in later phases, owns workflow
state transitions.  Model output may inform a profile, but it cannot change
the policy or dispatch Workers directly.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from minicode.conflict_port_runner import BoundedConflictPortRunner
from minicode.workflow_repair import (
    MAX_IMPLEMENTATION_REPAIRS,
    REPAIR_TRANSITION_ERROR,
    CONTINUATION_TRANSITION_ERROR,
    validate_continuation_admission,
)

if TYPE_CHECKING:
    from minicode.conflict_port_runner import ConflictPortRunner


SCHEDULER_POLICY_VERSION = 5
SUPPORTED_SCHEDULER_POLICY_VERSIONS = frozenset({2, 3, 4, SCHEDULER_POLICY_VERSION})
_DEFAULT_CONFLICT_AUTHORITY_RUNNER = BoundedConflictPortRunner()
_DEFAULT_CONFLICT_OBSERVER_RUNNER = BoundedConflictPortRunner()


class TaskKind(str, Enum):
    """State-changing shape of one normalized task."""

    READ_ONLY = "read_only"
    CHANGE = "change"


class TaskRisk(str, Enum):
    """Risk authority used by dispatch and escalation policy."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DispatchStage(str, Enum):
    """Closed scheduler-owned stage vocabulary."""

    PARENT = "parent"
    ANALYST = "analyst"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    REVIEWER = "reviewer"
    # Runtime escalation from a blocked Worker stage carrying a frozen
    # ConflictPackage; never selected by a static plan rule.
    ADJUDICATOR = "adjudicator"
    HUMAN = "human"
    COMMIT = "commit"


@dataclass(frozen=True, slots=True)
class TaskProfile:
    """Bounded facts required to select the smallest safe workflow."""

    task_id: str
    kind: TaskKind
    risk: TaskRisk
    complex: bool = False
    parent_has_sufficient_context: bool = False
    scope_clear: bool = False
    acceptance_executable: bool = False
    human_confirmation_required: bool = False
    human_confirmation_granted: bool = False
    human_approval_id: str | None = None
    # Host-selected risk policy; public declarations cannot set this flag.
    independent_review_required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task profile task_id must be non-empty")
        if not isinstance(self.kind, TaskKind) or not isinstance(
            self.risk, TaskRisk
        ):
            raise ValueError("task profile kind and risk must use closed enums")
        flags = (
            self.complex,
            self.parent_has_sufficient_context,
            self.scope_clear,
            self.acceptance_executable,
            self.human_confirmation_required,
            self.human_confirmation_granted,
            self.independent_review_required,
        )
        if any(type(value) is not bool for value in flags):
            raise ValueError("task profile routing flags must be booleans")
        if self.human_confirmation_granted != (
            self.human_approval_id is not None
        ):
            raise ValueError("task profile human approval identity is invalid")
        if self.human_approval_id is not None and (
            not isinstance(self.human_approval_id, str)
            or re.fullmatch(
                r"approval_[A-Za-z0-9][A-Za-z0-9_.-]{3,127}",
                self.human_approval_id,
            )
            is None
        ):
            raise ValueError("task profile human approval identity is invalid")
        if self.human_confirmation_granted and self.risk not in {
            TaskRisk.HIGH,
            TaskRisk.CRITICAL,
        }:
            raise ValueError("task profile human approval is not applicable")


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    """Immutable ordered stages selected by one policy rule."""

    task_id: str
    stages: tuple[DispatchStage, ...]
    rule_id: str
    policy_version: int = SCHEDULER_POLICY_VERSION
    requires_human: bool = False


@dataclass(frozen=True, slots=True)
class StageResult:
    """Internal projection of an already validated role artifact.

    This is not a Worker wire contract.  Role-specific JSON must first pass
    ``multi_agent_contracts``; the scheduler consumes only the authority bits
    needed for ordering and gates.
    """

    status: str
    artifact_id: str
    patch_digest: str | None = None
    approved: bool | None = None
    retryable: bool = False
    # Host-classified implementation feedback, never a Worker wire flag.
    repairable: bool = False
    requires_human: bool = False
    error_code: str | None = None
    # A blocked Worker stage may carry a frozen ConflictPackage.v1 document.
    # The scheduler answers it with one bounded ADJUDICATOR escalation; the
    # package's claims must order the continuation claim second so an
    # explicit ``select_b`` verdict is the only resumption path.
    escalate_conflict: Mapping[str, object] | None = None
    conflict_artifact_id: str | None = None
    # Set only by an ADJUDICATOR stage result: the validated
    # AdjudicationVerdict.v1 document the scheduler's decision maps from.
    conflict_verdict: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "completed",
            "failed",
            "blocked",
            "inconclusive",
        }:
            raise ValueError("stage result status is invalid")
        if not isinstance(self.artifact_id, str) or not self.artifact_id.strip():
            raise ValueError("stage result artifact_id must be non-empty")
        if self.patch_digest is not None and (
            not isinstance(self.patch_digest, str)
            or not self.patch_digest.strip()
        ):
            raise ValueError("stage result patch_digest is invalid")
        if self.approved is not None and type(self.approved) is not bool:
            raise ValueError("stage result approved must be a boolean or null")
        if type(self.retryable) is not bool:
            raise ValueError("stage result retryable must be boolean")
        if type(self.repairable) is not bool:
            raise ValueError("stage result repairable must be boolean")
        if type(self.requires_human) is not bool:
            raise ValueError("stage result requires_human must be boolean")
        if self.requires_human and self.status == "completed":
            raise ValueError("completed stage cannot require human authority")
        if self.error_code is not None and (
            not isinstance(self.error_code, str) or not self.error_code.strip()
        ):
            raise ValueError("stage result error_code is invalid")
        if self.escalate_conflict is not None and (
            self.status != "blocked"
            or not isinstance(self.escalate_conflict, Mapping)
        ):
            raise ValueError("stage escalation requires a blocked ConflictPackage")
        if self.conflict_artifact_id is not None and (
            self.escalate_conflict is None
            or not isinstance(self.conflict_artifact_id, str)
            or not self.conflict_artifact_id.strip()
        ):
            raise ValueError("stage conflict artifact requires an escalation")
        if self.conflict_verdict is not None and (
            self.status != "completed"
            or not isinstance(self.conflict_verdict, Mapping)
        ):
            raise ValueError("stage verdict requires a completed adjudication")


@dataclass(frozen=True, slots=True)
class StageAssignment:
    workflow_id: str
    task_id: str
    assignment_id: str
    attempt_id: str
    attempt: int
    agent_run_id: str
    stage: DispatchStage
    rule_id: str
    input_artifact_ids: tuple[str, ...] = ()
    repair_source_artifact_id: str | None = None
    repair_index: int = 0
    continuation: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class CommitRequest:
    workflow_id: str
    task_id: str
    patch_digest: str
    artifact_ids: tuple[str, ...]
    rule_id: str


@dataclass(frozen=True, slots=True)
class SchedulerTransition:
    sequence: int
    workflow_id: str
    task_id: str
    assignment_id: str
    attempt_id: str
    agent_run_id: str
    stage: DispatchStage
    rule_id: str
    decision_id: str
    parent_decision_id: str | None
    outcome: str
    artifact_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowExecutionOutcome:
    workflow_id: str
    task_id: str
    status: str
    completed_stages: tuple[DispatchStage, ...]
    committed: bool
    failure_stage: DispatchStage | None = None
    terminal_reason: str | None = None
    patch_digest: str | None = None


@dataclass(frozen=True, slots=True)
class SchedulerExecutionPolicy:
    """Closed bounded retry limits; only explicitly retryable failures use them."""

    analyst_max_attempts: int = 2
    executor_max_attempts: int = 2
    verifier_max_attempts: int = 2
    reviewer_max_attempts: int = 2
    # One verdict per conflict by default: a re-ask may only repair a
    # malformed AdjudicationVerdict, never re-litigate a decided one.
    adjudicator_max_attempts: int = 1
    implementation_max_repairs: int = 1

    def __post_init__(self) -> None:
        limits = (
            self.analyst_max_attempts,
            self.executor_max_attempts,
            self.verifier_max_attempts,
            self.reviewer_max_attempts,
            self.adjudicator_max_attempts,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 3
            for value in limits
        ):
            raise ValueError("scheduler retry limits must be between one and three")
        if type(self.implementation_max_repairs) is not int or not (
            0 <= self.implementation_max_repairs <= MAX_IMPLEMENTATION_REPAIRS
        ):
            raise ValueError("implementation repair limit must be between zero and two")

    def max_attempts(self, stage: DispatchStage) -> int:
        return {
            DispatchStage.ANALYST: self.analyst_max_attempts,
            DispatchStage.EXECUTOR: self.executor_max_attempts,
            DispatchStage.VERIFIER: self.verifier_max_attempts,
            DispatchStage.REVIEWER: self.reviewer_max_attempts,
            DispatchStage.ADJUDICATOR: self.adjudicator_max_attempts,
        }.get(stage, 1)


class CentralScheduler:
    """Select centrally controlled flows from normalized task facts."""

    def __init__(
        self,
        *,
        conflict_port_runner: "ConflictPortRunner | None" = None,
        conflict_observer_runner: "ConflictPortRunner | None" = None,
    ) -> None:
        if conflict_port_runner is None:
            conflict_port_runner = _DEFAULT_CONFLICT_AUTHORITY_RUNNER
        if conflict_observer_runner is None:
            conflict_observer_runner = _DEFAULT_CONFLICT_OBSERVER_RUNNER
        if not callable(getattr(conflict_port_runner, "run", None)):
            raise TypeError("conflict_port_runner must implement run()")
        if not callable(getattr(conflict_observer_runner, "run", None)):
            raise TypeError("conflict_observer_runner must implement run()")
        if conflict_observer_runner is conflict_port_runner:
            raise ValueError("conflict runners must use independent capacity")
        self._conflict_port_runner = conflict_port_runner
        self._conflict_observer_runner = conflict_observer_runner

    def plan(
        self, profile: TaskProfile, *, policy_version: int = SCHEDULER_POLICY_VERSION,
    ) -> DispatchPlan:
        """Plan new work with current policy; replay recorded policy explicitly."""
        if (
            type(policy_version) is not int
            or policy_version not in SUPPORTED_SCHEDULER_POLICY_VERSIONS
        ):
            raise ValueError("scheduler policy version is unsupported")
        if (
            profile.kind is TaskKind.READ_ONLY
            and profile.risk is TaskRisk.LOW
            and not profile.complex
            and profile.parent_has_sufficient_context
        ):
            return DispatchPlan(
                task_id=profile.task_id,
                stages=(DispatchStage.PARENT,),
                rule_id="route.parent_only",
                policy_version=policy_version,
            )
        if (
            profile.risk is TaskRisk.CRITICAL
            and profile.kind is TaskKind.READ_ONLY
        ):
            return DispatchPlan(
                task_id=profile.task_id,
                stages=(DispatchStage.ANALYST, DispatchStage.HUMAN),
                rule_id="route.critical_read_only_human_gate",
                policy_version=policy_version,
                requires_human=True,
            )
        if profile.risk is TaskRisk.CRITICAL:
            return DispatchPlan(
                task_id=profile.task_id,
                stages=(
                    DispatchStage.ANALYST,
                    DispatchStage.HUMAN,
                    DispatchStage.EXECUTOR,
                    DispatchStage.VERIFIER,
                    DispatchStage.REVIEWER,
                ),
                rule_id="route.critical_human_gate",
                policy_version=policy_version,
                requires_human=True,
            )
        if (
            profile.kind is TaskKind.CHANGE
            and profile.risk is TaskRisk.HIGH
            and profile.human_confirmation_required
        ):
            return DispatchPlan(
                task_id=profile.task_id,
                stages=(
                    DispatchStage.ANALYST,
                    DispatchStage.HUMAN,
                    DispatchStage.EXECUTOR,
                    DispatchStage.VERIFIER,
                    DispatchStage.REVIEWER,
                ),
                rule_id="route.high_risk_human_gate",
                policy_version=policy_version,
                requires_human=True,
            )
        if profile.kind is TaskKind.READ_ONLY:
            return DispatchPlan(
                task_id=profile.task_id,
                stages=(DispatchStage.ANALYST,),
                rule_id="route.single_analyst",
                policy_version=policy_version,
            )
        if (
            profile.kind is TaskKind.CHANGE
            and profile.risk is TaskRisk.LOW
            and (policy_version != 2 or not profile.complex)
            and profile.scope_clear
            and profile.acceptance_executable
        ):
            if policy_version >= 5 and not profile.independent_review_required and not profile.complex:
                return DispatchPlan(
                    task_id=profile.task_id,
                    stages=(DispatchStage.EXECUTOR, DispatchStage.VERIFIER),
                    rule_id="route.verified_change",
                    policy_version=policy_version,
                )
            # Executable check associations are declarations, not proof that
            # all requirements have assertions. Keep implementation direct,
            # but review semantic coverage before promotion. Recorded v2/v3
            # plans retain their original meaning during audit replay.
            return DispatchPlan(
                task_id=profile.task_id,
                stages=(DispatchStage.EXECUTOR, DispatchStage.VERIFIER)
                + ((DispatchStage.REVIEWER,) if policy_version >= 4 else ()),
                rule_id="route.scoped_change",
                policy_version=policy_version,
            )
        if profile.kind is TaskKind.CHANGE:
            return DispatchPlan(
                task_id=profile.task_id,
                stages=(
                    DispatchStage.ANALYST,
                    DispatchStage.EXECUTOR,
                    DispatchStage.VERIFIER,
                    DispatchStage.REVIEWER,
                ),
                rule_id="route.reviewed_change",
                policy_version=policy_version,
            )
        raise ValueError("task profile is not covered by scheduler policy")

    def execute(
        self,
        profile: TaskProfile,
        *,
        workflow_id: str,
        run_worker: Callable[[StageAssignment], StageResult],
        run_verifier: Callable[[StageAssignment], StageResult],
        commit: Callable[[CommitRequest], bool],
        prepare_commit: Callable[[CommitRequest], bool] | None = None,
        run_human: Callable[[StageAssignment], StageResult] | None = None,
        on_transition: Callable[[SchedulerTransition], None] | None = None,
        policy: SchedulerExecutionPolicy | None = None,
        admit_implementation_repair: Callable[[StageAssignment, StageResult], Mapping[str, object] | bool | None] | None = None,
        deadline_monotonic: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        abort_exceptions: tuple[type[BaseException], ...] = (),
    ) -> WorkflowExecutionOutcome:
        """Execute one immutable dispatch plan through centrally owned gates."""

        if not isinstance(profile, TaskProfile):
            raise TypeError("profile must be a TaskProfile")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("workflow_id must be non-empty")
        if not all(callable(port) for port in (run_worker, run_verifier, commit)):
            raise TypeError("scheduler execution ports must be callable")
        if prepare_commit is not None and not callable(prepare_commit):
            raise TypeError("prepare_commit must be callable or null")
        if run_human is not None and not callable(run_human):
            raise TypeError("run_human must be callable or null")
        if on_transition is not None and not callable(on_transition):
            raise TypeError("on_transition must be callable")
        if admit_implementation_repair is not None and not callable(admit_implementation_repair):
            raise TypeError("admit_implementation_repair must be callable")
        if policy is None:
            policy = SchedulerExecutionPolicy()
        if not isinstance(policy, SchedulerExecutionPolicy):
            raise TypeError("policy must be a SchedulerExecutionPolicy")
        if (
            deadline_monotonic is not None
            and (
                isinstance(deadline_monotonic, bool)
                or not isinstance(deadline_monotonic, (int, float))
            )
        ):
            raise TypeError("deadline_monotonic must be numeric or null")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            not isinstance(abort_exceptions, tuple)
            or any(
                not isinstance(item, type)
                or not issubclass(item, BaseException)
                for item in abort_exceptions
            )
        ):
            raise TypeError("abort_exceptions must be exception classes")

        plan = self.plan(profile)
        pending_stages = list(plan.stages)
        repair_count = 0
        repair_source_artifact_id: str | None = None
        continuation: Mapping[str, object] | None = None
        completed: list[DispatchStage] = []
        artifacts: list[str] = []
        patch_digest: str | None = None
        sequence = 0
        parent_decision_id: str | None = None
        workflow_namespace = hashlib.sha256(
            workflow_id.encode("utf-8")
        ).hexdigest()[:12]

        def assignment_for(
            stage: DispatchStage,
            *,
            ordinal: int,
            attempt: int,
        ) -> StageAssignment:
            return StageAssignment(
                workflow_id=workflow_id,
                task_id=profile.task_id,
                assignment_id=(
                    f"asg_{workflow_namespace}_{ordinal}_{stage.value}"
                ),
                attempt_id=(
                    f"attempt_{workflow_namespace}_{ordinal}_{attempt}"
                ),
                attempt=attempt,
                agent_run_id=(
                    "sub_"
                    + hashlib.sha256(
                        (
                            f"{workflow_id}\0{ordinal}\0{attempt}\0{stage.value}"
                        ).encode("utf-8")
                    ).hexdigest()[:32]
                ),
                stage=stage,
                rule_id=plan.rule_id,
                input_artifact_ids=tuple(artifacts),
                repair_source_artifact_id=(
                    repair_source_artifact_id
                    if stage is DispatchStage.EXECUTOR else None
                ),
                repair_index=repair_count,
                continuation=continuation if stage is DispatchStage.EXECUTOR else None,
            )

        def emit(
            assignment: StageAssignment,
            *,
            outcome: str,
            artifact_id: str | None = None,
            error_code: str | None = None,
        ) -> None:
            nonlocal sequence, parent_decision_id
            sequence += 1
            decision_id = f"decision_{workflow_namespace}_{sequence:04d}"
            transition = SchedulerTransition(
                sequence=sequence,
                workflow_id=workflow_id,
                task_id=profile.task_id,
                assignment_id=assignment.assignment_id,
                attempt_id=assignment.attempt_id,
                agent_run_id=assignment.agent_run_id,
                stage=assignment.stage,
                rule_id=plan.rule_id,
                decision_id=decision_id,
                parent_decision_id=parent_decision_id,
                outcome=outcome,
                artifact_id=artifact_id,
                error_code=error_code,
            )
            if on_transition is not None:
                on_transition(transition)
            parent_decision_id = decision_id

        def terminal(
            status: str,
            stage: DispatchStage,
            reason: str,
        ) -> WorkflowExecutionOutcome:
            return WorkflowExecutionOutcome(
                workflow_id=workflow_id,
                task_id=profile.task_id,
                status=status,
                completed_stages=tuple(completed),
                committed=False,
                failure_stage=stage,
                terminal_reason=reason,
                patch_digest=patch_digest,
            )

        def deadline_reached() -> bool:
            return (
                deadline_monotonic is not None
                and float(clock()) >= float(deadline_monotonic)
            )

        if plan.stages == (DispatchStage.PARENT,):
            assignment = assignment_for(
                DispatchStage.PARENT,
                ordinal=1,
                attempt=1,
            )
            if deadline_reached():
                emit(
                    assignment,
                    outcome="timed_out",
                    error_code="global_deadline_exceeded",
                )
                return terminal(
                    "timed_out",
                    DispatchStage.PARENT,
                    "global_deadline_exceeded",
                )
            emit(assignment, outcome="completed")
            return WorkflowExecutionOutcome(
                workflow_id=workflow_id,
                task_id=profile.task_id,
                status="completed",
                completed_stages=(DispatchStage.PARENT,),
                committed=False,
            )

        for ordinal, stage in enumerate(pending_stages, start=1):
            repair_scheduled = False

            def schedule_implementation_repair(
                candidate: StageResult, failed_assignment: StageAssignment
            ) -> bool:
                nonlocal repair_count, repair_source_artifact_id, patch_digest, continuation
                if (
                    not candidate.repairable
                    or stage not in {DispatchStage.VERIFIER, DispatchStage.REVIEWER}
                    or plan.rule_id not in {"route.scoped_change", "route.reviewed_change", "route.verified_change"}
                    or candidate.requires_human
                    or candidate.escalate_conflict is not None
                    or candidate.patch_digest != patch_digest
                    or patch_digest is None
                    or (
                        stage is DispatchStage.VERIFIER and (
                            candidate.status not in {"failed", "inconclusive"}
                            or candidate.error_code not in {
                                "verification_failed", "acceptance_evidence_incomplete",
                            }
                        )
                    )
                    or (
                        stage is DispatchStage.REVIEWER and (
                            candidate.status != "completed"
                            or candidate.approved is not False
                            or candidate.error_code is not None
                        )
                    )
                    or deadline_reached()
                ):
                    return False
                # A live workflow owner can continue its existing Executor on
                # the shared ledger. None retains the legacy bounded policy;
                # False is an explicit refusal and never falls back to it.
                admission = None
                if admit_implementation_repair is not None:
                    admission = admit_implementation_repair(failed_assignment, candidate)
                if admission is False:
                    return False
                if admission is None:
                    if repair_count >= policy.implementation_max_repairs:
                        return False
                    continuation = None
                else:
                    try:
                        continuation = validate_continuation_admission(admission)
                    except (ValueError, TypeError):
                        return False
                repair_count += 1
                repair_source_artifact_id = candidate.artifact_id
                artifacts.append(candidate.artifact_id)
                emit(
                    failed_assignment,
                    outcome="repairing",
                    artifact_id=candidate.artifact_id,
                    error_code=(CONTINUATION_TRANSITION_ERROR if continuation is not None
                                else REPAIR_TRANSITION_ERROR),
                )
                # Mutating only the unvisited tail preserves a unique ordinal
                # (and therefore fresh identities) for every revised stage.
                executor_index = plan.stages.index(DispatchStage.EXECUTOR)
                pending_stages[ordinal:] = plan.stages[executor_index:]
                completed[:] = [item for item in completed if item not in {
                    DispatchStage.EXECUTOR, DispatchStage.VERIFIER, DispatchStage.REVIEWER,
                }]
                patch_digest = None
                return True

            assignment = assignment_for(stage, ordinal=ordinal, attempt=1)
            if deadline_reached():
                emit(
                    assignment,
                    outcome="timed_out",
                    error_code="global_deadline_exceeded",
                )
                return terminal(
                    "timed_out",
                    stage,
                    "global_deadline_exceeded",
                )
            if stage is DispatchStage.HUMAN:
                if run_human is None:
                    emit(
                        assignment,
                        outcome="waiting_human",
                        error_code="human_approval_required",
                    )
                    return WorkflowExecutionOutcome(
                        workflow_id=workflow_id,
                        task_id=profile.task_id,
                        status="waiting_human",
                        completed_stages=tuple(completed),
                        committed=False,
                        failure_stage=DispatchStage.HUMAN,
                        terminal_reason="human_approval_required",
                    )
            result: StageResult | None = None
            for attempt in range(1, policy.max_attempts(stage) + 1):
                assignment = assignment_for(
                    stage,
                    ordinal=ordinal,
                    attempt=attempt,
                )
                if deadline_reached():
                    emit(
                        assignment,
                        outcome="timed_out",
                        error_code="global_deadline_exceeded",
                    )
                    return terminal(
                        "timed_out",
                        stage,
                        "global_deadline_exceeded",
                    )
                emit(assignment, outcome="dispatched")
                try:
                    if stage is DispatchStage.VERIFIER:
                        candidate = run_verifier(assignment)
                    elif stage is DispatchStage.HUMAN:
                        # Human authority is a host-owned port.  A Worker may
                        # never stand in for it, even when an approval artifact
                        # already exists in the host application.
                        if run_human is None:  # pragma: no cover - guarded above
                            raise RuntimeError("human authority port is missing")
                        candidate = run_human(assignment)
                    else:
                        candidate = run_worker(assignment)
                except abort_exceptions:
                    raise
                except Exception:  # noqa: BLE001 - untyped port failures fail closed
                    if deadline_reached():
                        emit(
                            assignment,
                            outcome="timed_out",
                            error_code="global_deadline_exceeded",
                        )
                        return terminal(
                            "timed_out",
                            stage,
                            "global_deadline_exceeded",
                        )
                    emit(
                        assignment,
                        outcome="failed",
                        error_code="stage_port_failed",
                    )
                    return terminal("failed", stage, "stage_port_failed")
                if deadline_reached():
                    emit(
                        assignment,
                        outcome="timed_out",
                        error_code="global_deadline_exceeded",
                    )
                    return terminal(
                        "timed_out",
                        stage,
                        "global_deadline_exceeded",
                    )
                if not isinstance(candidate, StageResult):
                    emit(
                        assignment,
                        outcome="rejected",
                        error_code="stage_result_invalid",
                    )
                    return terminal("failed", stage, "stage_result_invalid")
                if candidate.status != "completed":
                    if candidate.requires_human:
                        emit(
                            assignment,
                            outcome="waiting_human",
                            artifact_id=candidate.artifact_id,
                            error_code=(
                                candidate.error_code
                                or "human_authority_required"
                            ),
                        )
                        return WorkflowExecutionOutcome(
                            workflow_id=workflow_id,
                            task_id=profile.task_id,
                            status="waiting_human",
                            completed_stages=tuple(completed),
                            committed=False,
                            failure_stage=stage,
                            terminal_reason=(
                                candidate.error_code
                                or "human_authority_required"
                            ),
                            patch_digest=patch_digest,
                        )
                    if candidate.escalate_conflict is not None:
                        # A frozen ConflictPackage converts a terminal-looking
                        # blocked stage into exactly one bounded adjudication.
                        # The verdict is binding: only an unconditional selection
                        # of the continuation claim (ordered second in the
                        # package) resumes the plan; every other decision,
                        # including an inconclusive one, fails closed.
                        emit(
                            assignment,
                            outcome="blocked",
                            artifact_id=candidate.artifact_id,
                            error_code=(
                                candidate.error_code
                                or f"stage_{candidate.status}"
                            ),
                        )
                        if candidate.conflict_artifact_id is not None:
                            artifacts.append(candidate.conflict_artifact_id)
                        adjudicated_result: StageResult | None = None
                        for adjudication_attempt in range(
                            1,
                            policy.max_attempts(DispatchStage.ADJUDICATOR) + 1,
                        ):
                            adjudicator_assignment = assignment_for(
                                DispatchStage.ADJUDICATOR,
                                ordinal=ordinal,
                                attempt=adjudication_attempt,
                            )
                            if deadline_reached():
                                emit(
                                    adjudicator_assignment,
                                    outcome="timed_out",
                                    error_code="global_deadline_exceeded",
                                )
                                return terminal(
                                    "timed_out",
                                    DispatchStage.ADJUDICATOR,
                                    "global_deadline_exceeded",
                                )
                            emit(adjudicator_assignment, outcome="dispatched")
                            try:
                                adj_candidate = run_worker(
                                    adjudicator_assignment
                                )
                            except abort_exceptions:
                                raise
                            except Exception:  # noqa: BLE001 - untyped port failures fail closed
                                emit(
                                    adjudicator_assignment,
                                    outcome="failed",
                                    error_code="stage_port_failed",
                                )
                                return terminal(
                                    "failed",
                                    DispatchStage.ADJUDICATOR,
                                    "stage_port_failed",
                                )
                            if deadline_reached():
                                emit(
                                    adjudicator_assignment,
                                    outcome="timed_out",
                                    error_code="global_deadline_exceeded",
                                )
                                return terminal(
                                    "timed_out",
                                    DispatchStage.ADJUDICATOR,
                                    "global_deadline_exceeded",
                                )
                            if not isinstance(adj_candidate, StageResult) or (
                                adj_candidate.status != "completed"
                                or not isinstance(
                                    adj_candidate.conflict_verdict,
                                    Mapping,
                                )
                            ):
                                adj_error = (
                                    adj_candidate.error_code
                                    if isinstance(adj_candidate, StageResult)
                                    else None
                                ) or "adjudication_result_invalid"
                                adj_artifact = (
                                    adj_candidate.artifact_id
                                    if isinstance(adj_candidate, StageResult)
                                    else None
                                )
                                emit(
                                    adjudicator_assignment,
                                    outcome=(
                                        adj_candidate.status
                                        if isinstance(adj_candidate, StageResult)
                                        else "rejected"
                                    ),
                                    artifact_id=adj_artifact,
                                    error_code=adj_error,
                                )
                                if (
                                    isinstance(adj_candidate, StageResult)
                                    and adj_candidate.retryable
                                    and adjudication_attempt
                                    < policy.max_attempts(
                                        DispatchStage.ADJUDICATOR
                                    )
                                ):
                                    emit(
                                        adjudicator_assignment,
                                        outcome="retrying",
                                        error_code=adj_error,
                                    )
                                    continue
                                return terminal(
                                    "failed",
                                    DispatchStage.ADJUDICATOR,
                                    adj_error,
                                )
                            verdict_decision = str(
                                adj_candidate.conflict_verdict.get("decision")
                            )
                            if (
                                verdict_decision == "select_b"
                                and adj_candidate.conflict_verdict.get("conditions")
                                == []
                            ):
                                emit(
                                    adjudicator_assignment,
                                    outcome="completed",
                                    artifact_id=adj_candidate.artifact_id,
                                )
                                # The escalation lives inside the escalated
                                # stage's attempt: completed_stages stays
                                # plan-pure and ordered.
                                artifacts.append(adj_candidate.artifact_id)
                                adjudicated_result = StageResult(
                                    status="completed",
                                    artifact_id=candidate.artifact_id,
                                    patch_digest=candidate.patch_digest,
                                    approved=candidate.approved,
                                )
                                break
                            adjudication_reason = {
                                "select_a": "adjudicated_reclassification_required",
                                "select_b": "adjudication_conditions_unresolved",
                                "synthesize": "adjudication_decision_not_binding",
                                "insufficient": "adjudication_inconclusive",
                                "human_required": "adjudication_human_required",
                            }.get(
                                verdict_decision,
                                "adjudication_decision_invalid",
                            )
                            emit(
                                adjudicator_assignment,
                                outcome="rejected",
                                artifact_id=adj_candidate.artifact_id,
                                error_code=adjudication_reason,
                            )
                            return terminal(
                                "failed",
                                DispatchStage.ADJUDICATOR,
                                adjudication_reason,
                            )
                        if adjudicated_result is None:  # pragma: no cover - loop shape
                            return terminal(
                                "failed",
                                DispatchStage.ADJUDICATOR,
                                "adjudication_attempts_exhausted",
                            )
                        result = adjudicated_result
                        break
                    emit(
                        assignment,
                        outcome=candidate.status,
                        artifact_id=candidate.artifact_id,
                        error_code=(
                            candidate.error_code
                            or f"stage_{candidate.status}"
                        ),
                    )
                    if (
                        candidate.retryable
                        and attempt < policy.max_attempts(stage)
                    ):
                        emit(
                            assignment,
                            outcome="retrying",
                            error_code=(
                                candidate.error_code
                                or f"stage_{candidate.status}"
                            ),
                        )
                        continue
                    if schedule_implementation_repair(candidate, assignment):
                        repair_scheduled = True
                        break
                    return terminal(
                        "failed",
                        stage,
                        candidate.error_code or f"stage_{candidate.status}",
                    )
                result = candidate
                break
            if repair_scheduled:
                continue
            if result is None:
                return terminal("failed", stage, "stage_attempts_exhausted")

            if stage is DispatchStage.HUMAN:
                if result.approved is not True:
                    rejection = (
                        result.error_code
                        or (
                            "human_approval_rejected"
                            if result.approved is False
                            else "human_approval_missing"
                        )
                    )
                    emit(
                        assignment,
                        outcome="rejected",
                        artifact_id=result.artifact_id,
                        error_code=rejection,
                    )
                    return terminal("failed", stage, rejection)
            elif stage is DispatchStage.EXECUTOR:
                if result.patch_digest is None:
                    emit(
                        assignment,
                        outcome="rejected",
                        artifact_id=result.artifact_id,
                        error_code="executor_patch_digest_missing",
                    )
                    return terminal(
                        "failed",
                        stage,
                        "executor_patch_digest_missing",
                    )
                patch_digest = result.patch_digest
            elif stage is DispatchStage.VERIFIER:
                if patch_digest is None or result.patch_digest != patch_digest:
                    emit(
                        assignment,
                        outcome="rejected",
                        artifact_id=result.artifact_id,
                        error_code="verification_patch_digest_mismatch",
                    )
                    return terminal(
                        "failed",
                        stage,
                        "verification_patch_digest_mismatch",
                    )
            elif stage is DispatchStage.REVIEWER:
                review_rejection: str | None = None
                if patch_digest is None or result.patch_digest != patch_digest:
                    review_rejection = "review_patch_digest_mismatch"
                elif result.approved is not True:
                    review_rejection = result.error_code or "review_not_approved"
                if review_rejection is not None:
                    emit(
                        assignment,
                        outcome="rejected",
                        artifact_id=result.artifact_id,
                        error_code=review_rejection,
                    )
                    if schedule_implementation_repair(result, assignment):
                        continue
                    return terminal("failed", stage, review_rejection)

            artifacts.append(result.artifact_id)
            completed.append(stage)
            emit(
                assignment,
                outcome="completed",
                artifact_id=result.artifact_id,
            )

        if profile.kind is TaskKind.READ_ONLY:
            return WorkflowExecutionOutcome(
                workflow_id=workflow_id,
                task_id=profile.task_id,
                status="completed",
                completed_stages=tuple(completed),
                committed=False,
            )
        if patch_digest is None or DispatchStage.VERIFIER not in completed:
            return terminal(
                "failed",
                DispatchStage.VERIFIER,
                "verification_gate_missing",
            )
        commit_assignment = assignment_for(
            DispatchStage.COMMIT,
            ordinal=len(pending_stages) + 1,
            attempt=1,
        )
        if deadline_reached():
            emit(
                commit_assignment,
                outcome="timed_out",
                error_code="global_deadline_exceeded",
            )
            return terminal(
                "timed_out",
                DispatchStage.COMMIT,
                "global_deadline_exceeded",
            )
        emit(commit_assignment, outcome="commit_requested")
        request = CommitRequest(
            workflow_id=workflow_id,
            task_id=profile.task_id,
            patch_digest=patch_digest,
            artifact_ids=tuple(artifacts),
            rule_id=plan.rule_id,
        )
        if prepare_commit is not None:
            try:
                prepared = prepare_commit(request) is True
            except abort_exceptions:
                raise
            except Exception:  # noqa: BLE001 - persistence gate fails closed
                if deadline_reached():
                    emit(
                        commit_assignment,
                        outcome="timed_out",
                        error_code="global_deadline_exceeded",
                    )
                    return terminal(
                        "timed_out",
                        DispatchStage.COMMIT,
                        "global_deadline_exceeded",
                    )
                prepared = False
            if deadline_reached():
                emit(
                    commit_assignment,
                    outcome="timed_out",
                    error_code="global_deadline_exceeded",
                )
                return terminal(
                    "timed_out",
                    DispatchStage.COMMIT,
                    "global_deadline_exceeded",
                )
            if not prepared:
                emit(
                    commit_assignment,
                    outcome="failed",
                    error_code="commit_ready_persistence_failed",
                )
                return terminal(
                    "failed",
                    DispatchStage.COMMIT,
                    "commit_ready_persistence_failed",
                )
        try:
            committed = commit(request) is True
        except abort_exceptions:
            raise
        except Exception:  # noqa: BLE001 - commit port fails closed
            if deadline_reached():
                emit(
                    commit_assignment,
                    outcome="timed_out",
                    error_code="global_deadline_exceeded",
                )
                return terminal(
                    "timed_out",
                    DispatchStage.COMMIT,
                    "global_deadline_exceeded",
                )
            committed = False
        if not committed and deadline_reached():
            emit(
                commit_assignment,
                outcome="timed_out",
                error_code="global_deadline_exceeded",
            )
            return terminal(
                "timed_out",
                DispatchStage.COMMIT,
                "global_deadline_exceeded",
            )
        emit(
            commit_assignment,
            outcome="completed" if committed else "failed",
            error_code=None if committed else "commit_failed",
        )
        if not committed:
            return terminal("failed", DispatchStage.COMMIT, "commit_failed")
        return WorkflowExecutionOutcome(
            workflow_id=workflow_id,
            task_id=profile.task_id,
            status="completed",
            completed_stages=tuple(completed),
            committed=True,
            patch_digest=patch_digest,
        )


__all__ = [
    "CentralScheduler",
    "DispatchPlan",
    "DispatchStage",
    "CommitRequest",
    "SCHEDULER_POLICY_VERSION",
    "SUPPORTED_SCHEDULER_POLICY_VERSIONS",
    "SchedulerExecutionPolicy",
    "SchedulerTransition",
    "StageAssignment",
    "StageResult",
    "TaskKind",
    "TaskProfile",
    "TaskRisk",
    "WorkflowExecutionOutcome",
]
