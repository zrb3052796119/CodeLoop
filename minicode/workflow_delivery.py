"""Ephemeral authority for an acknowledged workflow in the current parent turn.

This is delivery evidence, never proof that an arbitrary user goal is complete.
Only the finalizer issues it inside an actual tool invocation. It is not parsed
from model text, persisted messages, todo state, or a generic success marker.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import json
import threading


_SEAL = object()
_CURRENT_INVOCATION = ContextVar("workflow_delivery_invocation", default=None)


@dataclass(slots=True)
class _Invocation:
    owner: object
    call_id: str
    structured_workflow: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class WorkflowDeliveryReceipt:
    workflow_id: str
    task_id: str
    patch_digest: str
    files: tuple[tuple[str, str], ...]
    checks: tuple[tuple[str, tuple[str, ...]], ...]
    _invocation: _Invocation
    _seal: object


@dataclass(frozen=True, slots=True)
class WorkflowFailureReceipt:
    workflow_id: str
    task_id: str
    reason: str
    commit: str
    patch_digest: str | None
    files: tuple[tuple[str, str], ...]
    checks: tuple[tuple[str, tuple[str, ...], str, int | None], ...]
    review: str
    candidate_ref: str | None
    budget_terminal: bool
    _invocation: _Invocation
    _seal: object


def claim_workflow_handoff(workflow_id: str, task_id: str) -> bool:
    """Only the parsed structured runner may exchange its parent's model floor.

    The active parent invocation supplies deterministic delivery, including an
    unknown outcome if finalization fails. Ancestor budget views are untouched.
    """
    invocation = _CURRENT_INVOCATION.get()
    if invocation is None:
        return False
    if invocation.structured_workflow not in (None, (workflow_id, task_id)):
        return False
    invocation.structured_workflow = (workflow_id, task_id)
    return True


def _unknown_failure(invocation) -> WorkflowFailureReceipt:
    return WorkflowFailureReceipt(
        *invocation.structured_workflow, "workflow_result_unavailable", "unknown",
        None, (), (), "unknown", None, False, invocation, _SEAL,
    )


def issue_workflow_failure(snapshot, state) -> WorkflowFailureReceipt | None:
    """A failed delivery is evidence of failure, never completion authority."""
    invocation = _CURRENT_INVOCATION.get()
    outcome = snapshot.scheduler_outcome
    if (invocation is None or invocation.structured_workflow != (outcome.workflow_id, outcome.task_id)
            or snapshot.contract_mode != "v2" or snapshot.dispatch_plan.policy_version not in {4, 5}):
        return None
    if (outcome.committed or state.finalization_status != "completed"
            or state.audit_status != "persisted" or state.trace_projection_failed):
        return _unknown_failure(invocation)
    execution = json.loads(snapshot.execution_artifact_json or "null")
    verification = json.loads(snapshot.verification_result_json or "null")
    review = json.loads(snapshot.typed_review_json)
    changes = execution.get("changeSet", {}) if isinstance(execution, dict) else {}
    digest = changes.get("patchDigest")
    files = tuple((item["path"], item["action"]) for item in changes.get("files", []))
    commands = []
    if isinstance(verification, dict) and digest and verification.get("patchDigest") == digest:
        artifacts = {item.artifact_id: item for item in snapshot.artifacts or ()}
        for check in verification.get("checks", []):
            artifact = artifacts.get(check.get("artifactRef"))
            if artifact is None:
                return _unknown_failure(invocation)
            evidence = json.loads(artifact.content)
            argv = evidence.get("argv")
            if (evidence.get("checkId") != check.get("checkId")
                    or evidence.get("status") != check.get("status")
                    or evidence.get("exitCode") != check.get("exitCode")
                    or not isinstance(argv, list) or not argv
                    or any(not isinstance(item, str) for item in argv)):
                return _unknown_failure(invocation)
            commands.append((check["checkId"], tuple(argv), check["status"], check.get("exitCode")))
    return WorkflowFailureReceipt(
        outcome.workflow_id, outcome.task_id, outcome.terminal_reason or "workflow_incomplete",
        "not_applied", digest, files, tuple(commands), str(review.get("verdict", "unknown")),
        None, False, invocation, _SEAL,
    )


def bind_workflow_failure(result, *, budget_terminal=False, candidate_ref=None):
    """Attach host observations only while their exact invocation is active."""
    receipt = getattr(result, "_workflow_delivery_receipt", None)
    invocation = _CURRENT_INVOCATION.get()
    if (isinstance(receipt, WorkflowFailureReceipt) and receipt._seal is _SEAL
            and receipt._invocation is invocation):
        receipt = replace(receipt, budget_terminal=receipt.budget_terminal or budget_terminal,
                          candidate_ref=(candidate_ref if receipt.commit == "not_applied" and candidate_ref
                                         else receipt.candidate_ref))
        return replace(result, _workflow_delivery_receipt=receipt)
    return result


def issue_workflow_delivery(snapshot, state) -> WorkflowDeliveryReceipt | None:
    """Project only finalized, persisted, independently audited host facts."""
    invocation = _CURRENT_INVOCATION.get()
    outcome = snapshot.scheduler_outcome
    if (invocation is None or snapshot.contract_mode != "v2"
            or snapshot.dispatch_plan.policy_version not in {4, 5}
            or not outcome.committed or outcome.status != "completed"
            or snapshot.commit_ready_status != "persisted"
            or state.finalization_status != "completed" or state.audit_status != "persisted"):
        return None
    execution = json.loads(snapshot.execution_artifact_json or "null")
    verification = json.loads(snapshot.verification_result_json or "null")
    review = json.loads(snapshot.typed_review_json)
    execution_delivered = isinstance(execution, dict) and (
        execution.get("status") == "completed"
        or (execution.get("resultType") == "ExecutionCandidate.v1"
            and execution.get("status") == "awaiting_verification"
            and execution.get("handoffReason") == "executor_budget_exceeded")
    )
    # The persisted audit and commit-ready seal have already validated the
    # host's v5 routing decision. Do not invent a Reviewer verdict for a
    # user-bound acceptance route, or weaken reviewed routes in other policies.
    host_acceptance_route = (
        snapshot.dispatch_plan.policy_version == 5
        and snapshot.dispatch_plan.rule_id == "route.verified_change"
        and not snapshot.profile.independent_review_required
    )
    if (not execution_delivered
            or not isinstance(verification, dict) or verification.get("status") != "passed"
            or (not host_acceptance_route and review.get("verdict") != "approved")
            or review.get("blockingFindings")):
        return None
    change_set = execution.get("changeSet", {})
    digest = change_set.get("patchDigest")
    files = change_set.get("files", [])
    checks = verification.get("checks", [])
    if (not isinstance(digest, str) or not digest or digest != outcome.patch_digest
            or verification.get("patchDigest") != digest or not files or not checks
            or any(item.get("status") != "passed" or item.get("exitCode") != 0 for item in checks)):
        return None
    artifacts = {item.artifact_id: item for item in snapshot.artifacts or ()}
    commands = []
    for check in checks:
        artifact = artifacts.get(check.get("artifactRef"))
        if artifact is None:
            return None
        evidence = json.loads(artifact.content)
        argv = evidence.get("argv")
        if (evidence.get("checkId") != check.get("checkId") or evidence.get("status") != "passed"
                or evidence.get("exitCode") != 0 or not isinstance(argv, list)
                or not argv or any(not isinstance(item, str) for item in argv)):
            return None
        commands.append((check["checkId"], tuple(argv)))
    return WorkflowDeliveryReceipt(
        outcome.workflow_id, outcome.task_id, digest,
        tuple((item["path"], item["action"]) for item in files),
        tuple(commands),
        invocation, _SEAL,
    )


class TurnWorkflowDelivery:
    """One-turn receipt ownership, exact invocation consumption, and residuals."""

    def __init__(self):
        self._owner = object()
        self._lock = threading.Lock()
        self._invocations = {}
        self.receipts: list[WorkflowDeliveryReceipt | WorkflowFailureReceipt] = []
        self.remaining: list[str] = []
        self.budget_terminal = False

    @contextmanager
    def invocation(self, call):
        value = call.get("input")
        eligible = (call.get("toolName") == "task" and isinstance(value, dict)
                    and value.get("agent_type") == "workflow" and value.get("action", "run") == "run")
        invocation = _Invocation(self._owner, str(call.get("id"))) if eligible else None
        if invocation is not None:
            with self._lock:
                self._invocations[invocation.call_id] = invocation
        token = _CURRENT_INVOCATION.set(invocation)
        try:
            yield
        finally:
            _CURRENT_INVOCATION.reset(token)

    def observe(self, call, result, *, acknowledged: bool, suppressed: bool, read_only: bool):
        receipt = getattr(result, "_workflow_delivery_receipt", None)
        accepted = False
        success = (isinstance(receipt, WorkflowDeliveryReceipt) and result.ok
                   and result._must_deliver and result._continue_parent)
        failure = isinstance(receipt, WorkflowFailureReceipt) and (not result.ok or receipt.commit == "unknown")
        if (not suppressed and acknowledged and (success or failure)
                and receipt._seal is _SEAL and receipt._invocation.owner is self._owner):
            with self._lock:
                current = self._invocations.pop(str(call.get("id")), None)
            if current is receipt._invocation:
                self.receipts.append(receipt)
                accepted = True
                if failure:
                    self.budget_terminal |= receipt.budget_terminal
                    self.remaining.append(f"Workflow {receipt.workflow_id} did not complete: {receipt.reason}.")
                if result.awaitUser:
                    self.remaining.append("The workflow requires user action before it can continue.")
        if accepted:
            return
        name = str(call.get("toolName", "tool"))
        reason = None
        if suppressed:
            reason = f"A requested {name} call was not executed."
        elif not result.ok or result.awaitUser:
            reason = f"A {name} call still requires follow-up."
        elif self.receipts and name != "todo_write" and not read_only:
            reason = f"Later {name} work is not covered by the committed workflow checks."
        if reason and reason not in self.remaining:
            self.remaining.append(reason)

    def is_budget_terminal(self, call, result) -> bool:
        receipt = getattr(result, "_workflow_delivery_receipt", None)
        with self._lock:
            return bool(not result.ok and isinstance(receipt, WorkflowFailureReceipt)
                        and receipt._seal is _SEAL and receipt.budget_terminal
                        and receipt._invocation.owner is self._owner
                        and self._invocations.get(str(call.get("id"))) is receipt._invocation)


def invoke_with_delivery(owner, call, operation):
    if owner is None:
        return operation()
    with owner.invocation(call):
        result = operation()
        invocation = _CURRENT_INVOCATION.get()
        if (invocation is not None and invocation.structured_workflow is not None
                and getattr(result, "_workflow_delivery_receipt", None) is None):
            # Finalization may fail after the model floor has been exchanged.
            # Deliver uncertainty, never reconstruct authority from output text.
            from minicode.tooling import ToolTerminationReason
            receipt = replace(_unknown_failure(invocation), budget_terminal=(
                result._termination_reason is ToolTerminationReason.BUDGET_EXCEEDED))
            result = replace(result, _workflow_delivery_receipt=receipt)
        return result
