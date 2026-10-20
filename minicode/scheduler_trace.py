"""Content-free, JSON-safe trace artifact for central scheduler decisions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from minicode.central_scheduler import (
    SUPPORTED_SCHEDULER_POLICY_VERSIONS,
    DispatchPlan,
    DispatchStage,
    SchedulerTransition,
    WorkflowExecutionOutcome,
)
from minicode.workflow_repair import (
    CONTINUATION_TRANSITION_ERROR, MAX_IMPLEMENTATION_REPAIRS, REPAIR_TRANSITION_ERROR,
)


SCHEDULER_TRACE_VERSION = 1
SCHEDULER_TRACE_MARKER = "=== SCHEDULER TRACE v1 ==="
PARENT_WORKFLOW_HANDOFF_MARKER = "=== SCHEDULER HANDOFF v1 ==="
_SOLO_FALLBACK_LEAD = "Workflow failed, so this handoff is closed."


def output_has_solo_fallback(output: str) -> bool:
    """True when the workflow result already tells the parent to finish solo."""

    return _SOLO_FALLBACK_LEAD in (output or "")

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$")
_RULE_RE = re.compile(r"^route\.[a-z][a-z0-9_]{0,63}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_STATUSES = frozenset({"completed", "failed", "waiting_human", "timed_out"})
_TRANSITION_OUTCOMES = frozenset(
    {
        "blocked",
        "commit_requested",
        "completed",
        "dispatched",
        "failed",
        "inconclusive",
        "rejected",
        "repairing",
        "retrying",
        "timed_out",
        "waiting_human",
    }
)
_TRACE_FIELDS = frozenset(
    {
        "schedulerTraceVersion",
        "workflowId",
        "taskId",
        "policyVersion",
        "ruleId",
        "status",
        "committed",
        "terminalReason",
        "transitions",
    }
)
_TRANSITION_FIELDS = frozenset(
    {
        "sequence",
        "workflowId",
        "taskId",
        "assignmentId",
        "attemptId",
        "agentRunId",
        "decisionId",
        "parentDecisionId",
        "stage",
        "outcome",
        "artifactId",
        "errorCode",
    }
)
_STAGES = frozenset(stage.value for stage in DispatchStage)
_MAX_TRANSITIONS = 256
_MAX_JSON_CHARS = 200_000
_RULE_STAGE_ORDER = {
    "route.parent_only": ("parent",),
    "route.single_analyst": ("analyst",),
    "route.scoped_change": ("executor", "verifier", "commit"),
    "route.verified_change": ("executor", "verifier", "commit"),
    "route.critical_read_only_human_gate": ("analyst", "human"),
    "route.critical_human_gate": (
        "analyst",
        "human",
        "executor",
        "verifier",
        "reviewer",
        "commit",
    ),
    "route.high_risk_human_gate": (
        "analyst",
        "human",
        "executor",
        "verifier",
        "reviewer",
        "commit",
    ),
    "route.reviewed_change": (
        "analyst",
        "executor",
        "verifier",
        "reviewer",
        "commit",
    ),
}
_COMMIT_RULES = frozenset(
    {
        "route.scoped_change",
        "route.verified_change",
        "route.reviewed_change",
        "route.critical_human_gate",
        "route.high_risk_human_gate",
    }
)
_ATTEMPT_TERMINALS = frozenset(
    {
        "blocked",
        "completed",
        "failed",
        "inconclusive",
        "rejected",
        "timed_out",
        "waiting_human",
    }
)
_PREDISPATCH_TERMINALS = frozenset({"timed_out", "waiting_human"})
_IMPLEMENTATION_STAGES = frozenset({"executor", "verifier", "reviewer"})
_REPAIR_RULES = frozenset({"route.scoped_change", "route.reviewed_change", "route.verified_change"})


def _validate_repair_boundaries(
    transitions: list[Mapping[str, object]], *, rule_id: str
) -> bool:
    """Authorize only explicit, bounded failure-to-new-implementation edges."""
    historical_ids: dict[str, set[object]] = {
        key: set() for key in ("assignmentId", "attemptId", "agentRunId")
    }
    prior_round_ids = {key: set() for key in historical_ids}
    repairs = 0
    for index, item in enumerate(transitions):
        stage = item.get("stage")
        if stage in _IMPLEMENTATION_STAGES:
            if any(item.get(key) in values for key, values in prior_round_ids.items()):
                return False
            for key in historical_ids:
                historical_ids[key].add(item.get(key))
        if item.get("outcome") != "repairing":
            continue
        repairs += 1
        if (rule_id not in _REPAIR_RULES or not 0 < index < len(transitions) - 1
                or item.get("errorCode") == REPAIR_TRANSITION_ERROR
                and repairs > MAX_IMPLEMENTATION_REPAIRS):
            return False
        previous, following = transitions[index - 1], transitions[index + 1]
        if (
            stage not in {"verifier", "reviewer"}
            or previous.get("outcome") not in {"failed", "inconclusive", "rejected"}
            or any(previous.get(key) != item.get(key) for key in (
                "stage", "assignmentId", "attemptId", "agentRunId", "artifactId"
            ))
            or item.get("artifactId") is None
            or item.get("errorCode") not in {REPAIR_TRANSITION_ERROR, CONTINUATION_TRANSITION_ERROR}
            or following.get("stage") != "executor"
            or following.get("outcome") not in {"dispatched", "timed_out"}
        ):
            return False
        prior_round_ids = {key: set(values) for key, values in historical_ids.items()}
    return True


def _effective_completed_stages(
    transitions: list[Mapping[str, object]],
) -> set[str]:
    completed: set[str] = set()
    for item in transitions:
        if item.get("outcome") == "repairing":
            completed.difference_update(_IMPLEMENTATION_STAGES)
        elif item.get("outcome") == "completed":
            completed.add(str(item.get("stage")))
    return completed


def _valid_attempt_lifecycle(
    outcomes: list[str],
    *,
    adjudicated: bool = False,
) -> bool:
    if len(outcomes) == 1:
        return outcomes[0] in _PREDISPATCH_TERMINALS
    if outcomes[0] != "dispatched" or outcomes[1] not in _ATTEMPT_TERMINALS:
        return False
    if len(outcomes) == 2:
        return True
    if (
        len(outcomes) == 3
        and adjudicated
        and outcomes[1] == "blocked"
        and outcomes[2] == "completed"
    ):
        # One frozen-conflict escalation: the blocked attempt resumed after
        # a binding adjudication selected the continuation claim.
        return True
    return (
        len(outcomes) == 3
        and outcomes[1] != "completed"
        and outcomes[2] in {"retrying", "repairing"}
    )


_ADJUDICATION_TERMINALS = frozenset(
    {"completed", "rejected", "failed", "timed_out"}
)


def _valid_adjudication_block(outcomes: list[str]) -> bool:
    """Validate one ADJUDICATOR escalation block's outcome sequence."""

    if not outcomes:
        return False
    if outcomes == ["timed_out"]:
        return True
    if outcomes[0] != "dispatched":
        return False
    if len(outcomes) == 2:
        return outcomes[1] in _ADJUDICATION_TERMINALS
    if len(outcomes) == 4 and outcomes[2] == "retrying":
        # One bounded verdict-repair retry before the terminal decision.
        return outcomes[3] in _ADJUDICATION_TERMINALS
    return False


def _split_adjudication_escalations(
    transitions: list[SchedulerTransition],
) -> tuple[list[SchedulerTransition], set[tuple[object, ...]]]:
    """Split ADJUDICATOR escalation blocks out of a plan-stage trace.

    Each block must directly follow a plan stage's ``blocked`` transition —
    the frozen-conflict escalation point — and carry a bounded verdict
    lifecycle.  The blocked attempt's identity is returned so its
    ``dispatched, blocked, completed`` resumption stays legal.
    """

    plan_transitions: list[SchedulerTransition] = []
    adjudicated_identities: set[tuple[object, ...]] = set()
    index = 0
    while index < len(transitions):
        transition = transitions[index]
        if transition.stage is not DispatchStage.ADJUDICATOR:
            plan_transitions.append(transition)
            index += 1
            continue
        if not plan_transitions or plan_transitions[-1].outcome != "blocked":
            raise ValueError(
                "scheduler trace adjudication lacks its blocked escalation"
            )
        escalated = plan_transitions[-1]
        identity = (
            escalated.stage,
            escalated.assignment_id,
            escalated.attempt_id,
            escalated.agent_run_id,
        )
        if identity in adjudicated_identities:
            raise ValueError(
                "scheduler trace adjudicates one conflict twice"
            )
        block: list[SchedulerTransition] = []
        while (
            index < len(transitions)
            and transitions[index].stage is DispatchStage.ADJUDICATOR
        ):
            block.append(transitions[index])
            index += 1
        if not _valid_adjudication_block(
            [item.outcome for item in block]
        ):
            raise ValueError(
                "scheduler trace adjudication lifecycle is invalid"
            )
        adjudicated_identities.add(identity)
    return plan_transitions, adjudicated_identities


def _valid_global_lifecycle(
    events: list[tuple[str, str, tuple[str, str, str, str]]],
    *,
    ordered_stages: tuple[str, ...],
    committed: bool,
) -> bool:
    """Validate cross-attempt retry, stage-prefix, and commit invariants."""
    if ordered_stages == ("parent",):
        return (
            committed is False
            and len(events) == 1
            and events[0][0] == "parent"
            and events[0][1] in {"completed", "timed_out"}
        )
    stage_positions = {
        stage: index for index, stage in enumerate(ordered_stages)
    }
    completed: set[str] = set()
    current_attempt: dict[str, tuple[str, str, str, str]] = {}
    for index, (stage, outcome, identity) in enumerate(events):
        if outcome == "repairing":
            completed.difference_update(_IMPLEMENTATION_STAGES)
            for implementation_stage in _IMPLEMENTATION_STAGES:
                current_attempt.pop(implementation_stage, None)
            continue
        position = stage_positions.get(stage)
        if position is None or any(
            prior not in completed for prior in ordered_stages[:position]
        ):
            return False
        if stage in completed:
            return False
        if stage not in {"parent", "commit"}:
            prior_identity = current_attempt.get(stage)
            if prior_identity is None:
                current_attempt[stage] = identity
            elif identity != prior_identity:
                if index == 0:
                    return False
                previous_stage, previous_outcome, previous_identity = events[
                    index - 1
                ]
                if (
                    previous_stage != stage
                    or previous_outcome != "retrying"
                    or previous_identity != prior_identity
                    or identity[1] != prior_identity[1]
                    or identity[2] == prior_identity[2]
                    or identity[3] == prior_identity[3]
                ):
                    return False
                current_attempt[stage] = identity
        if outcome == "retrying":
            if index + 1 >= len(events):
                return False
            next_stage, next_outcome, next_identity = events[index + 1]
            if (
                next_stage != stage
                or next_identity == identity
                or next_outcome not in {"dispatched", "timed_out"}
            ):
                return False
        if outcome == "completed":
            completed.add(stage)

    commit_events = [
        (outcome, identity)
        for stage, outcome, identity in events
        if stage == "commit"
    ]
    commit_outcomes = [outcome for outcome, _identity in commit_events]
    commit_identity_is_stable = len(
        {identity for _outcome, identity in commit_events}
    ) <= 1
    if committed:
        return (
            commit_identity_is_stable
            and commit_outcomes == ["commit_requested", "completed"]
        )
    return (
        commit_identity_is_stable
        and (
            not commit_outcomes
            or commit_outcomes == ["timed_out"]
            or (
                len(commit_outcomes) == 2
                and commit_outcomes[0] == "commit_requested"
                and commit_outcomes[1] in {"failed", "timed_out"}
            )
        )
    )


def _require_identifier(
    value: object,
    *,
    prefixes: tuple[str, ...],
) -> str:
    if (
        not isinstance(value, str)
        or _ID_RE.fullmatch(value) is None
        or not value.startswith(prefixes)
    ):
        raise ValueError("scheduler trace values must be content-free identifiers")
    return value


def _validate_content_free_values(
    plan: DispatchPlan,
    outcome: WorkflowExecutionOutcome,
    transitions: list[SchedulerTransition],
) -> None:
    _require_identifier(plan.task_id, prefixes=("task_",))
    if not isinstance(plan.rule_id, str) or _RULE_RE.fullmatch(plan.rule_id) is None:
        raise ValueError("scheduler trace values must be content-free identifiers")
    _require_identifier(outcome.workflow_id, prefixes=("wf_",))
    _require_identifier(outcome.task_id, prefixes=("task_",))
    if outcome.status not in _STATUSES or type(outcome.committed) is not bool:
        raise ValueError("scheduler trace values must be content-free enums")
    if outcome.terminal_reason is not None and (
        not isinstance(outcome.terminal_reason, str)
        or _REASON_RE.fullmatch(outcome.terminal_reason) is None
    ):
        raise ValueError("scheduler trace values must be content-free reason codes")
    for transition in transitions:
        _require_identifier(transition.workflow_id, prefixes=("wf_",))
        _require_identifier(transition.task_id, prefixes=("task_",))
        _require_identifier(transition.assignment_id, prefixes=("asg_",))
        _require_identifier(transition.attempt_id, prefixes=("attempt_",))
        _require_identifier(transition.agent_run_id, prefixes=("sub_",))
        _require_identifier(transition.decision_id, prefixes=("decision_",))
        if transition.parent_decision_id is not None:
            _require_identifier(
                transition.parent_decision_id,
                prefixes=("decision_",),
            )
        if not isinstance(transition.stage, DispatchStage):
            raise ValueError("scheduler trace values must be content-free enums")
        if transition.outcome not in _TRANSITION_OUTCOMES:
            raise ValueError("scheduler trace values must be content-free enums")
        if transition.artifact_id is not None:
            _require_identifier(
                transition.artifact_id,
                prefixes=("artifact_", "verification_"),
            )
        if transition.error_code is not None and (
            not isinstance(transition.error_code, str)
            or _REASON_RE.fullmatch(transition.error_code) is None
        ):
            raise ValueError(
                "scheduler trace values must be content-free reason codes"
            )


def _validate_scheduler_state(
    plan: DispatchPlan,
    outcome: WorkflowExecutionOutcome,
    transitions: list[SchedulerTransition],
) -> None:
    planned_stages = plan.stages
    completed_stages = outcome.completed_stages
    if (
        type(plan.policy_version) is not int
        or not 1 <= plan.policy_version <= 10_000
        or type(plan.requires_human) is not bool
        or not isinstance(planned_stages, tuple)
        or not 1 <= len(planned_stages) <= len(DispatchStage)
        or any(not isinstance(stage, DispatchStage) for stage in planned_stages)
        or len(set(planned_stages)) != len(planned_stages)
        or DispatchStage.COMMIT in planned_stages
        or plan.requires_human != (DispatchStage.HUMAN in planned_stages)
        or not isinstance(completed_stages, tuple)
        or any(not isinstance(stage, DispatchStage) for stage in completed_stages)
        or len(set(completed_stages)) != len(completed_stages)
        or any(stage not in planned_stages for stage in completed_stages)
        or tuple(
            stage for stage in planned_stages if stage in completed_stages
        )
        != completed_stages
        or not 1 <= len(transitions) <= _MAX_TRANSITIONS
    ):
        raise ValueError("scheduler trace scheduler state is invalid")
    # Escalation blocks (frozen-conflict adjudications) interleave with plan
    # stages; every plan-oriented invariant below runs on the plan-pure
    # stream, and the failure-stage check keeps the original terminal
    # transition — an adjudication rejection ends the workflow.
    plan_transitions, adjudicated_identities = _split_adjudication_escalations(
        transitions
    )
    if not plan_transitions:
        raise ValueError("scheduler trace scheduler state is invalid")
    if outcome.failure_stage is not None and not isinstance(
        outcome.failure_stage, DispatchStage
    ):
        raise ValueError("scheduler trace scheduler state is invalid")
    if (
        (outcome.status == "completed" and outcome.terminal_reason is not None)
        or (outcome.status != "completed" and outcome.terminal_reason is None)
        or (outcome.committed and outcome.status != "completed")
        or (outcome.status == "completed" and outcome.failure_stage is not None)
        or (
            outcome.status in {"failed", "timed_out"}
            and outcome.failure_stage is None
        )
        or (
            outcome.status == "waiting_human"
            and (
                (
                    outcome.failure_stage is None
                    and not plan.requires_human
                )
                or (
                    outcome.failure_stage is not None
                    and outcome.failure_stage not in planned_stages
                )
            )
        )
        or (
            outcome.status == "completed"
            and completed_stages != planned_stages
        )
    ):
        raise ValueError("scheduler trace scheduler state is invalid")

    stage_order = {stage: index for index, stage in enumerate(planned_stages)}
    stage_order[DispatchStage.COMMIT] = len(planned_stages)
    last_stage_index = -1
    decision_ids: set[str] = set()
    commit_completed = False
    for transition in plan_transitions:
        index = stage_order.get(transition.stage)
        if (
            index is None
            or index < last_stage_index
            or transition.decision_id in decision_ids
        ):
            raise ValueError("scheduler trace scheduler state is invalid")
        last_stage_index = (
            stage_order.get(DispatchStage.EXECUTOR, 0) - 1
            if transition.outcome == "repairing" else index
        )
        decision_ids.add(transition.decision_id)
        commit_completed = commit_completed or (
            transition.stage is DispatchStage.COMMIT
            and transition.outcome == "completed"
        )
    observed_completed_stages: set[DispatchStage] = set()
    for transition in plan_transitions:
        if transition.outcome == "repairing":
            observed_completed_stages.difference_update(
                {DispatchStage.EXECUTOR, DispatchStage.VERIFIER, DispatchStage.REVIEWER}
            )
        elif transition.outcome == "completed":
            if transition.stage in observed_completed_stages:
                raise ValueError("scheduler trace stage completed more than once")
            observed_completed_stages.add(transition.stage)
    dispatched_identities: set[tuple[DispatchStage, str, str, str]] = set()
    attempt_lifecycles: dict[
        tuple[DispatchStage, str, str, str], list[str]
    ] = {}
    for transition in plan_transitions:
        identity = (
            transition.stage,
            transition.assignment_id,
            transition.attempt_id,
            transition.agent_run_id,
        )
        if transition.stage not in {DispatchStage.PARENT, DispatchStage.COMMIT}:
            attempt_lifecycles.setdefault(identity, []).append(
                transition.outcome
            )
        if transition.outcome == "dispatched":
            if identity in dispatched_identities:
                raise ValueError("scheduler trace dispatch is duplicated")
            dispatched_identities.add(identity)
        elif (
            transition.outcome == "completed"
            and transition.stage
            not in {DispatchStage.PARENT, DispatchStage.COMMIT}
            and (
                identity not in dispatched_identities
                or transition.artifact_id is None
            )
        ):
            raise ValueError(
                "scheduler trace completed stage lacks its dispatch lifecycle"
            )
    if any(
        not _valid_attempt_lifecycle(
            outcomes,
            adjudicated=identity in adjudicated_identities,
        )
        for identity, outcomes in attempt_lifecycles.items()
    ):
        raise ValueError("scheduler trace attempt lifecycle is invalid")
    typed_events = [
        (
            transition.stage.value,
            transition.outcome,
            (
                transition.stage.value,
                transition.assignment_id,
                transition.attempt_id,
                transition.agent_run_id,
            ),
        )
        for transition in plan_transitions
    ]
    if not _valid_global_lifecycle(
        typed_events,
        ordered_stages=tuple(
            [stage.value for stage in planned_stages]
            + (["commit"] if plan.rule_id in _COMMIT_RULES else [])
        ),
        committed=outcome.committed,
    ):
        raise ValueError("scheduler trace global lifecycle is invalid")
    if (
        plan.rule_id == "route.parent_only"
        and plan_transitions[0].artifact_id is not None
    ):
        raise ValueError("scheduler parent trace cannot reference an artifact")
    actual_completed_stages = tuple(
        stage
        for stage in planned_stages
        if stage in observed_completed_stages
    )
    if actual_completed_stages != completed_stages:
        raise ValueError("scheduler trace completed stages are inconsistent")
    if outcome.status != "completed" and (
        outcome.failure_stage is None
        or transitions[-1].stage is not outcome.failure_stage
        or outcome.terminal_reason != transitions[-1].error_code
    ):
        raise ValueError("scheduler trace failure stage is inconsistent")
    if (
        outcome.status == "completed"
        and not set(planned_stages).issubset(observed_completed_stages)
    ):
        raise ValueError("scheduler trace skipped a completed stage")
    if outcome.committed and (
        not commit_completed
        or not any(
            transition.stage is DispatchStage.COMMIT
            and transition.outcome == "commit_requested"
            for transition in transitions
        )
    ):
        raise ValueError("scheduler trace scheduler state is invalid")


def _normalize_scheduler_trace(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) != _TRACE_FIELDS:
        return None
    if type(value.get("schedulerTraceVersion")) is not int or (
        value.get("schedulerTraceVersion") != SCHEDULER_TRACE_VERSION
    ):
        return None
    policy_version = value.get("policyVersion")
    committed = value.get("committed")
    status = value.get("status")
    terminal_reason = value.get("terminalReason")
    transitions = value.get("transitions")
    if (
        type(policy_version) is not int
        or policy_version not in SUPPORTED_SCHEDULER_POLICY_VERSIONS
        or type(committed) is not bool
        or status not in _STATUSES
        or not isinstance(transitions, list)
        or not 1 <= len(transitions) <= _MAX_TRANSITIONS
        or (
            terminal_reason is not None
            and (
                not isinstance(terminal_reason, str)
                or _REASON_RE.fullmatch(terminal_reason) is None
            )
        )
    ):
        return None
    try:
        workflow_id = _require_identifier(
            value.get("workflowId"),
            prefixes=("wf_",),
        )
        task_id = _require_identifier(
            value.get("taskId"),
            prefixes=("task_",),
        )
    except ValueError:
        return None
    rule_id = value.get("ruleId")
    if not isinstance(rule_id, str) or rule_id not in _RULE_STAGE_ORDER:
        return None
    if rule_id == "route.verified_change" and policy_version < 5:
        return None
    ordered_stages = (
        ("executor", "verifier", "reviewer", "commit")
        if rule_id == "route.scoped_change" and policy_version >= 4
        else _RULE_STAGE_ORDER[rule_id]
    )
    if (
        (status == "completed" and terminal_reason is not None)
        or (status != "completed" and terminal_reason is None)
        or (committed and status != "completed")
        or (
            status == "completed"
            and (rule_id in _COMMIT_RULES) != committed
        )
    ):
        return None

    normalized_transitions: list[dict[str, object]] = []
    parent_decision_id: str | None = None
    decision_ids: set[str] = set()
    stage_order = {
        stage: index for index, stage in enumerate(ordered_stages)
    }
    last_stage_index = -1
    commit_completed = False
    dispatched_identities: set[tuple[str, str, str, str]] = set()
    attempt_lifecycles: dict[tuple[str, str, str, str], list[str]] = {}
    adjudication_blocks: list[list[str]] = []
    current_block: list[str] | None = None
    adjudicated_identities: set[tuple[str, str, str, str]] = set()
    previous_item: dict[str, object] | None = None
    for expected_sequence, item in enumerate(transitions, start=1):
        if not isinstance(item, Mapping) or set(item) != _TRANSITION_FIELDS:
            return None
        sequence = item.get("sequence")
        stage = item.get("stage")
        transition_outcome = item.get("outcome")
        artifact_id = item.get("artifactId")
        error_code = item.get("errorCode")
        positional_ok = stage in stage_order and not (
            stage_order[stage] < last_stage_index
        )
        if stage == "adjudicator":
            # Escalation blocks attach positionally to the plan stage whose
            # blocked transition directly precedes them.
            previous_stage = (
                str(previous_item.get("stage")) if previous_item else None
            )
            if previous_stage == "adjudicator":
                positional_ok = last_stage_index >= 0
            elif (
                previous_item is not None
                and previous_stage in stage_order
                and previous_item.get("outcome") == "blocked"
                and last_stage_index >= 0
            ):
                positional_ok = True
                if current_block is not None:
                    adjudication_blocks.append(current_block)
                current_block = [str(transition_outcome)]
                adjudicated_identities.add(
                    (
                        previous_stage,
                        str(previous_item.get("assignmentId")),
                        str(previous_item.get("attemptId")),
                        str(previous_item.get("agentRunId")),
                    )
                )
            else:
                positional_ok = False
            if current_block is not None and positional_ok:
                if previous_stage == "adjudicator":
                    current_block.append(str(transition_outcome))
        if (
            type(sequence) is not int
            or sequence != expected_sequence
            or item.get("workflowId") != workflow_id
            or item.get("taskId") != task_id
            or stage not in _STAGES
            or not positional_ok
            or transition_outcome not in _TRANSITION_OUTCOMES
        ):
            return None
        try:
            assignment_id = _require_identifier(
                item.get("assignmentId"),
                prefixes=("asg_",),
            )
            attempt_id = _require_identifier(
                item.get("attemptId"),
                prefixes=("attempt_",),
            )
            agent_run_id = _require_identifier(
                item.get("agentRunId"),
                prefixes=("sub_",),
            )
            decision_id = _require_identifier(
                item.get("decisionId"),
                prefixes=("decision_",),
            )
            if decision_id in decision_ids:
                return None
            if item.get("parentDecisionId") != parent_decision_id:
                return None
            if parent_decision_id is not None:
                _require_identifier(
                    parent_decision_id,
                    prefixes=("decision_",),
                )
            if artifact_id is not None:
                artifact_id = _require_identifier(
                    artifact_id,
                    prefixes=("artifact_", "verification_"),
                )
            if error_code is not None and (
                not isinstance(error_code, str)
                or _REASON_RE.fullmatch(error_code) is None
            ):
                return None
        except ValueError:
            return None
        successful_outcome = transition_outcome in {
            "commit_requested",
            "completed",
            "dispatched",
        }
        if successful_outcome != (error_code is None):
            return None
        normalized_transitions.append(
            {
                "sequence": sequence,
                "workflowId": workflow_id,
                "taskId": task_id,
                "assignmentId": assignment_id,
                "attemptId": attempt_id,
                "agentRunId": agent_run_id,
                "decisionId": decision_id,
                "parentDecisionId": parent_decision_id,
                "stage": stage,
                "outcome": transition_outcome,
                "artifactId": artifact_id,
                "errorCode": error_code,
            }
        )
        identity = (stage, assignment_id, attempt_id, agent_run_id)
        if stage == "adjudicator":
            pass
        elif stage not in {"parent", "commit"}:
            attempt_lifecycles.setdefault(identity, []).append(
                str(transition_outcome)
            )

        if transition_outcome == "dispatched":
            if identity in dispatched_identities:
                return None
            dispatched_identities.add(identity)
        elif (
            transition_outcome == "completed"
            and stage not in {"parent", "commit"}
            and (
                identity not in dispatched_identities
                or artifact_id is None
            )
        ):
            return None
        parent_decision_id = decision_id
        decision_ids.add(decision_id)
        if stage != "adjudicator":
            last_stage_index = (
                stage_order.get("executor", 0) - 1
                if transition_outcome == "repairing" else stage_order[stage]
            )
        commit_completed = commit_completed or (
            stage == "commit" and transition_outcome == "completed"
        )
        previous_item = item

    if current_block is not None:
        adjudication_blocks.append(current_block)
    if not _validate_repair_boundaries(normalized_transitions, rule_id=rule_id):
        return None
    if any(
        not _valid_adjudication_block(block) for block in adjudication_blocks
    ):
        return None
    if any(
        not _valid_attempt_lifecycle(
            outcomes,
            adjudicated=identity in adjudicated_identities,
        )
        for identity, outcomes in attempt_lifecycles.items()
    ):
        return None
    normalized_events = [
        (
            str(transition["stage"]),
            str(transition["outcome"]),
            (
                str(transition["stage"]),
                str(transition["assignmentId"]),
                str(transition["attemptId"]),
                str(transition["agentRunId"]),
            ),
        )
        for transition in normalized_transitions
        if str(transition["stage"]) != "adjudicator"
    ]
    if not _valid_global_lifecycle(
        normalized_events,
        ordered_stages=ordered_stages,
        committed=committed,
    ):
        return None
    if rule_id == "route.parent_only" and normalized_transitions[0][
        "artifactId"
    ] is not None:
        return None

    last_outcome = normalized_transitions[-1]["outcome"]
    expected_terminal_outcomes = {
        "completed": frozenset({"completed"}),
        "failed": frozenset({"blocked", "failed", "inconclusive", "rejected"}),
        "timed_out": frozenset({"timed_out"}),
        "waiting_human": frozenset({"waiting_human"}),
    }
    completed_stage_names = _effective_completed_stages(normalized_transitions)
    required_completed_stages = set(ordered_stages)
    commit_requested = any(
        transition["stage"] == "commit"
        and transition["outcome"] == "commit_requested"
        for transition in normalized_transitions
    )
    if (
        last_outcome not in expected_terminal_outcomes[status]
        or (
            status != "completed"
            and terminal_reason
            != normalized_transitions[-1]["errorCode"]
        )
        or committed != commit_completed
        or (
            status == "completed"
            and not required_completed_stages.issubset(
                completed_stage_names
            )
        )
        or (committed and not commit_requested)
    ):
        return None

    return {
        "schedulerTraceVersion": SCHEDULER_TRACE_VERSION,
        "workflowId": workflow_id,
        "taskId": task_id,
        "policyVersion": policy_version,
        "ruleId": rule_id,
        "status": status,
        "committed": committed,
        "terminalReason": terminal_reason,
        "transitions": normalized_transitions,
    }


def project_scheduler_trace(
    plan: DispatchPlan,
    outcome: WorkflowExecutionOutcome,
    transitions: list[SchedulerTransition],
) -> dict[str, object]:
    """Project scheduler-owned state into a content-free trace artifact."""
    if not isinstance(plan, DispatchPlan) or not isinstance(
        outcome, WorkflowExecutionOutcome
    ):
        raise ValueError("scheduler trace input type is invalid")
    if not isinstance(transitions, list) or any(
        not isinstance(transition, SchedulerTransition)
        for transition in transitions
    ):
        raise ValueError("scheduler trace transition input is invalid")
    _validate_content_free_values(plan, outcome, transitions)
    _validate_scheduler_state(plan, outcome, transitions)
    if plan.task_id != outcome.task_id:
        raise ValueError("scheduler trace task correlation is inconsistent")
    parent_decision_id: str | None = None
    for expected_sequence, transition in enumerate(transitions, start=1):
        if (
            transition.sequence != expected_sequence
            or transition.workflow_id != outcome.workflow_id
            or transition.task_id != outcome.task_id
            or transition.rule_id != plan.rule_id
            or transition.parent_decision_id != parent_decision_id
        ):
            raise ValueError("scheduler trace transition correlation is inconsistent")
        parent_decision_id = transition.decision_id
    projected = {
        "schedulerTraceVersion": SCHEDULER_TRACE_VERSION,
        "workflowId": outcome.workflow_id,
        "taskId": outcome.task_id,
        "policyVersion": plan.policy_version,
        "ruleId": plan.rule_id,
        "status": outcome.status,
        "committed": outcome.committed,
        "terminalReason": outcome.terminal_reason,
        "transitions": [
            {
                "sequence": transition.sequence,
                "workflowId": transition.workflow_id,
                "taskId": transition.task_id,
                "assignmentId": transition.assignment_id,
                "attemptId": transition.attempt_id,
                "agentRunId": transition.agent_run_id,
                "decisionId": transition.decision_id,
                "parentDecisionId": transition.parent_decision_id,
                "stage": transition.stage.value,
                "outcome": transition.outcome,
                "artifactId": transition.artifact_id,
                "errorCode": transition.error_code,
            }
            for transition in transitions
        ],
    }
    normalized = _normalize_scheduler_trace(projected)
    if normalized is None:
        raise ValueError("scheduler trace schema is invalid")
    return normalized


def render_scheduler_trace(trace: Mapping[str, object]) -> str:
    """Render a trace after a marker suitable for appending to task output."""
    normalized = _normalize_scheduler_trace(trace)
    if normalized is None:
        raise ValueError("scheduler trace schema is invalid")
    return SCHEDULER_TRACE_MARKER + "\n" + json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _phase_highlights(trace: Mapping[str, object]) -> list[str]:
    raw = trace.get("transitions")
    if not isinstance(raw, list):
        return []
    phases: list[str] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("outcome") == "dispatched":
            continue
        stage = item.get("stage")
        outcome = item.get("outcome")
        if not isinstance(stage, str) or not isinstance(outcome, str):
            continue
        label = f"{stage}:{outcome}"
        if not phases or phases[-1] != label:
            phases.append(label)
        if len(phases) == 8:
            break
    return phases


def parent_workflow_handoff(output: str, *, tool_ok: bool) -> dict[str, object]:
    """Compact facts a parent can read without the full transition log."""

    trace = extract_scheduler_trace(output)
    solo = _SOLO_FALLBACK_LEAD in (output or "")
    if trace is None:
        return {
            "committed": False,
            "ok": bool(tool_ok),
            "phases": [],
            "ran": False,
            "ruleId": None,
            "scheduler": "central",
            "soloFallback": solo,
            "status": "unavailable",
            "terminalReason": "trace_unavailable",
        }
    return {
        "committed": bool(trace.get("committed")),
        "ok": bool(tool_ok),
        "phases": _phase_highlights(trace),
        "ran": True,
        "ruleId": trace.get("ruleId"),
        "scheduler": "central",
        "soloFallback": solo,
        "status": trace.get("status"),
        "terminalReason": trace.get("terminalReason"),
    }


def render_parent_workflow_handoff(output: str, *, tool_ok: bool) -> str:
    payload = parent_workflow_handoff(output, tool_ok=tool_ok)
    return PARENT_WORKFLOW_HANDOFF_MARKER + "\n" + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def attach_parent_workflow_handoff(output: str, *, tool_ok: bool) -> str:
    """Put the compact handoff, and any solo-fallback note, at the front.

    The full scheduler trace stays in the body. ``extract_scheduler_trace``
    still reads that trace. A later preview that keeps only the head therefore
    still shows whether CentralScheduler ran, the rule, and the verdict.
    """

    text = output or ""
    if text.startswith(PARENT_WORKFLOW_HANDOFF_MARKER):
        return text
    note = ""
    lead_at = text.find(_SOLO_FALLBACK_LEAD)
    if lead_at >= 0:
        note_and_after = text[lead_at:]
        note, separator, after = note_and_after.partition("\n\n")
        text = text[:lead_at].rstrip()
        if separator:
            text = (text + "\n\n" + after).strip()
        note = note.strip()
    summary = render_parent_workflow_handoff(
        (text + "\n" + note).strip(),
        tool_ok=tool_ok,
    )
    parts = [summary]
    if note:
        parts.append(note)
    if text.strip():
        parts.append(text.strip())
    return "\n\n".join(parts)


def extract_scheduler_trace(output: object) -> dict[str, object] | None:
    """Extract a rendered trace even when a result artifact follows it."""
    if not isinstance(output, str):
        return None
    _before, marker, after = output.rpartition(SCHEDULER_TRACE_MARKER)
    if not marker:
        return None
    payload = after.lstrip().split("\n", 1)[0]
    if not payload or len(payload) > _MAX_JSON_CHARS:
        return None
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return _normalize_scheduler_trace(value)


__all__ = [
    "PARENT_WORKFLOW_HANDOFF_MARKER",
    "SCHEDULER_TRACE_MARKER",
    "SCHEDULER_TRACE_VERSION",
    "attach_parent_workflow_handoff",
    "extract_scheduler_trace",
    "parent_workflow_handoff",
    "project_scheduler_trace",
    "render_parent_workflow_handoff",
    "render_scheduler_trace",
]
