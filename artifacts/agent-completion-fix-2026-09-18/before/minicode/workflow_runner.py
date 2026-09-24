"""Complete centrally scheduled multi-agent workflow execution.

The task tool supplies validated input and the existing child-agent runner.
This module privately owns isolation, phase state, scheduler ports, evidence,
and promotion.  A detached read-only module owns audit, trace, and truthful
terminal projection after execution authority is sealed.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import AgentTurnDeadlineExceeded
from minicode.central_scheduler import (
    CentralScheduler,
    DispatchPlan,
    DispatchStage,
    SchedulerTransition,
    StageResult,
    TaskProfile,
    WorkflowExecutionOutcome,
)
from minicode.conflict_port_runner import (
    BoundedConflictPortRunner,
    ConflictPortCancelled,
    ConflictPortRunner,
)
from minicode.deterministic_verifier import (
    DeterministicVerifier,
    VerificationCheck,
    VerificationDeadlineExceeded,
)
from minicode.multi_agent_contracts import (
    MAX_ANALYST_REPORT_BYTES,
    MAX_ROLE_CONTRACT_BYTES,
    Assignment,
    ImplementationOrder,
    WorkflowRequest,
    analyst_report_requires_reclassification,
    parse_assignment,
    parse_conflict_package,
    parse_executor_declaration,
    parse_implementation_order,
    parse_role_result,
    parse_verification_result,
    parse_workflow_request,
    repair_role_payload,
)
from minicode.scheduler_artifacts import (
    AssignmentArtifactChannel,
    SchedulerArtifactError,
    SchedulerArtifactStore,
)
from minicode.scheduler_audit import (
    human_approval_analysis_digest,
    project_scheduler_commit_ready,
)
from minicode.subagent_journal import (
    SubagentRunJournal,
    new_subagent_id,
)
from minicode.subagent_journal import (
    open_subagent_journal as _open_subagent_journal,
)
from minicode.subagent_observation import (
    emit_subagent_observation as _emit_subagent_observation,
)
from minicode.subagent_types import AGENT_TYPES, MAX_AGENT_DEPTH, resolve_max_turns
from minicode.task_deadline import (
    TaskDeadlineExceeded as _TaskDeadlineExceeded,
)
from minicode.task_deadline import (
    raise_task_deadline as _raise_task_deadline,
)
from minicode.task_deadline import (
    task_timeout_seconds as _task_timeout_seconds,
)
from minicode.tooling import (
    ToolExecutionAbandoned,
    ToolResult,
)
from minicode.turn_cancellation import TurnCancellationRequested
from minicode.workflow_finalization import (
    _WORKFLOW_OUTPUT_MAX_CHARS,
    _FinalizationDeadlineExceeded,
    _WorkflowFinalizationPorts,
    _WorkflowFinalizationSnapshot,
    _WorkflowObservation,
    finalize_workflow,
)
from minicode.workflow_scope_registry import (
    WorkflowScopeRegistration,
    WorkflowWriteScopeConflict,
    process_workflow_scope_registry,
)
from minicode.workflow_repair import (
    REPAIR_REQUEST_TYPE,
    implementation_repair_input_refs,
    parse_implementation_repair_request,
    review_supports_implementation_repair,
    verification_supports_implementation_repair,
)
from minicode.workflow_workspace import (
    FrozenWorkflowChange as _FrozenWorkflowChange,
)
from minicode.workflow_workspace import (
    WorkflowIsolationError as _WorkflowIsolationError,
)
from minicode.workflow_workspace import (
    WorkflowWorkspaceTransaction as _WorkflowWorkspaceTransaction,
)

_RunSubagent = Callable[[dict, object], ToolResult]
_FINALIZATION_AUDIT_PORT_RUNNER = BoundedConflictPortRunner(
    max_in_flight=2,
    default_timeout_seconds=5.0,
)
_FINALIZATION_OBSERVATION_PORT_RUNNER = BoundedConflictPortRunner(
    max_in_flight=4,
    default_timeout_seconds=1.0,
)


def _run_finalization_port(
    runner: ConflictPortRunner,
    operation: Callable[[], object],
    *,
    port_name: str,
    operation_id: str,
    context: object,
    committed: bool,
) -> object:
    """Apply a local recovery budget without sacrificing commit truth."""
    cancellation_token = (
        None if committed else getattr(context, "_cancellation_token", None)
    )
    try:
        return runner.run(
            operation,
            port_name=port_name,
            operation_id=operation_id,
            timeout_seconds=None,
            cancellation_token=cancellation_token,
        )
    except ConflictPortCancelled as exc:
        if exc.cause is not None:
            raise exc.cause from exc
        raise


@dataclass(slots=True)
class _WorkflowExecutionState:
    """The single mutable authority shared by Scheduler-owned phase ports."""

    acceptance_evidence_matrix: dict[str, object] | None
    acceptance_evidence_matrix_id: str | None
    agent_budget: AgentTurnBudget
    analyst_artifact_id: str | None
    analyst_evidence_ids: list[str]
    analyst_report: dict[str, object] | None
    approval_request_document: dict[str, object] | None
    approval_request_id: str | None
    artifact_store: SchedulerArtifactStore
    change_diff_artifact: dict[str, object] | None
    change_diff_artifact_id: str | None
    child_context: object
    commit_error: _WorkflowIsolationError | None
    commit_ready_status: str
    context: object
    description: str
    execute_result: ToolResult | None
    execution_artifact: dict[str, object] | None
    execution_artifact_id: str | None
    executor_assignment_id: str | None
    frozen_change_set: dict[str, object] | None
    frozen_workspace_change: _FrozenWorkflowChange | None
    human_approval_artifact_id: str | None
    human_approval_document: dict[str, object] | None
    implementation_order: ImplementationOrder | None
    locked_write_paths: tuple[str, ...]
    mailbox: object | None
    plan_result: ToolResult | None
    profile: TaskProfile
    requested_checks: tuple[VerificationCheck, ...]
    research_notes: str
    review_key: str
    review_result: ToolResult | None
    role_result_parse_errors: dict[str, str]
    run_subagent: _RunSubagent
    scheduler_artifact_authority: object
    task_id: str
    task_prompt: str
    transaction: _WorkflowWorkspaceTransaction | None
    transitions: list[SchedulerTransition]
    typed_review: dict[str, object]
    adjudication_verdict: dict[str, object] | None
    pending_conflict_artifact_id: str | None
    pending_conflict_package: dict[str, object] | None
    verification_artifact_id: str | None
    verification_evidence_ids: list[str]
    verification_result: dict[str, object] | None
    workflow_id: str
    workflow_request: WorkflowRequest
    workflow_request_artifact_id: str
    workflow_request_digest: str


def _deterministic_verifier_for_context(
    context: object,
    *,
    trusted_git_executable: str | None = None,
    untrusted_executable_root: str | None = None,
) -> DeterministicVerifier:
    """Bind only a host-owned sandbox capability, never WorkflowRequest data."""
    backend = getattr(context, "_verifier_sandbox_backend", None)
    if backend is None:
        runtime = getattr(context, "_runtime", None)
        if isinstance(runtime, dict):
            backend = runtime.get("_verifierSandboxBackend")
            verifier_settings = runtime.get("verifier")
            if backend is None and isinstance(verifier_settings, dict):
                backend = verifier_settings.get("_sandboxBackend")
    if backend is not None and not callable(getattr(backend, "wrap_command", None)):
        backend = None
    raw_trusted = getattr(context, "_verifier_trusted_executables", None)
    if raw_trusted is None:
        runtime = getattr(context, "_runtime", None)
        if isinstance(runtime, dict):
            raw_trusted = runtime.get("_verifierTrustedExecutables")
            verifier_settings = runtime.get("verifier")
            if raw_trusted is None and isinstance(verifier_settings, dict):
                raw_trusted = verifier_settings.get("_trustedExecutables")
    trusted_executables = dict(raw_trusted) if isinstance(raw_trusted, dict) else {}
    raw_explicit = getattr(context, "_verifier_explicit_trusted_executables", None)
    if not isinstance(raw_explicit, dict):
        # Host embedders inject the field through ToolContext; the CLI path
        # resolves the same user-owned env channel directly.
        from minicode.verifier_sandbox import user_registered_trusted_executables

        raw_explicit = user_registered_trusted_executables()
    explicit_trusted = {
        str(name): str(path)
        for name, path in raw_explicit.items()
        if isinstance(name, str) and isinstance(path, str)
    }
    if trusted_git_executable is not None:
        trusted_executables["git"] = trusted_git_executable
    return DeterministicVerifier(
        sandbox_backend=backend,
        trusted_executables=trusted_executables,
        explicit_trusted_executables=explicit_trusted,
        untrusted_executable_roots=(
            (untrusted_executable_root,)
            if untrusted_executable_root is not None
            else ()
        ),
    )


def _workflow_human_approval(
    context: object,
    request: WorkflowRequest,
    *,
    request_digest: str,
    base_digest: str,
    analyst_artifact_id: str,
    analyst_digest: str,
) -> tuple[str | None, dict[str, object], dict[str, object] | None]:
    """Consume only a host approval bound to request, baseline and analysis."""
    if re.fullmatch(r"sha256:[0-9a-f]{64}", request_digest) is None:
        raise ValueError("human approval request digest is invalid")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", analyst_digest) is None:
        raise ValueError("human approval analyst digest is invalid")
    identity = (
        f"{request.task_id}\0{request_digest}\0{base_digest}\0{analyst_digest}"
    ).encode("utf-8")
    approval_request_id = "approval_" + hashlib.sha256(identity).hexdigest()[:32]
    request_document = {
        "approvalVersion": 1,
        "resultType": "HumanApprovalRequest.v1",
        "approvalRequestId": approval_request_id,
        "taskId": request.task_id,
        "requestDigest": request_digest,
        "baseDigest": base_digest,
        "analystArtifactId": analyst_artifact_id,
        "analystDigest": analyst_digest,
        "risk": request.task_profile.risk.value,
    }
    provider = getattr(context, "_workflow_human_approval_provider", None)
    if provider is None:
        runtime = getattr(context, "_runtime", None)
        if isinstance(runtime, dict):
            provider = runtime.get("_workflowHumanApprovalProvider")
    consumer = getattr(provider, "consume_approval", None)
    if not callable(consumer):
        return approval_request_id, request_document, None
    _raise_task_deadline(context)
    try:
        response = consumer(
            approval_request_id=approval_request_id,
            task_id=request.task_id,
            request_digest=request_digest,
            base_digest=base_digest,
            analyst_artifact_id=analyst_artifact_id,
            analyst_digest=analyst_digest,
            deadline_monotonic=getattr(
                context,
                "_task_deadline_monotonic",
                None,
            ),
        )
    except (
        TurnCancellationRequested,
        ToolExecutionAbandoned,
        AgentTurnDeadlineExceeded,
        _TaskDeadlineExceeded,
    ):
        raise
    except Exception:  # noqa: BLE001 - invalid host capability fails closed
        _raise_task_deadline(context)
        return approval_request_id, request_document, None
    _raise_task_deadline(context)
    fields = {
        "approvalVersion",
        "approvalRequestId",
        "taskId",
        "requestDigest",
        "baseDigest",
        "analystDigest",
        "decision",
        "approvedBy",
        "expiresAt",
    }
    if not isinstance(response, dict) or set(response) != fields:
        return approval_request_id, request_document, None
    approved_by = response.get("approvedBy")
    expires_at = response.get("expiresAt")
    if (
        response.get("approvalVersion") != 1
        or response.get("approvalRequestId") != approval_request_id
        or response.get("taskId") != request.task_id
        or response.get("requestDigest") != request_digest
        or response.get("baseDigest") != base_digest
        or response.get("analystDigest") != analyst_digest
        or response.get("decision") != "approved"
        or not isinstance(approved_by, str)
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.@-]{1,127}", approved_by) is None
        or not isinstance(expires_at, str)
        or len(expires_at) > 80
    ):
        return approval_request_id, request_document, None
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return approval_request_id, request_document, None
    validated_at = datetime.now(timezone.utc)
    if expiry.tzinfo is None or expiry <= validated_at:
        return approval_request_id, request_document, None
    return (
        approval_request_id,
        request_document,
        {
            "approvalVersion": 1,
            "resultType": "HumanApproval.v1",
            "approvalId": approval_request_id,
            "taskId": request.task_id,
            "requestDigest": request_digest,
            "baseDigest": base_digest,
            "analystDigest": analyst_digest,
            "decision": "approved",
            "approvedBy": approved_by,
            "validatedAt": validated_at.isoformat().replace("+00:00", "Z"),
            "expiresAt": expires_at,
        },
    )


def _human_approval_is_current(document: object) -> bool:
    """Recheck the sealed approval clock at every privileged boundary."""
    if not isinstance(document, dict):
        return False
    expires_at = document.get("expiresAt")
    if not isinstance(expires_at, str):
        return False
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)


_WORKFLOW_EXPLORATION_FACETS = (
    (
        "architecture",
        "Map the modules, entry points, and ownership relevant to this task. "
        "Read the key files and explain the dependency flow.",
    ),
    (
        "tests",
        "Find the test layout, fixtures, and exact verification commands "
        "relevant to this task.",
    ),
    (
        "risks",
        "Find callers, configuration, migrations, and other places that "
        "could break when this task is implemented.",
    ),
)

_WORKFLOW_REVIEW_VERSION = 1
_WORKFLOW_REVIEW_VERDICTS = frozenset({"approved", "changes_required", "inconclusive"})
_WORKFLOW_REVIEW_MAX_ITEMS = 6
_WORKFLOW_REVIEW_MAX_ITEM_CHARS = 200
_WORKFLOW_REVIEW_MAX_JSON_CHARS = 3000
_WORKFLOW_MAX_PHASES = AGENT_TYPES["workflow"]["max_phases"]


def _normalize_workflow_review(
    content: object,
) -> tuple[dict[str, object] | None, str | None]:
    """Validate the reviewer's mailbox verdict as bounded authority.

    Reviewer prose is presentation only. The parent workflow accepts a result
    exclusively from this exact versioned JSON shape so a normally completed
    reviewer that reports blockers cannot be mistaken for approval.
    """
    if not isinstance(content, str) or not content.strip():
        return None, "review_verdict_missing"
    if len(content) > _WORKFLOW_REVIEW_MAX_JSON_CHARS:
        return None, "review_verdict_oversized"
    try:
        raw = json.loads(content)
    except (TypeError, ValueError):
        return None, "review_verdict_invalid_json"
    expected_fields = {
        "reviewVersion",
        "verdict",
        "blockingFindings",
        "warnings",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        return None, "review_verdict_invalid_shape"
    verdict = raw.get("verdict")
    blocking = raw.get("blockingFindings")
    warnings = raw.get("warnings")
    if (
        raw.get("reviewVersion") != _WORKFLOW_REVIEW_VERSION
        or verdict not in _WORKFLOW_REVIEW_VERDICTS
        or not isinstance(blocking, list)
        or not isinstance(warnings, list)
        or len(blocking) > _WORKFLOW_REVIEW_MAX_ITEMS
        or len(warnings) > _WORKFLOW_REVIEW_MAX_ITEMS
        or any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > _WORKFLOW_REVIEW_MAX_ITEM_CHARS
            for item in [*blocking, *warnings]
        )
        or (verdict == "approved" and bool(blocking))
        or (verdict == "changes_required" and not blocking)
    ):
        return None, "review_verdict_invalid_shape"
    return {
        "reviewVersion": _WORKFLOW_REVIEW_VERSION,
        "verdict": verdict,
        "blockingFindings": list(blocking),
        "warnings": list(warnings),
    }, None


def _read_workflow_review(
    mailbox: object, key: str
) -> tuple[dict[str, object] | None, str | None]:
    if mailbox is None:
        return None, "review_verdict_mailbox_unavailable"
    reader = getattr(mailbox, "read", None)
    if not callable(reader):
        return None, "review_verdict_mailbox_unavailable"
    try:
        note = reader(key)
    except Exception:  # noqa: BLE001 - malformed authority fails closed
        return None, "review_verdict_unavailable"
    if note is None:
        return None, "review_verdict_missing"
    return _normalize_workflow_review(getattr(note, "content", None))


def _workflow_failure_review(reason: str) -> dict[str, object]:
    return {
        "reviewVersion": _WORKFLOW_REVIEW_VERSION,
        "verdict": "inconclusive",
        "blockingFindings": [f"error[{reason}]: workflow review was not authoritative"],
        "warnings": [],
    }


def _workflow_review_not_applicable() -> dict[str, object]:
    return {
        "reviewVersion": _WORKFLOW_REVIEW_VERSION,
        "verdict": "not_applicable",
        "blockingFindings": [],
        "warnings": [],
    }


def _build_worker_assignment(
    request: WorkflowRequest,
    stage_assignment: object,
    *,
    context: object,
    agent_budget: AgentTurnBudget,
    role: str,
    mode: str,
    objective: str,
    input_refs: tuple[str, ...] = (),
    write_paths: tuple[str, ...] | None = None,
) -> Assignment:
    deadline_monotonic = getattr(context, "_task_deadline_monotonic", None)
    if isinstance(deadline_monotonic, (int, float)):
        remaining_seconds = max(0.0, float(deadline_monotonic) - time.monotonic())
    else:
        timeout_seconds = getattr(context, "_task_timeout_seconds", None)
        if not isinstance(timeout_seconds, (int, float)):
            timeout_seconds = _task_timeout_seconds(context)
        remaining_seconds = float(timeout_seconds)
    deadline = (
        (datetime.now(timezone.utc) + timedelta(seconds=remaining_seconds))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    budget_snapshot = agent_budget.snapshot()
    # Declared per-role model-call budget, resolved through the same
    # host-overridable knob the phase dispatch applies so the Assignment
    # never promises a limit the worker does not actually get.
    role_model_limit = {
        "analyst": resolve_max_turns("plan"),
        "executor": resolve_max_turns("general"),
        "reviewer": resolve_max_turns("plan"),
        "adjudicator": resolve_max_turns("plan"),
    }[role]
    remaining_model_calls = role_model_limit
    if budget_snapshot.limit_model_calls is not None:
        remaining_model_calls = min(
            remaining_model_calls,
            max(
                0,
                budget_snapshot.limit_model_calls - budget_snapshot.used_model_calls,
            ),
        )
    remaining_tokens = 0
    if budget_snapshot.limit_total_tokens is not None:
        remaining_tokens = max(
            0,
            budget_snapshot.limit_total_tokens
            - budget_snapshot.used_total_tokens
            - budget_snapshot.reserved_total_tokens,
        )
    expected_result_type = {
        "analyst": "AnalystReport.v1",
        "executor": "ExecutionResult.v1",
        "reviewer": "ReviewVerdict.v1",
        "adjudicator": "AdjudicationVerdict.v1",
    }[role]
    raw = {
        "assignmentVersion": 1,
        "workflowId": getattr(stage_assignment, "workflow_id"),
        "taskId": request.task_id,
        "assignmentId": getattr(stage_assignment, "assignment_id"),
        "role": role,
        "mode": mode,
        "objective": objective,
        "scope": {
            "readRoots": list(request.scope.read_roots),
            "writePaths": list(
                request.scope.write_paths if write_paths is None else write_paths
            ),
            "forbiddenPaths": list(request.scope.forbidden_paths),
        },
        "constraints": [
            {
                "id": "c_scheduler_scope",
                "kind": "scope",
                "text": "Do not expand the scheduler-owned objective or paths.",
            }
        ],
        "acceptanceCriteria": [item.to_dict() for item in request.criteria],
        "inputRefs": list(input_refs),
        "risk": request.task_profile.risk.value,
        "budget": {
            "deadlineAt": deadline,
            "maxModelCalls": remaining_model_calls,
            # Both are truthful upper bounds inherited from the one enforced
            # shared total-token budget; they are not independent grants.
            "maxInputTokens": remaining_tokens,
            "maxOutputTokens": remaining_tokens,
        },
        "expectedResultType": expected_result_type,
    }
    return parse_assignment(raw)


def _assignment_result_key(stage_assignment: object, role_code: str) -> str:
    """Return a scheduler key accepted by both Store and note tool contracts."""
    if (
        not isinstance(role_code, str)
        or re.fullmatch(r"[a-z]{2,12}", role_code) is None
    ):
        raise ValueError("role result key code is invalid")
    identity = (
        f"{getattr(stage_assignment, 'assignment_id')}\0"
        f"{getattr(stage_assignment, 'attempt_id')}\0{role_code}"
    )
    readable = identity.replace("\0", "_")
    candidate = f"artifact_{readable}"
    if len(candidate) <= 80:
        return candidate
    return (
        "artifact_"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:56]
        + f"_{role_code}"
    )


def _stage_receipt_artifact_id(stage_assignment: object) -> str:
    """Unique fallback artifact for one exact Scheduler stage attempt."""
    return _assignment_result_key(stage_assignment, "receipt")


def _assignment_artifact_id(stage_assignment: object) -> str:
    """Stable audit key for the exact per-attempt Assignment envelope."""
    identity = (
        f"{getattr(stage_assignment, 'assignment_id')}\0"
        f"{getattr(stage_assignment, 'attempt_id')}"
    )
    return (
        "artifact_assignment_"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    )


def _assignment_artifact_document(
    stage_assignment: object,
    assignment: Assignment,
) -> dict[str, object]:
    return {
        "assignmentArtifactVersion": 1,
        "resultType": "AssignmentArtifact.v1",
        "attemptId": getattr(stage_assignment, "attempt_id"),
        "agentRunId": getattr(stage_assignment, "agent_run_id"),
        "stage": getattr(stage_assignment, "stage").value,
        "ruleId": getattr(stage_assignment, "rule_id"),
        "assignment": assignment.to_dict(),
    }


def _freeze_analyst_evidence(
    report: dict[str, object],
    *,
    workspace_root: Path,
    base_digest: str,
    stage_assignment: object,
    operation_checkpoint: object | None,
) -> list[tuple[str, dict[str, object]]]:
    """Bind Analyst line claims to exact regular-file bytes in the snapshot."""
    canonical_root = workspace_root.resolve(strict=True)
    frozen: list[tuple[str, dict[str, object]]] = []
    findings = report.get("findings", [])
    if not isinstance(findings, list):
        raise _WorkflowIsolationError("Analyst findings are invalid")
    for finding in findings:
        if not isinstance(finding, dict):
            raise _WorkflowIsolationError("Analyst finding is invalid")
        claim_id = str(finding.get("claimId", ""))
        evidence_items = finding.get("evidence", [])
        if not isinstance(evidence_items, list):
            raise _WorkflowIsolationError("Analyst evidence is invalid")
        for evidence in evidence_items:
            if callable(operation_checkpoint):
                operation_checkpoint()
            if not isinstance(evidence, dict):
                raise _WorkflowIsolationError("Analyst evidence is invalid")
            relative = str(evidence.get("path", ""))
            line_start = evidence.get("lineStart")
            line_end = evidence.get("lineEnd")
            candidate = canonical_root / relative
            try:
                lexical = candidate.relative_to(canonical_root)
            except ValueError as exc:
                raise _WorkflowIsolationError(
                    "Analyst evidence escaped the snapshot"
                ) from exc
            cursor = canonical_root
            for part in lexical.parts:
                cursor /= part
                if cursor.is_symlink():
                    raise _WorkflowIsolationError(
                        "Analyst evidence cannot traverse a symlink"
                    )
            try:
                info = candidate.stat()
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise _WorkflowIsolationError(
                    "Analyst evidence file is unavailable"
                ) from exc
            if (
                not resolved.is_relative_to(canonical_root)
                or not stat.S_ISREG(info.st_mode)
                or info.st_size > 10 * 1024 * 1024
                or not isinstance(line_start, int)
                or isinstance(line_start, bool)
                or not isinstance(line_end, int)
                or isinstance(line_end, bool)
            ):
                raise _WorkflowIsolationError("Analyst evidence file is invalid")
            file_digest = hashlib.sha256()
            content_digest = hashlib.sha256()
            line_count = 0
            with resolved.open("rb") as handle:
                for line_count, line in enumerate(handle, start=1):
                    if line_count % 256 == 0 and callable(operation_checkpoint):
                        operation_checkpoint()
                    file_digest.update(line)
                    if line_start <= line_count <= line_end:
                        content_digest.update(line)
            if line_end > line_count:
                raise _WorkflowIsolationError(
                    "Analyst evidence line range exceeds the snapshot file: "
                    f"{relative} has {line_count} lines but claim "
                    f"{claim_id or '<unknown>'} cites lineEnd {line_end}"
                )
            identity = (
                f"{getattr(stage_assignment, 'assignment_id')}\0"
                f"{getattr(stage_assignment, 'attempt_id')}\0{claim_id}\0"
                f"{relative}\0{line_start}\0{line_end}"
            )
            artifact_id = (
                "artifact_evidence_"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            )
            frozen.append(
                (
                    artifact_id,
                    {
                        "evidenceSnapshotVersion": 1,
                        "resultType": "EvidenceSnapshot.v1",
                        "assignmentId": getattr(
                            stage_assignment,
                            "assignment_id",
                        ),
                        "attemptId": getattr(stage_assignment, "attempt_id"),
                        "claimId": claim_id,
                        "baseDigest": base_digest,
                        "path": relative,
                        "lineStart": line_start,
                        "lineEnd": line_end,
                        "fileDigest": "sha256:" + file_digest.hexdigest(),
                        "contentDigest": "sha256:" + content_digest.hexdigest(),
                    },
                )
            )
    return frozen


# Independent verification checks each clone the frozen tree; more than
# three concurrent clones turns the disk into the bottleneck.
_PARALLEL_VERIFICATION_WORKERS = 3
_RISK_CONFLICT_STOP_CHOICE = "stop_for_reclassification"
_RISK_CONFLICT_CONTINUE_CHOICE = "proceed_under_locked_classification"


def _freeze_analyst_risk_conflict(
    request: WorkflowRequest,
    stage_assignment: object,
    analyst_assignment: Assignment,
    report: Mapping[str, object],
    *,
    analyst_evidence_ids: Sequence[str],
    workflow_request_artifact_id: str,
) -> tuple[dict[str, object], str]:
    """Freeze one scheduler-owned risk-reclassification conflict.

    The Analyst validated risks above the locked Assignment risk.  Instead
    of silently discarding either authority, the escalation becomes a frozen
    ConflictPackage: claim A carries the Analyst's exceeding findings with
    their frozen evidence snapshots, claim B carries the locked profile's
    grounds (declared write scope, forbidden paths, bound verification).
    Claims order the continuation second so the scheduler's ``select_b``
    verdict is the only resumption path.
    """

    assigned_rank = {
        "low": 0,
        "medium": 1,
        "high": 2,
        "critical": 3,
    }[analyst_assignment.risk.value]
    exceeding: list[Mapping[str, object]] = [
        risk
        for risk in (
            item
            if isinstance(item, Mapping)
            else None
            for item in (report.get("risks") or [])
        )
        if risk is not None
        and {
            "low": 0,
            "medium": 1,
            "high": 2,
            "critical": 3,
        }.get(str(risk.get("severity")), -1)
        > assigned_rank
    ]
    if not exceeding:
        raise ValueError("no exceeding Analyst risk to freeze")
    conflict_identity = (
        f"{analyst_assignment.workflow_id}\0"
        f"{getattr(stage_assignment, 'assignment_id')}"
    )
    conflict_seed = hashlib.sha256(conflict_identity.encode("utf-8")).hexdigest()
    analyst_statement = " ".join(
        str(risk.get("statement") or "").strip() for risk in exceeding[:4]
    )[:1200]
    claim_a_evidence = (
        list(analyst_evidence_ids[:8])
        if analyst_evidence_ids
        else [workflow_request_artifact_id]
    )
    claim_b_statement = (
        "The locked Assignment classification stands: the request declares "
        f"kind={request.task_profile.kind.value}, risk={analyst_assignment.risk.value}, "
        f"{len(request.scope.write_paths)} bounded write path(s), forbidden "
        f"paths enforced, {len(request.criteria)} acceptance criteria, and "
        f"{len(request.verification_checks)} bound deterministic verification "
        "check(s); every promoted byte must still pass the Verifier gate."
    )[:1200]
    package: dict[str, object] = {
        "conflictVersion": 1,
        "conflictId": f"conflict_{conflict_seed[:16]}",
        "workflowId": analyst_assignment.workflow_id,
        "taskId": analyst_assignment.task_id,
        "kind": "risk",
        "risk": str(exceeding[0].get("severity")),
        "question": {
            "questionId": f"question_{conflict_seed[:16]}",
            "prompt": (
                "Validated Analyst findings exceed the locked Assignment "
                "risk. Should this workflow proceed under the locked "
                "classification, or stop for reclassification?"
            ),
            "choices": [
                _RISK_CONFLICT_STOP_CHOICE,
                _RISK_CONFLICT_CONTINUE_CHOICE,
            ],
        },
        "claims": [
            {
                "claimId": "claim_analyst_risk",
                "choice": _RISK_CONFLICT_STOP_CHOICE,
                "statement": analyst_statement
                or "Analyst findings exceed the locked risk.",
                "evidenceRefs": claim_a_evidence,
            },
            {
                "claimId": "claim_locked_profile",
                "choice": _RISK_CONFLICT_CONTINUE_CHOICE,
                "statement": claim_b_statement,
                "evidenceRefs": [workflow_request_artifact_id],
            },
        ],
        "evidenceDigest": "sha256:"
        + hashlib.sha256(
            json.dumps(
                {
                    "conflictId": f"conflict_{conflict_seed[:16]}",
                    "analystEvidence": claim_a_evidence,
                    "lockedEvidence": [workflow_request_artifact_id],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        "voteEligible": False,
    }
    validated = parse_conflict_package(
        package,
        workflow_id=analyst_assignment.workflow_id,
        task_id=analyst_assignment.task_id,
    )
    conflict_artifact_id = (
        "artifact_conflict_"
        + hashlib.sha256(
            conflict_identity.encode("utf-8")
        ).hexdigest()[:32]
    )
    return validated, conflict_artifact_id


def _analyst_findings_notes(
    report: Mapping[str, object] | None,
    *,
    maximum_chars: int = 4_000,
) -> str:
    """Bound the Analyst's frozen findings into executor prompt context.

    The findings are already contract-validated and byte-frozen; restating
    them (statement plus evidence path/lines) lets the Executor start from
    the surveyed facts instead of re-reading each cited file cold.
    """

    if not isinstance(report, Mapping):
        return ""
    findings = report.get("findings")
    if not isinstance(findings, list) or not findings:
        return ""
    lines: list[str] = []
    used = 0
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        statement = str(finding.get("statement") or "").strip()
        if not statement:
            continue
        evidence = finding.get("evidence")
        citations = []
        if isinstance(evidence, list):
            for item in evidence[:2]:
                if isinstance(item, Mapping):
                    citations.append(
                        f"{item.get('path')}:{item.get('lineStart')}-"
                        f"{item.get('lineEnd')}"
                    )
        line = "- " + statement
        if citations:
            line += " (" + ", ".join(citations) + ")"
        if used + len(line) > maximum_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return (
        "ANALYST FINDINGS (frozen evidence; use these instead of "
        "re-reading the cited files for context):\n" + "\n".join(lines)
    )


def _role_result_instruction(
    assignment: Assignment,
    *,
    result_key: str,
    implementation_order: ImplementationOrder | None = None,
    previous_error: str | None = None,
) -> str:
    if assignment.role.value == "analyst":
        read_root = assignment.scope.read_roots[0]
        example_evidence_path = (
            assignment.scope.write_paths[0]
            if assignment.scope.write_paths
            else (
                "README.md"
                if read_root == "."
                else read_root.rstrip("/") + "/README.md"
            )
        )
        candidate = (
            None
            if assignment.mode == "assess_claim"
            else {
                "required": assignment.mode == "prepare_change",
                "steps": (
                    [
                        {
                            "id": "s1",
                            "action": "bounded implementation step",
                            "allowedPaths": list(assignment.scope.write_paths[:1]),
                        }
                    ]
                    if assignment.mode == "prepare_change"
                    else []
                ),
                "verificationRequests": [],
            }
        )
        template = {
            "resultVersion": 1,
            "assignmentId": assignment.assignment_id,
            "status": "completed",
            "findings": [
                {
                    "claimId": "claim_1",
                    "statement": "evidence-backed finding",
                    "evidence": [
                        {
                            "path": example_evidence_path,
                            "lineStart": 1,
                            "lineEnd": 1,
                        }
                    ],
                    "confidence": "medium",
                }
            ],
            "unknowns": [],
            "risks": [],
            "ballot": None,
        }
        if candidate is not None:
            template["implementationOrderCandidate"] = candidate
    elif assignment.role.value == "executor":
        if implementation_order is None:
            raise ValueError("Executor result instruction requires its locked order")
        template = {
            "resultVersion": 1,
            "assignmentId": assignment.assignment_id,
            "orderId": implementation_order.order_id,
            "status": "completed|blocked|failed",
            "stepResults": [
                {
                    "stepId": step.step_id,
                    "status": "completed|skipped|failed",
                    "artifactRefs": [],
                }
                for step in implementation_order.steps
            ],
            "deviations": [],
            "selfChecks": [],
            "blockers": [],
        }
    elif assignment.role.value == "reviewer":
        template = {
            "resultVersion": 1,
            "assignmentId": assignment.assignment_id,
            "patchDigest": "sha256:<64 lowercase hex characters>",
            "verdict": "approved|changes_required|inconclusive",
            "criteria": [
                {
                    "criterionId": item.criterion_id,
                    "status": "satisfied|failed|unknown",
                    "evidenceRefs": ["artifact_reference"],
                }
                for item in assignment.acceptance_criteria
            ],
            "blockingFindings": [],
            "warnings": [],
            "requestedChecks": [],
            "confidence": "high|medium|low",
        }
    elif assignment.role.value == "adjudicator":
        template = {
            "resultVersion": 1,
            "assignmentId": assignment.assignment_id,
            "conflictId": "conflict_<id from the ConflictPackage>",
            "decision": "select_a|select_b|synthesize|insufficient|human_required",
            "resolvedClaims": [
                {
                    "claimId": "claim_<one claim from the ConflictPackage>",
                    "resolution": "why this claim was accepted or rejected",
                    "evidenceRefs": ["<ConflictPackage claim evidence refs only>"],
                }
            ],
            "conditions": [],
            "confidence": "high|medium|low",
        }
    else:
        template = {
            "resultVersion": 1,
            "assignmentId": assignment.assignment_id,
        }
    contract_byte_limit = (
        MAX_ANALYST_REPORT_BYTES
        if assignment.role.value == "analyst"
        else MAX_ROLE_CONTRACT_BYTES
    )
    instruction = (
        "The Scheduler accepts only a strict role result. Before your final "
        "answer, call subagent_note_write exactly once with key "
        f"`{result_key}` and content containing ONLY JSON shaped like this "
        "template (replace placeholders with real evidence):\n"
        + json.dumps(template, ensure_ascii=False, sort_keys=True)
        + "\nUnknown fields, prose around the JSON, or a mismatched assignmentId "
        f"will fail the workflow closed. Canonical UTF-8 JSON must be at most "
        f"{contract_byte_limit} "
        "bytes. Executor results must not include a "
        "changeSet; the Scheduler derives it from frozen workspace bytes."
    )
    if assignment.role.value == "analyst":
        instruction += (
            "\nOptional list element shapes: each risks entry is an object "
            '{"severity": "low|medium|high|critical", "statement": "...", '
            '"evidenceRefs": ["claim_1"]}; unknowns and verificationRequests '
            "entries are plain strings."
            "\nEvidence line ranges must stay within the cited file's actual "
            "line count; the Scheduler freezes each citation against exact "
            "file bytes and rejects any lineEnd beyond the end of the file."
        )
    if assignment.role.value == "executor":
        instruction += (
            "\nselfChecks must stay an empty array and every stepResults "
            "entry must keep artifactRefs as an empty array: the Verifier "
            "owns command evidence and the Scheduler owns artifacts. Report "
            "honest per-step status in stepResults and any deviation in "
            "deviations instead."
            "\nStatus rule: \"completed\" requires every step completed, no "
            "blockers, and no high-impact deviation. If a step itself cannot "
            "be completed — e.g. a file in allowedPaths does not exist or an "
            "edit could not be applied — status must be \"blocked\" with a "
            "blocker naming the obstacle; never report \"completed\" with "
            "blockers. Running verification commands is the Verifier's job, "
            "not yours: when no command-execution tool is available, leave "
            "verification to the Verifier and note it at most as a "
            "low-impact deviation."
            "\nOptional list element shapes: each deviations entry is an "
            'object {"stepId": "s1", "reason": "...", "impact": '
            '"low|medium|high"}; blockers entries are plain strings.'
        )
    if assignment.role.value == "adjudicator":
        instruction += (
            "\ndecision selects claims by ConflictPackage order: select_a is "
            "the FIRST claim, select_b the SECOND. A decisive decision "
            "(select_a/select_b/synthesize) must resolve claims in "
            "resolvedClaims, and every resolution's evidenceRefs may cite "
            "only evidence refs already present in the ConflictPackage "
            "claims. decisions insufficient and human_required fail the "
            "workflow closed rather than guessing."
        )
    if previous_error is not None:
        instruction += (
            "\nYour previous submission for this assignment failed "
            f"validation: {previous_error}. Correct the JSON and resubmit "
            "the complete document."
        )
    return instruction


def _read_role_result_payload(channel: object, key: str) -> object:
    reader = getattr(channel, "read", None)
    if not callable(reader):
        raise ValueError("role result channel is unavailable")
    note = reader(key)
    if note is None:
        raise ValueError("role result is missing")
    try:
        return json.loads(note.content)
    except (TypeError, ValueError) as exc:
        raise ValueError("role result is not valid JSON") from exc


_ROLE_RESULT_ERROR_FEEDBACK_LIMIT = 400


def _role_result_error_feedback(error: BaseException) -> str:
    """Bounded single-line validation feedback for the next attempt prompt."""
    text = " ".join(f"{type(error).__name__}: {error}".split())
    if len(text) > _ROLE_RESULT_ERROR_FEEDBACK_LIMIT:
        text = text[: _ROLE_RESULT_ERROR_FEEDBACK_LIMIT - 1].rstrip() + "…"
    return text


def _read_role_result_payload_with_repair(channel: object, key: str) -> tuple[object, bool]:
    """Read one role payload, applying bounded format repair before failing.

    Returns ``(payload, repaired)``.  Repair only strips format noise; the
    unchanged strict ``parse_role_result`` validation still runs afterwards.
    """
    reader = getattr(channel, "read", None)
    if not callable(reader):
        raise ValueError("role result channel is unavailable")
    note = reader(key)
    if note is None:
        raise ValueError("role result is missing")
    content = getattr(note, "content", None)
    try:
        return json.loads(content), False
    except (TypeError, ValueError):
        pass
    repaired = repair_role_payload(content)
    if repaired is not None:
        try:
            return json.loads(repaired), True
        except (TypeError, ValueError):
            pass
    raise ValueError("role result is not valid JSON")


def _repair_receipt_artifact_id(stage_assignment: object, role_code: str) -> str:
    """Unique audit artifact recording one repaired role payload attempt."""
    identity = (
        f"{getattr(stage_assignment, 'assignment_id')}\0"
        f"{getattr(stage_assignment, 'attempt_id')}\0{role_code}\0repair"
    )
    return (
        "artifact_repair_"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    )


def _publish_role_result_repair_receipt(
    state: _WorkflowExecutionState,
    stage_assignment: object,
    role_code: str,
) -> None:
    """Record that format repair was applied; audit stays able to tell a
    first-pass-clean payload apart from a repaired one."""
    try:
        state.artifact_store.publish(
            _repair_receipt_artifact_id(stage_assignment, role_code),
            {
                "repairReceiptVersion": 1,
                "resultType": "RoleResultRepairReceipt.v1",
                "assignmentId": getattr(stage_assignment, "assignment_id"),
                "attemptId": getattr(stage_assignment, "attempt_id"),
                "agentRunId": getattr(stage_assignment, "agent_run_id"),
                "stage": getattr(stage_assignment, "stage").value,
                "role": role_code,
                "repaired": True,
            },
            authority=state.scheduler_artifact_authority,
        )
    except Exception:  # noqa: BLE001 - repair telemetry must not fail the stage
        pass


class _RepairingRoleResultChannel:
    """Write boundary that repairs format noise before the Store's JSON gate.

    Only format noise is repaired (markdown fences, surrounding prose,
    invisible characters); Store canonicalization and the unchanged strict
    ``parse_role_result`` contract still decide acceptance, so semantic
    failures stay fail-closed.  Whether a repair was applied is exposed so
    the Scheduler can publish an audit receipt distinguishing a
    first-pass-clean payload from a repaired one.
    """

    def __init__(self, inner: AssignmentArtifactChannel) -> None:
        self._inner = inner
        self.repaired = False

    def write(self, key: str, content: str, *, author: str):
        try:
            return self._inner.write(key, content, author=author)
        except SchedulerArtifactError as error:
            if "not valid JSON" not in str(error):
                raise
            repaired = repair_role_payload(content)
            if repaired is None:
                raise
            written = self._inner.write(key, repaired, author=author)
            self.repaired = True
            return written

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def _workflow_result_counts(results: list[ToolResult]) -> tuple[int, int]:
    model_turns = 0
    tool_calls = 0
    for result in results:
        match = re.search(
            r"Model turns: (\d+) \(tool calls: (\d+)\)",
            result.output,
        )
        if match is not None:
            model_turns += int(match.group(1))
            tool_calls += int(match.group(2))
    return model_turns, tool_calls


def _workflow_model_turn_limit(agent_budget: object) -> int | None:
    snapshot = getattr(agent_budget, "snapshot", None)
    if not callable(snapshot):
        return None
    try:
        current = snapshot()
    except Exception:
        return None
    limit = getattr(current, "limit_model_calls", None)
    used = getattr(current, "used_model_calls", None)
    if (
        isinstance(limit, int)
        and not isinstance(limit, bool)
        and isinstance(used, int)
        and not isinstance(used, bool)
    ):
        return max(0, limit - used)
    return None


def _emit_workflow_observation(
    context,
    *,
    subagent_id: str,
    result_contract_status: str,
    journal: SubagentRunJournal | None,
    outcome: str,
    results: list[ToolResult],
    phase_count: int,
    started: float,
    model_turn_limit: int | None,
    result_truncated: bool,
) -> None:
    model_turns, tool_calls = _workflow_result_counts(results)
    duration_ms = max(0, int((time.time() - started) * 1000))
    _record_workflow_observation(
        context,
        subagent_id=subagent_id,
        journal=journal,
        model_turn_limit=model_turn_limit,
        observation=_WorkflowObservation(
            result_contract_status=result_contract_status,
            outcome=outcome,
            phase_count=phase_count,
            result_truncated=result_truncated,
            duration_ms=duration_ms,
            model_turns=model_turns,
            tool_calls=tool_calls,
        ),
    )


def _record_workflow_observation(
    context: object,
    *,
    subagent_id: str,
    journal: SubagentRunJournal | None,
    model_turn_limit: int | None,
    observation: _WorkflowObservation,
) -> None:
    _emit_subagent_observation(
        context,
        subagent_id=subagent_id,
        result_contract_status=observation.result_contract_status,
        agent_type="workflow",
        outcome=observation.outcome,
        model_turns=observation.model_turns,
        tool_calls=observation.tool_calls,
        duration_ms=observation.duration_ms,
        model_turn_limit=model_turn_limit,
        phase_count=observation.phase_count,
        max_phases=_WORKFLOW_MAX_PHASES,
        result_truncated=observation.result_truncated,
    )
    if journal is not None:
        budget = getattr(context, "_agent_budget", None)
        snapshot = getattr(budget, "snapshot", None)
        attributed_tokens = getattr(budget, "attributed_tokens", None)
        tokens = attributed_tokens() if callable(attributed_tokens) else None
        try:
            journal.finish(
                subagent_id,
                outcome=(
                    observation.outcome
                    if observation.outcome in {"completed", "failed", "budget_exceeded"}
                    else "failed"
                ),
                model_turns=observation.model_turns,
                tool_calls=observation.tool_calls,
                duration_ms=observation.duration_ms,
                max_turns=_WORKFLOW_MAX_PHASES,
                result_truncated=observation.result_truncated,
                budget=snapshot() if callable(snapshot) else None,
                input_tokens=tokens[0] if tokens is not None else None,
                output_tokens=tokens[1] if tokens is not None else None,
            )
        except Exception:  # noqa: BLE001 - sidecar observation is optional
            pass


class _WorkflowCommitStateUnknown(BaseException):
    """Promotion was interrupted and the parent is neither base nor target."""


def _inline_explore_notes(
    explore_results: list[ToolResult],
    *,
    per_facet_chars: int = 3_000,
) -> str:
    """Bound parallel-explore outputs into inline Analyst prompt context."""

    sections: list[str] = []
    for result in explore_results:
        text = str(result.output or "").strip()
        if not text:
            continue
        if len(text) > per_facet_chars:
            text = text[:per_facet_chars] + "\n…(truncated)"
        sections.append(text)
    if not sections:
        return ""
    return (
        "\n\nPRE-SURVEY (parallel read-only exploration; treat as leads to "
        "verify, not frozen evidence):\n" + "\n---\n".join(sections)
    )


def _run_parallel_exploration(
    input_data: dict,
    context,
    child_context,
    mailbox,
    *,
    run_subagent: _RunSubagent,
) -> list[ToolResult]:
    """Run three read-only explore agents concurrently before planning.

    Each facet writes its findings to a dedicated mailbox key. Explore is
    concurrency-safe, has no permission prompt, and shares the parent Turn
    budget and mailbox, so this is the one place where workflow phases are
    intentionally parallel.
    """
    task_prompt = input_data["prompt"]
    description = input_data["description"]

    def explore_facet(key: str, angle: str) -> tuple[str, ToolResult]:
        note_key = f"workflow_explore_{key}"
        result = run_subagent(
            {
                "description": f"explore:{description}:{key}",
                "agent_type": "explore",
                "prompt": (
                    f"{angle}\n\nTASK CONTEXT:\n{task_prompt}\n\n"
                    "Do not modify files. Write your findings to the shared "
                    f"mailbox with subagent_note_write using key {note_key}. "
                    "End with <final> and a concise summary."
                ),
            },
            child_context,
        )
        return key, result

    results_by_key: dict[str, ToolResult] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(_WORKFLOW_EXPLORATION_FACETS),
        thread_name_prefix="mc-workflow-explore",
    ) as pool:
        futures = [
            pool.submit(explore_facet, key, angle)
            for key, angle in _WORKFLOW_EXPLORATION_FACETS
        ]
        for future in concurrent.futures.as_completed(futures):
            key, result = future.result()
            results_by_key[key] = result
            if mailbox is not None:
                try:
                    mailbox.write(
                        f"workflow_explore_{key}",
                        (
                            result.output
                            if result.ok
                            else f"exploration failed: {result.output[:1000]}"
                        ),
                        author="workflow",
                    )
                except Exception:  # noqa: BLE001 - notes are optional
                    pass
    return [results_by_key[key] for key, _ in _WORKFLOW_EXPLORATION_FACETS]


def _workflow_declared_write_paths(
    input_data: dict,
    *,
    subagent_id: str,
) -> tuple[str, ...]:
    """Best-effort declared write scope for admission and baseline scoping.

    The phase runner re-parses the request and owns the typed contract
    failure; an unparseable request can never reach the Executor stage, so
    admission treats it as an empty scope and lets that downstream error
    stand.  The legacy fallback declares no write paths and never conflicts.
    """
    try:
        request = parse_workflow_request(
            input_data.get("workflow_request"),
            legacy_task_id="task_" + subagent_id.removeprefix("sub_"),
        )
    except Exception:  # noqa: BLE001 - the phase runner owns the typed error
        return ()
    if not request.structured:
        return ()
    return request.scope.write_paths


def run_workflow(
    input_data: dict,
    context,
    *,
    depth: int,
    subagent_id: str,
    run_subagent: _RunSubagent,
) -> ToolResult:
    """Run a workflow against a disposable workspace transaction.

    Admission first claims the declared write scope in the process-local
    registry so overlapping concurrent workflows are rejected before any
    stage burns model budget; the claim is released on every terminal path.
    """
    agent_budget = getattr(context, "_agent_budget", None)
    if agent_budget is None:
        runtime = getattr(context, "_runtime", None)
        agent_budget = AgentTurnBudget.from_runtime(
            runtime if isinstance(runtime, dict) else {}
        )
    # The workflow row is the single journal entry for the whole scheduled
    # chain, so its attribution view must see every phase's model calls:
    # phases nest their own views inside this one through the shared context.
    attribution_view = getattr(agent_budget, "attribution_view", None)
    if callable(attribution_view):
        agent_budget = attribution_view(subagent_id)
    context = replace(context, _agent_budget=agent_budget)
    workflow_journal = _open_subagent_journal(context)
    if workflow_journal is not None:
        try:
            workflow_journal.start(
                subagent_id=subagent_id,
                agent_type="workflow",
                max_turns=_WORKFLOW_MAX_PHASES,
                budget=agent_budget.snapshot(),
            )
        except Exception:  # noqa: BLE001 - sidecar observation is optional
            workflow_journal = None
    if depth >= MAX_AGENT_DEPTH:
        # The phase runner owns the established typed depth-failure response
        # and observation projection.  No workspace snapshot is needed.
        return _run_workflow_phases(
            input_data,
            context,
            depth,
            subagent_id=subagent_id,
            workflow_journal=workflow_journal,
            transaction=None,
            run_subagent=run_subagent,
        )

    isolation_started = time.time()
    timeout_seconds = float(
        getattr(context, "_task_timeout_seconds", None)
        or _task_timeout_seconds(context)
    )
    deadline_monotonic = getattr(context, "_task_deadline_monotonic", None)
    transaction: _WorkflowWorkspaceTransaction | None = None
    workflow_result: ToolResult | None = None

    # Process-local admission: refuse before the snapshot when the declared
    # write scope overlaps an active workflow.  The claim is held through
    # promotion and released on every terminal path (see the finally below).
    workflow_id = "wf_" + subagent_id.removeprefix("sub_")
    declared_write_paths = _workflow_declared_write_paths(
        input_data,
        subagent_id=subagent_id,
    )
    resolved_write_paths = tuple(
        (Path(context.cwd).resolve(strict=False) / path).as_posix()
        for path in declared_write_paths
    )
    scope_registry = process_workflow_scope_registry()
    scope_registration: WorkflowScopeRegistration | None = None
    try:
        scope_registration = scope_registry.register(
            workflow_id,
            write_paths=resolved_write_paths,
        )
    except WorkflowWriteScopeConflict as conflict:
        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            journal=workflow_journal,
            outcome="write_scope_conflict",
            results=[],
            phase_count=0,
            started=isolation_started,
            model_turn_limit=_workflow_model_turn_limit(agent_budget),
            result_truncated=False,
        )
        overlap_preview = ", ".join(
            f"{incoming} ~ {active}"
            for incoming, active in conflict.overlapping_paths[:5]
        )
        return ToolResult(
            ok=False,
            output=(
                "error[workflow_write_conflict]: declared write scope overlaps "
                f"active workflow {conflict.conflicting_workflow_id} "
                f"({overlap_preview}); rerun after it finishes"
            ),
        )

    def committed_fallback(finalization: str) -> ToolResult:
        status = {
            "finalizationVersion": 1,
            "changesCommitted": True,
            "commitReadyStatus": "unknown",
            "finalizationStatus": finalization,
            "auditStatus": "projection_or_persistence_failed",
            "traceProjection": "failed",
            "resultProjection": "failed",
        }
        output = (
            "error[workflow_finalization_incomplete]: "
            f"changesCommitted=true, finalizationStatus={finalization}\n\n"
            "=== WORKFLOW FINALIZATION v1 ===\n" + json.dumps(status, sort_keys=True)
        )
        return ToolResult(
            ok=False,
            output=output[:_WORKFLOW_OUTPUT_MAX_CHARS],
            _must_deliver=True,
        )

    def unknown_commit_fallback() -> ToolResult:
        status = {
            "finalizationVersion": 1,
            "changesCommitted": None,
            "commitState": "unknown",
            "commitReadyStatus": "persisted_or_unknown",
            "finalizationStatus": "commit_state_unknown",
            "auditStatus": "projection_or_persistence_failed",
            "traceProjection": "failed",
            "resultProjection": "failed",
        }
        output = (
            "error[workflow_commit_state_unknown]: "
            "changesCommitted=unknown; inspect the scoped parent files "
            "before retrying\n\n=== WORKFLOW FINALIZATION v1 ===\n"
            + json.dumps(status, sort_keys=True)
        )
        return ToolResult(
            ok=False,
            output=output[:_WORKFLOW_OUTPUT_MAX_CHARS],
            _must_deliver=True,
        )

    def promoted_result_or_fallback(finalization: str) -> ToolResult | None:
        if transaction is not None and transaction.parent_promotion_unknown:
            return unknown_commit_fallback()
        if transaction is None or not transaction.parent_promotion_completed:
            return None
        if workflow_result is not None and workflow_result._must_deliver:
            return workflow_result
        return committed_fallback(finalization)

    try:
        with _WorkflowWorkspaceTransaction(
            context.cwd,
            deadline_monotonic=(
                float(deadline_monotonic)
                if isinstance(deadline_monotonic, (int, float))
                else None
            ),
            timeout_seconds=timeout_seconds,
            operation_checkpoint=lambda: _raise_task_deadline(context),
            # Keep the declared write bound for freeze and apply reconciliation.
            # Promotion validates the complete snapshot baseline because write
            # paths alone do not prove the change's read dependencies.
            baseline_scope_paths=(
                declared_write_paths if declared_write_paths else None
            ),
            baseline_scope_relative_to_cwd=True,
        ) as transaction:
            isolated_input = dict(input_data)
            isolated_input["prompt"] = transaction.remap_text(input_data["prompt"])
            isolated_context = replace(context, cwd=str(transaction.cwd))
            workflow_result = _run_workflow_phases(
                isolated_input,
                isolated_context,
                depth,
                subagent_id=subagent_id,
                workflow_journal=workflow_journal,
                transaction=transaction,
                run_subagent=run_subagent,
            )
            if input_data.get("_workflow_auto_constructed") is True:
                # Keep the simplified entry distinguishable from a
                # hand-written WorkflowRequest.v1 in the result handed back
                # to the parent; the input flag carries the same fact for
                # embedders and tests.
                workflow_result = replace(
                    workflow_result,
                    output=(
                        workflow_result.output
                        + "\n\nrequestSource: auto-constructed from the "
                        "simplified task entry ("
                        + (
                            "structured WorkflowRequest.v1 built from "
                            "write_paths/verification_commands)"
                            if input_data.get("_workflow_auto_constructed_mode")
                            == "structured"
                            else "fail-closed legacy request; no write paths "
                            "were declared, so the declared write scope stays "
                            "empty)"
                        )
                    ),
                )
        return workflow_result
    except (_TaskDeadlineExceeded, AgentTurnDeadlineExceeded) as exc:
        promoted = promoted_result_or_fallback("timed_out_after_commit")
        if promoted is not None:
            return promoted
        agent_budget = getattr(context, "_agent_budget", None)
        if agent_budget is None:
            runtime = getattr(context, "_runtime", None)
            agent_budget = AgentTurnBudget.from_runtime(
                runtime if isinstance(runtime, dict) else {}
            )
        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            journal=workflow_journal,
            outcome="failed",
            results=[],
            phase_count=0,
            started=isolation_started,
            model_turn_limit=_workflow_model_turn_limit(agent_budget),
            result_truncated=False,
        )
        return ToolResult(
            ok=False,
            output=f"error[sub_agent_deadline_exceeded]: {exc}",
        )
    except _WorkflowIsolationError as exc:
        promoted = promoted_result_or_fallback("projection_failed")
        if promoted is not None:
            return promoted
        agent_budget = getattr(context, "_agent_budget", None)
        if agent_budget is None:
            runtime = getattr(context, "_runtime", None)
            agent_budget = AgentTurnBudget.from_runtime(
                runtime if isinstance(runtime, dict) else {}
            )
        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="unavailable",
            journal=workflow_journal,
            outcome="failed",
            results=[],
            phase_count=0,
            started=isolation_started,
            model_turn_limit=_workflow_model_turn_limit(agent_budget),
            result_truncated=False,
        )
        return ToolResult(
            ok=False,
            output=(
                "error[workflow_isolation_unavailable]: workflow execution "
                f"did not start because a safe workspace snapshot could not "
                f"be established ({exc})"
            ),
        )
    except TurnCancellationRequested:
        promoted = promoted_result_or_fallback("cancelled_after_commit")
        if promoted is not None:
            return promoted
        raise
    except Exception:
        promoted = promoted_result_or_fallback("projection_failed")
        if promoted is not None:
            return promoted
        raise
    except BaseException:
        promoted = promoted_result_or_fallback("projection_failed")
        if promoted is not None:
            return promoted
        raise
    finally:
        # Every terminal path (success, failure, cancellation, timeout and
        # all post-promotion fallbacks) releases the admission claim so a
        # later workflow may reuse the same write paths.
        if scope_registration is not None:
            scope_registry.unregister(scope_registration.workflow_id)


class _WorkflowRunSession:
    """One non-reentrant workflow run with scheduler-owned phase ordering."""

    __slots__ = (
        "_input_data",
        "_context",
        "_depth",
        "_subagent_id",
        "_workflow_journal",
        "_transaction",
        "_run_subagent",
        "_ran",
        "_state",
    )

    def __init__(
        self,
        input_data: dict,
        context: object,
        depth: int,
        *,
        subagent_id: str,
        workflow_journal: SubagentRunJournal | None,
        transaction: _WorkflowWorkspaceTransaction | None,
        run_subagent: _RunSubagent,
    ) -> None:
        self._input_data = input_data
        self._context = context
        self._depth = depth
        self._subagent_id = subagent_id
        self._workflow_journal = workflow_journal
        self._transaction = transaction
        self._run_subagent = run_subagent
        self._ran = False
        self._state: _WorkflowExecutionState | None = None

    def _execution_state(self) -> _WorkflowExecutionState:
        state = self._state
        if state is None:
            raise RuntimeError("workflow execution authority is sealed")
        return state

    def _seal_execution_capabilities(self) -> None:
        """Make every execution-capable session handle unusable."""
        self._state = None
        self._context = None
        self._workflow_journal = None
        self._transaction = None
        self._run_subagent = None

    def run(self) -> ToolResult:
        if self._ran:
            raise RuntimeError("a workflow run session cannot be reused")
        self._ran = True
        input_data = self._input_data
        context = self._context
        depth = self._depth
        subagent_id = self._subagent_id
        workflow_journal = self._workflow_journal
        transaction = self._transaction
        run_subagent = self._run_subagent
        task_prompt = input_data["prompt"]
        description = input_data["description"]
        started = time.time()
        agent_budget = getattr(context, "_agent_budget", None)
        if agent_budget is None:
            runtime = getattr(context, "_runtime", None)
            agent_budget = AgentTurnBudget.from_runtime(
                runtime if isinstance(runtime, dict) else {}
            )
        model_turn_limit = _workflow_model_turn_limit(agent_budget)

        if depth >= MAX_AGENT_DEPTH:
            _emit_workflow_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="unavailable",
                journal=workflow_journal,
                outcome="depth_rejected",
                results=[],
                phase_count=0,
                started=started,
                model_turn_limit=model_turn_limit,
                result_truncated=False,
            )
            return ToolResult(
                ok=False,
                output=(
                    f"error[sub_agent_depth_exceeded]: workflow cannot run at "
                    f"depth {depth} (max {MAX_AGENT_DEPTH})"
                ),
            )

        child_context = replace(
            context,
            _agent_depth=depth + 1,
            _agent_budget=agent_budget,
            _workflow_phase=True,
        )
        mailbox = getattr(context, "_subagent_mailbox", None)
        explore_results: list[ToolResult] = []
        if input_data.get("parallel_explore"):
            explore_results = _run_parallel_exploration(
                input_data,
                context,
                child_context,
                mailbox,
                run_subagent=run_subagent,
            )
            _raise_task_deadline(context)

        structured = input_data.get("workflow_request") is not None
        if structured:
            # Structured Analysts read only scheduler-assigned artifacts, so
            # the fan-out survey is inlined into their prompt (bounded per
            # facet) instead of the legacy shared-mailbox notes.
            research_notes = _inline_explore_notes(explore_results)
        else:
            research_notes = (
                "\n\nResearch notes are available in the shared mailbox under "
                "workflow_explore_architecture, workflow_explore_tests and "
                "workflow_explore_risks. Read the relevant notes with "
                "subagent_note_read before finalizing the plan."
                if explore_results
                else ""
            )

        plan_result: ToolResult | None = None
        execute_result: ToolResult | None = None
        review_result: ToolResult | None = None
        typed_review = _workflow_review_not_applicable()
        verification_result: dict[str, object] | None = None
        frozen_workspace_change: _FrozenWorkflowChange | None = None
        frozen_change_set: dict[str, object] | None = None
        commit_error: _WorkflowIsolationError | None = None
        commit_ready_status = "not_applicable"
        review_key = f"workflow_review_{new_subagent_id()}"
        workflow_id = "wf_" + subagent_id.removeprefix("sub_")
        legacy_task_id = "task_" + subagent_id.removeprefix("sub_")
        workflow_request = parse_workflow_request(
            input_data.get("workflow_request"),
            legacy_task_id=legacy_task_id,
        )
        profile = workflow_request.task_profile
        approval_request_id: str | None = None
        approval_request_document: dict[str, object] | None = None
        human_approval_document: dict[str, object] | None = None
        task_id = workflow_request.task_id
        allowed_paths = (
            workflow_request.scope.write_paths
            if workflow_request.structured
            else (".",)
        )
        requested_checks = tuple(
            VerificationCheck(
                check_id=check.check_id,
                kind=check.kind,
                argv=check.argv,
                timeout_seconds=120,
            )
            for check in workflow_request.verification_checks
        )
        if not workflow_request.structured:
            requested_checks = (
                VerificationCheck(
                    check_id="check_workspace_patch",
                    kind="oracle",
                    argv=("git", "diff", "--cached", "--check"),
                    timeout_seconds=30,
                ),
            )
        transitions: list[SchedulerTransition] = []
        executor_assignment_id: str | None = None
        scheduler_artifact_authority = object()
        artifact_store = SchedulerArtifactStore(
            scheduler_authority=scheduler_artifact_authority
        )
        workflow_request_document = workflow_request.to_dict()
        workflow_objective_digest = (
            "sha256:" + hashlib.sha256(task_prompt.encode("utf-8")).hexdigest()
        )
        workflow_request_payload = json.dumps(
            {
                "objectiveDigest": workflow_objective_digest,
                "request": workflow_request_document,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        workflow_request_digest = (
            "sha256:" + hashlib.sha256(workflow_request_payload).hexdigest()
        )
        workflow_request_artifact_id = (
            "artifact_request_" + workflow_request_digest.removeprefix("sha256:")[:32]
        )
        if workflow_request.structured:
            artifact_store.publish(
                workflow_request_artifact_id,
                {
                    "requestArtifactVersion": 1,
                    "resultType": "WorkflowRequestArtifact.v1",
                    "workflowId": workflow_id,
                    "taskId": task_id,
                    "requestDigest": workflow_request_digest,
                    "objectiveDigest": workflow_objective_digest,
                    "request": workflow_request_document,
                },
                authority=scheduler_artifact_authority,
            )
        human_approval_artifact_id: str | None = None
        analyst_report: dict[str, object] | None = None
        analyst_artifact_id: str | None = None
        analyst_evidence_ids: list[str] = []
        implementation_order: ImplementationOrder | None = None
        execution_artifact: dict[str, object] | None = None
        execution_artifact_id: str | None = None
        change_diff_artifact: dict[str, object] | None = None
        change_diff_artifact_id: str | None = None
        verification_artifact_id: str | None = None
        verification_evidence_ids: list[str] = []
        acceptance_evidence_matrix_id: str | None = None
        acceptance_evidence_matrix: dict[str, object] | None = None
        locked_write_paths = allowed_paths

        self._state = _WorkflowExecutionState(
            acceptance_evidence_matrix=acceptance_evidence_matrix,
            acceptance_evidence_matrix_id=acceptance_evidence_matrix_id,
            agent_budget=agent_budget,
            analyst_artifact_id=analyst_artifact_id,
            analyst_evidence_ids=analyst_evidence_ids,
            analyst_report=analyst_report,
            approval_request_document=approval_request_document,
            approval_request_id=approval_request_id,
            artifact_store=artifact_store,
            change_diff_artifact=change_diff_artifact,
            change_diff_artifact_id=change_diff_artifact_id,
            child_context=child_context,
            commit_error=commit_error,
            commit_ready_status=commit_ready_status,
            context=context,
            description=description,
            execute_result=execute_result,
            execution_artifact=execution_artifact,
            execution_artifact_id=execution_artifact_id,
            executor_assignment_id=executor_assignment_id,
            frozen_change_set=frozen_change_set,
            frozen_workspace_change=frozen_workspace_change,
            human_approval_artifact_id=human_approval_artifact_id,
            human_approval_document=human_approval_document,
            implementation_order=implementation_order,
            locked_write_paths=locked_write_paths,
            mailbox=mailbox,
            plan_result=plan_result,
            profile=profile,
            requested_checks=requested_checks,
            research_notes=research_notes,
            review_key=review_key,
            review_result=review_result,
            role_result_parse_errors={},
            run_subagent=run_subagent,
            scheduler_artifact_authority=scheduler_artifact_authority,
            task_id=task_id,
            task_prompt=task_prompt,
            transaction=transaction,
            transitions=transitions,
            typed_review=typed_review,
            adjudication_verdict=None,
            pending_conflict_artifact_id=None,
            pending_conflict_package=None,
            verification_artifact_id=verification_artifact_id,
            verification_evidence_ids=verification_evidence_ids,
            verification_result=verification_result,
            workflow_id=workflow_id,
            workflow_request=workflow_request,
            workflow_request_artifact_id=workflow_request_artifact_id,
            workflow_request_digest=workflow_request_digest,
        )

        scheduler = CentralScheduler()
        dispatch_plan = scheduler.plan(profile)
        try:
            scheduler_outcome = scheduler.execute(
                profile,
                workflow_id=workflow_id,
                run_worker=self.run_worker,
                run_verifier=self.run_verifier,
                run_human=(
                    self.run_human
                    if workflow_request.structured
                    and profile.human_confirmation_required
                    else None
                ),
                prepare_commit=(
                    self.prepare_commit if workflow_request.structured else None
                ),
                commit=self.commit_change,
                on_transition=self.record_transition,
                deadline_monotonic=getattr(
                    context,
                    "_task_deadline_monotonic",
                    None,
                ),
                abort_exceptions=(
                    TurnCancellationRequested,
                    ToolExecutionAbandoned,
                ),
            )
        finally:
            artifact_store.revoke_pending_assignment_writes(
                authority=scheduler_artifact_authority,
            )

        try:
            return self._seal_and_finalize(
                context=context,
                subagent_id=subagent_id,
                workflow_journal=workflow_journal,
                started=started,
                model_turn_limit=model_turn_limit,
                explore_results=explore_results,
                dispatch_plan=dispatch_plan,
                scheduler_outcome=scheduler_outcome,
            )
        finally:
            # Every post-Scheduler exit revokes execution authority, including
            # cancellation and fatal snapshot/fallback-capture failures.
            self._seal_execution_capabilities()

    def _seal_and_finalize(
        self,
        *,
        context: object,
        subagent_id: str,
        workflow_journal: SubagentRunJournal | None,
        started: float,
        model_turn_limit: int | None,
        explore_results: list[ToolResult],
        dispatch_plan: DispatchPlan,
        scheduler_outcome: WorkflowExecutionOutcome,
    ) -> ToolResult:
        state = self._state
        if state is None:
            raise RuntimeError("workflow execution state is unavailable")

        def checkpoint() -> None:
            try:
                _raise_task_deadline(context)
            except (_TaskDeadlineExceeded, AgentTurnDeadlineExceeded) as exc:
                raise _FinalizationDeadlineExceeded(str(exc)) from exc

        initial_finalization_status = "completed"
        try:
            checkpoint()
        except TurnCancellationRequested:
            if not scheduler_outcome.committed:
                raise
            initial_finalization_status = "cancelled_after_commit"
        except _FinalizationDeadlineExceeded:
            initial_finalization_status = (
                "timed_out_after_commit" if scheduler_outcome.committed else "timed_out"
            )

        artifact_snapshot = None
        try:
            artifact_ids = state.artifact_store.list_artifact_ids()
            artifact_snapshot = tuple(
                artifact
                for artifact_id in artifact_ids
                if (artifact := state.artifact_store.read(artifact_id)) is not None
            )
            if len(artifact_snapshot) != len(artifact_ids):
                raise RuntimeError("scheduler artifact snapshot is inconsistent")
        except TurnCancellationRequested:
            if not scheduler_outcome.committed:
                raise
            initial_finalization_status = "cancelled_after_commit"
        except BaseException as exc:
            if not isinstance(exc, Exception) and not scheduler_outcome.committed:
                raise

        audit_recorder = getattr(
            getattr(context, "_event_sink", None),
            "record_scheduler_audit",
            None,
        )

        def persist_audit(audit: object) -> object:
            return _run_finalization_port(
                _FINALIZATION_AUDIT_PORT_RUNNER,
                lambda: audit_recorder(audit),
                port_name="workflow-final-audit",
                operation_id=f"{scheduler_outcome.workflow_id}:audit",
                context=context,
                committed=scheduler_outcome.committed,
            )

        def observe(observation: _WorkflowObservation) -> None:
            _run_finalization_port(
                _FINALIZATION_OBSERVATION_PORT_RUNNER,
                lambda: _record_workflow_observation(
                    context,
                    subagent_id=subagent_id,
                    journal=workflow_journal,
                    model_turn_limit=model_turn_limit,
                    observation=observation,
                ),
                port_name="workflow-final-observation",
                operation_id=f"{scheduler_outcome.workflow_id}:observe",
                context=context,
                committed=scheduler_outcome.committed,
            )

        ports = _WorkflowFinalizationPorts(
            checkpoint=checkpoint,
            persist_audit=(persist_audit if callable(audit_recorder) else None),
            observe=observe,
        )
        contract_mode = "v2" if state.workflow_request.structured else "legacy"
        production_audit_required = (
            state.workflow_request.structured
            and getattr(context, "_central_workflow_v2_enforced", False) is True
        )
        commit_error_text = (
            None if state.commit_error is None else str(state.commit_error)
        )
        try:
            snapshot = _WorkflowFinalizationSnapshot.capture(
                description=state.description,
                subagent_id=subagent_id,
                started_at=started,
                contract_mode=contract_mode,
                profile=state.profile,
                dispatch_plan=dispatch_plan,
                scheduler_outcome=scheduler_outcome,
                transitions=state.transitions,
                artifacts=artifact_snapshot,
                explore_results=explore_results,
                plan_result=state.plan_result,
                execute_result=state.execute_result,
                review_result=state.review_result,
                analyst_report=state.analyst_report,
                execution_artifact=state.execution_artifact,
                verification_result=state.verification_result,
                typed_review=state.typed_review,
                approval_request_id=state.approval_request_id,
                commit_ready_status=state.commit_ready_status,
                commit_error_text=commit_error_text,
                production_audit_required=production_audit_required,
                initial_finalization_status=initial_finalization_status,
            )
        except BaseException:
            if not scheduler_outcome.committed:
                raise
            snapshot = _WorkflowFinalizationSnapshot.capture_projection_failure(
                description=state.description,
                subagent_id=subagent_id,
                started_at=started,
                contract_mode=contract_mode,
                profile=state.profile,
                dispatch_plan=dispatch_plan,
                scheduler_outcome=scheduler_outcome,
                transitions=state.transitions,
                artifacts=artifact_snapshot,
                approval_request_id=state.approval_request_id,
                commit_ready_status=state.commit_ready_status,
                commit_error_text=commit_error_text,
                production_audit_required=production_audit_required,
            )
        self._seal_execution_capabilities()
        del state
        return finalize_workflow(snapshot, ports)

    def run_assigned_worker(
        self,
        worker_input: dict,
        worker_context,
        channel: AssignmentArtifactChannel | None,
    ) -> ToolResult:
        """Run one Worker and revoke its result key at the return boundary."""
        try:
            return self._execution_state().run_subagent(worker_input, worker_context)
        finally:
            if channel is not None:
                channel.close()

    def run_worker(self, assignment) -> StageResult:
        if assignment.stage is DispatchStage.ANALYST:
            return self._run_analyst(assignment)
        if assignment.stage is DispatchStage.EXECUTOR:
            return self._run_executor(assignment)
        if assignment.stage is DispatchStage.REVIEWER:
            return self._run_reviewer(assignment)
        if assignment.stage is DispatchStage.ADJUDICATOR:
            return self._run_adjudicator(assignment)
        return StageResult(
            status="failed",
            artifact_id=_stage_receipt_artifact_id(assignment),
            error_code="unsupported_worker_stage",
        )

    def _run_analyst(self, assignment) -> StageResult:
        self._execution_state().analyst_evidence_ids.clear()
        if self._execution_state().workflow_request.structured:
            analyst_assignment = _build_worker_assignment(
                self._execution_state().workflow_request,
                assignment,
                context=self._execution_state().context,
                agent_budget=self._execution_state().agent_budget,
                role="analyst",
                mode=(
                    "investigate"
                    if self._execution_state().workflow_request.kind == "read_only"
                    else "prepare_change"
                ),
                objective=self._execution_state().task_prompt,
                input_refs=(self._execution_state().workflow_request_artifact_id,),
            )
            result_key = _assignment_result_key(assignment, "analyst")
            assignment_artifact_id = _assignment_artifact_id(assignment)
            self._execution_state().artifact_store.publish(
                assignment_artifact_id,
                _assignment_artifact_document(
                    assignment,
                    analyst_assignment,
                ),
                authority=self._execution_state().scheduler_artifact_authority,
            )
            channel = self._execution_state().artifact_store.create_assignment_channel(
                readable_keys=(
                    *analyst_assignment.input_refs,
                    assignment_artifact_id,
                ),
                write_key=result_key,
            )
            channel = _RepairingRoleResultChannel(channel)
            analyst_context = replace(
                self._execution_state().child_context,
                _subagent_mailbox=channel,
                _logical_agent_role="analyst",
                _logical_agent_scope=analyst_assignment.scope,
                _logical_agent_workspace_root=(
                    str(self._execution_state().transaction.cwd)
                    if self._execution_state().transaction is not None
                    else self._execution_state().context.cwd
                ),
            )
            self._execution_state().plan_result = self.run_assigned_worker(
                {
                    "description": f"analyst:{self._execution_state().description}",
                    "agent_type": "plan",
                    "_subagent_id": assignment.agent_run_id,
                    "prompt": (
                        "Complete only this immutable Analyst Assignment. "
                        "Every material claim needs a repository path and "
                        "line range. Do not modify files.\n"
                        + (
                            self._execution_state().research_notes
                            if self._execution_state().research_notes
                            else ""
                        )
                        + "\n\nASSIGNMENT:\n"
                        + json.dumps(
                            analyst_assignment.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                    "_result_key": result_key,
                    "_result_instruction": _role_result_instruction(
                        analyst_assignment,
                        result_key=result_key,
                        previous_error=(
                            self._execution_state().role_result_parse_errors.get(
                                "analyst"
                            )
                        ),
                    ),
                },
                analyst_context,
                channel,
            )
            if self._execution_state().plan_result.ok:
                try:
                    analyst_payload, analyst_repaired = (
                        _read_role_result_payload_with_repair(
                            self._execution_state().artifact_store,
                            result_key,
                        )
                    )
                    self._execution_state().analyst_report = parse_role_result(
                        analyst_assignment,
                        analyst_payload,
                    )
                except Exception as exc:
                    self._execution_state().role_result_parse_errors["analyst"] = (
                        _role_result_error_feedback(exc)
                    )
                    return StageResult(
                        status="failed",
                        artifact_id=_stage_receipt_artifact_id(assignment),
                        retryable=True,
                        error_code="analyst_result_invalid",
                    )
                self._execution_state().role_result_parse_errors.pop("analyst", None)
                if analyst_repaired or channel.repaired:
                    _publish_role_result_repair_receipt(
                        self._execution_state(),
                        assignment,
                        "analyst",
                    )
                try:
                    if self._execution_state().transaction is None:
                        raise _WorkflowIsolationError(
                            "workflow transaction is unavailable"
                        )
                    evidence_documents = _freeze_analyst_evidence(
                        self._execution_state().analyst_report,
                        workspace_root=self._execution_state().transaction.cwd,
                        base_digest=self._execution_state().transaction.base_digest,
                        stage_assignment=assignment,
                        operation_checkpoint=lambda: _raise_task_deadline(
                            self._execution_state().context
                        ),
                    )
                    for evidence_id, evidence_document in evidence_documents:
                        self._execution_state().artifact_store.publish(
                            evidence_id,
                            evidence_document,
                            authority=self._execution_state().scheduler_artifact_authority,
                        )
                        self._execution_state().analyst_evidence_ids.append(evidence_id)
                    self._execution_state().analyst_artifact_id = (
                        f"artifact_{assignment.assignment_id}_"
                        f"{assignment.attempt_id}_analyst_handoff"
                    )
                    self._execution_state().artifact_store.publish(
                        self._execution_state().analyst_artifact_id,
                        {
                            "handoffVersion": 1,
                            "resultType": "AnalystHandoff.v1",
                            "report": self._execution_state().analyst_report,
                            "evidenceSnapshotRefs": list(
                                self._execution_state().analyst_evidence_ids
                            ),
                        },
                        authority=self._execution_state().scheduler_artifact_authority,
                    )
                except _WorkflowIsolationError as exc:
                    # Same bounded-feedback seam as parse failures: the retry
                    # prompt names the offending evidence rule so the Analyst
                    # can correct its citations instead of repeating them.
                    self._execution_state().role_result_parse_errors["analyst"] = (
                        _role_result_error_feedback(exc)
                    )
                    return StageResult(
                        status="failed",
                        artifact_id=_stage_receipt_artifact_id(assignment),
                        retryable=True,
                        error_code="analyst_evidence_invalid",
                    )
                if self._execution_state().analyst_report["status"] != "completed":
                    analyst_status = str(
                        self._execution_state().analyst_report["status"]
                    )
                    return StageResult(
                        status=analyst_status,
                        artifact_id=self._execution_state().analyst_artifact_id,
                        error_code=f"analyst_result_{analyst_status}",
                    )
                if self._execution_state().analyst_report["unknowns"]:
                    return StageResult(
                        status="inconclusive",
                        artifact_id=self._execution_state().analyst_artifact_id,
                        error_code=("analyst_unknowns_require_request_revision"),
                    )
                if analyst_report_requires_reclassification(
                    analyst_assignment,
                    self._execution_state().analyst_report,
                ):
                    conflict_package, conflict_artifact_id = (
                        _freeze_analyst_risk_conflict(
                            self._execution_state().workflow_request,
                            assignment,
                            analyst_assignment,
                            self._execution_state().analyst_report,
                            analyst_evidence_ids=(
                                self._execution_state().analyst_evidence_ids
                            ),
                            workflow_request_artifact_id=(
                                self._execution_state()
                                .workflow_request_artifact_id
                            ),
                        )
                    )
                    self._execution_state().artifact_store.publish(
                        conflict_artifact_id,
                        conflict_package,
                        authority=(
                            self._execution_state().scheduler_artifact_authority
                        ),
                    )
                    self._execution_state().pending_conflict_package = (
                        conflict_package
                    )
                    self._execution_state().pending_conflict_artifact_id = (
                        conflict_artifact_id
                    )
                    return StageResult(
                        status="blocked",
                        artifact_id=self._execution_state().analyst_artifact_id,
                        error_code=("analyst_risk_requires_reclassification"),
                        escalate_conflict=conflict_package,
                        conflict_artifact_id=conflict_artifact_id,
                    )
        else:
            self._execution_state().plan_result = self._execution_state().run_subagent(
                {
                    "description": f"plan:{self._execution_state().description}",
                    "agent_type": "plan",
                    "_subagent_id": assignment.agent_run_id,
                    "prompt": (
                        "Produce a concrete, step-by-step implementation plan "
                        "for this task. Do not modify files. Write the final "
                        "plan to the shared mailbox with subagent_note_write "
                        "using key workflow_plan. End with <final> and a "
                        "numbered plan plus the files you expect to touch."
                        f"{self._execution_state().research_notes}\n\nTASK:\n{self._execution_state().task_prompt}"
                    ),
                },
                self._execution_state().child_context,
            )
        _raise_task_deadline(self._execution_state().context)
        if (
            self._execution_state().mailbox is not None
            and not self._execution_state().workflow_request.structured
        ):
            try:
                self._execution_state().mailbox.write(
                    "workflow_plan",
                    self._execution_state().plan_result.output,
                    author="workflow",
                )
            except Exception:  # noqa: BLE001 - legacy note is optional
                pass
        terminal_analyst_artifact_id = (
            self._execution_state().analyst_artifact_id
            if self._execution_state().plan_result.ok
            and self._execution_state().workflow_request.structured
            else _stage_receipt_artifact_id(assignment)
            if self._execution_state().workflow_request.structured
            else _stage_receipt_artifact_id(assignment)
        )
        if terminal_analyst_artifact_id is None:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                retryable=True,
                error_code="analyst_artifact_missing",
            )
        return StageResult(
            status="completed" if self._execution_state().plan_result.ok else "failed",
            artifact_id=terminal_analyst_artifact_id,
            retryable=not self._execution_state().plan_result.ok,
            error_code=None
            if self._execution_state().plan_result.ok
            else "plan_phase_failed",
        )

    def _repair_budget_available(self) -> bool:
        state = self._execution_state()
        if not state.workflow_request.structured or state.profile.human_confirmation_required:
            return False
        budget = state.agent_budget.snapshot()
        return (
            (budget.limit_model_calls is None or budget.used_model_calls < budget.limit_model_calls)
            and (budget.limit_total_tokens is None or (
                budget.used_total_tokens + budget.reserved_total_tokens < budget.limit_total_tokens
            ))
            and (budget.limit_cost_usd is None or (
                Decimal(budget.used_cost_usd) < Decimal(budget.limit_cost_usd)
            ))
        )

    def _verification_repairable(self) -> bool:
        state = self._execution_state()
        if (
            not self._repair_budget_available()
            or state.verification_result is None
            or not state.change_diff_artifact
            or state.change_diff_artifact.get("complete") is not True
        ):
            return False
        documents = []
        for artifact_id in state.verification_evidence_ids:
            artifact = state.artifact_store.read(artifact_id)
            if artifact is None:
                return False
            documents.append(json.loads(artifact.content))
        return verification_supports_implementation_repair(state.verification_result, documents)

    def _prepare_implementation_repair(self, assignment) -> tuple[str, dict[str, object]]:
        state = self._execution_state()
        if (
            not self._repair_budget_available()
            or state.transaction is None
            or state.frozen_workspace_change is None
            or state.implementation_order is None
        ):
            raise ValueError("implementation repair authority is unavailable")
        source = state.artifact_store.read(assignment.repair_source_artifact_id)
        if source is None:
            raise ValueError("implementation repair feedback is unavailable")
        source_document = json.loads(source.content)
        source_stage = (
            "verifier" if source_document.get("resultType") == "VerificationResult.v1"
            else "reviewer"
        )
        if not (
            self._verification_repairable() if source_stage == "verifier"
            else review_supports_implementation_repair(source_document)
        ):
            raise ValueError("implementation repair source is not actionable")
        request = parse_implementation_repair_request({
            "repairVersion": 1,
            "resultType": REPAIR_REQUEST_TYPE,
            "workflowId": state.workflow_id,
            "taskId": state.task_id,
            "assignmentId": assignment.assignment_id,
            "repairIndex": assignment.repair_index,
            "sourceStage": source_stage,
            "sourceArtifactRef": assignment.repair_source_artifact_id,
            "requestArtifactRef": state.workflow_request_artifact_id,
            "previousExecutionArtifactRef": state.execution_artifact_id,
            "previousOrderRef": state.implementation_order.order_id,
            "previousDiffArtifactRef": state.change_diff_artifact_id,
            "previousPatchDigest": state.frozen_workspace_change.patch_digest,
            "previousVerificationArtifactRef": state.verification_artifact_id,
            "evidenceRefs": list(state.verification_evidence_ids),
        })
        artifact_id = f"artifact_repair_{assignment.assignment_id}"
        state.artifact_store.publish(artifact_id, request, authority=state.scheduler_artifact_authority)
        state.transaction.resume_for_repair(state.frozen_workspace_change)
        # No previous patch capability or gate approval may authorize a revision.
        state.frozen_workspace_change = None
        state.frozen_change_set = None
        state.execution_artifact = None
        state.execution_artifact_id = None
        state.change_diff_artifact = None
        state.change_diff_artifact_id = None
        state.verification_result = None
        state.verification_artifact_id = None
        state.verification_evidence_ids.clear()
        state.acceptance_evidence_matrix = None
        state.acceptance_evidence_matrix_id = None
        state.review_result = None
        state.typed_review = _workflow_review_not_applicable()
        return artifact_id, request

    def _run_executor(self, assignment) -> StageResult:
        if (
            self._execution_state().profile.human_confirmation_required
            and not _human_approval_is_current(
                self._execution_state().human_approval_document
            )
        ):
            return StageResult(
                status="blocked",
                artifact_id=(
                    self._execution_state().human_approval_artifact_id
                    or _stage_receipt_artifact_id(assignment)
                ),
                requires_human=True,
                error_code="human_approval_expired",
            )
        repair_request = None
        repair_refs: tuple[str, ...] = ()
        if assignment.repair_source_artifact_id is not None:
            try:
                repair_id, repair_request = self._prepare_implementation_repair(assignment)
                repair_refs = implementation_repair_input_refs(repair_id, repair_request)
            except (ValueError, _WorkflowIsolationError):
                return StageResult(
                    status="failed", artifact_id=_stage_receipt_artifact_id(assignment),
                    error_code="implementation_repair_unavailable",
                )
        self._execution_state().executor_assignment_id = assignment.assignment_id
        if (
            self._execution_state().plan_result is None
            and not self._execution_state().workflow_request.structured
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="plan_artifact_missing",
            )
        executor_assignment: Assignment | None = None
        if self._execution_state().workflow_request.structured:
            if self._execution_state().transaction is None:
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    error_code="workflow_transaction_missing",
                )
            executor_input_refs = (
                (
                    self._execution_state().analyst_artifact_id,
                    self._execution_state().workflow_request_artifact_id,
                    *self._execution_state().analyst_evidence_ids,
                    *(
                        (self._execution_state().human_approval_artifact_id,)
                        if self._execution_state().human_approval_artifact_id
                        is not None
                        else ()
                    ),
                )
                if self._execution_state().analyst_report is not None
                and self._execution_state().analyst_artifact_id is not None
                else (
                    # Scoped route: no Analyst handoff exists, but the
                    # executor stays bound to the frozen WorkflowRequest.
                    self._execution_state().workflow_request_artifact_id,
                )
            )
            executor_input_refs = (*executor_input_refs, *repair_refs)
            executor_assignment = _build_worker_assignment(
                self._execution_state().workflow_request,
                assignment,
                context=self._execution_state().context,
                agent_budget=self._execution_state().agent_budget,
                role="executor",
                mode="implement",
                objective=self._execution_state().task_prompt,
                input_refs=executor_input_refs,
            )
            candidate = (
                self._execution_state().analyst_report.get(
                    "implementationOrderCandidate"
                )
                if self._execution_state().analyst_report is not None
                else None
            )
            candidate_steps = (
                candidate.get("steps", []) if isinstance(candidate, dict) else []
            )
            if candidate_steps:
                raw_steps = [
                    {
                        "id": step["id"],
                        "action": step["action"],
                        "allowedPaths": list(step["allowedPaths"]),
                        "criterionIds": [
                            item.criterion_id
                            for item in self._execution_state().workflow_request.criteria
                        ],
                    }
                    for step in candidate_steps
                    if isinstance(step, dict)
                ]
                self._execution_state().locked_write_paths = tuple(
                    dict.fromkeys(
                        path for step in raw_steps for path in step["allowedPaths"]
                    )
                )
            else:
                self._execution_state().locked_write_paths = (
                    self._execution_state().workflow_request.scope.write_paths
                )
                raw_steps = [
                    {
                        "id": f"s_path_{index}",
                        "action": self._execution_state().task_prompt,
                        "allowedPaths": [path],
                        "criterionIds": [
                            item.criterion_id
                            for item in self._execution_state().workflow_request.criteria
                        ],
                    }
                    for index, path in enumerate(
                        self._execution_state().locked_write_paths,
                        start=1,
                    )
                ]
            order_raw = {
                "orderVersion": 1,
                "orderId": (
                    f"order_{assignment.assignment_id}_{assignment.attempt_id}"
                ),
                "assignmentId": assignment.assignment_id,
                "baseDigest": self._execution_state().transaction.base_digest,
                "allowedPaths": list(self._execution_state().locked_write_paths),
                "steps": raw_steps,
                "criterionIds": [
                    item.criterion_id
                    for item in self._execution_state().workflow_request.criteria
                ],
                "verificationRequests": [
                    " ".join(check.argv)
                    for check in self._execution_state().workflow_request.verification_checks
                ],
            }
            try:
                self._execution_state().implementation_order = (
                    parse_implementation_order(
                        executor_assignment,
                        order_raw,
                    )
                )
                self._execution_state().artifact_store.publish(
                    self._execution_state().implementation_order.order_id,
                    self._execution_state().implementation_order.to_dict(),
                    authority=self._execution_state().scheduler_artifact_authority,
                )
                executor_assignment = _build_worker_assignment(
                    self._execution_state().workflow_request,
                    assignment,
                    context=self._execution_state().context,
                    agent_budget=self._execution_state().agent_budget,
                    role="executor",
                    mode="implement",
                    objective=self._execution_state().task_prompt,
                    input_refs=(
                        *executor_input_refs,
                        self._execution_state().implementation_order.order_id,
                    ),
                )
                self._execution_state().implementation_order = (
                    parse_implementation_order(
                        executor_assignment,
                        order_raw,
                    )
                )
            except Exception:
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    error_code="implementation_order_invalid",
                )
        effective_plan = self._execution_state().plan_result or ToolResult(
            ok=True,
            output=(
                "Execute the immutable WorkflowRequest.v1 scope and "
                "acceptance criteria without expanding either."
            ),
        )
        plan_text = (
            effective_plan.output[:12000]
            if not self._execution_state().workflow_request.structured
            else (
                "The locked ImplementationOrder below is the sole "
                "executable plan authority."
            )
        )
        executor_result_key: str | None = None
        executor_assignment_channel: AssignmentArtifactChannel | None = None
        executor_channel = getattr(
            self._execution_state().child_context, "_subagent_mailbox", None
        )
        if (
            executor_assignment is not None
            and self._execution_state().implementation_order is not None
        ):
            executor_result_key = _assignment_result_key(assignment, "exec")
            assignment_artifact_id = _assignment_artifact_id(assignment)
            self._execution_state().artifact_store.publish(
                assignment_artifact_id,
                _assignment_artifact_document(
                    assignment,
                    executor_assignment,
                ),
                authority=self._execution_state().scheduler_artifact_authority,
            )
            executor_channel = (
                self._execution_state().artifact_store.create_assignment_channel(
                    readable_keys=(
                        *executor_assignment.input_refs,
                        assignment_artifact_id,
                    ),
                    write_key=executor_result_key,
                )
            )
            executor_assignment_channel = executor_channel
        executor_context = replace(
            self._execution_state().child_context,
            _subagent_mailbox=executor_channel,
            _logical_agent_role="executor",
            _logical_agent_scope=(
                executor_assignment.scope
                if executor_assignment is not None
                # Legacy requests carry no structured Assignment, so the
                # fallback is least-privilege: full snapshot read, no writes.
                # Legacy change work must migrate to WorkflowRequest.v1; the
                # mandatory forbidden set still applies on top regardless.
                else {
                    "read_roots": (".",),
                    "write_paths": (),
                    "forbidden_paths": (".git",),
                }
            ),
            _logical_agent_workspace_root=(
                str(
                    self._execution_state().transaction.cwd
                    if self._execution_state().workflow_request.structured
                    else self._execution_state().transaction.workspace_root
                )
                if self._execution_state().transaction is not None
                else self._execution_state().context.cwd
            ),
        )
        findings_notes = _analyst_findings_notes(
            self._execution_state().analyst_report
            if self._execution_state().workflow_request.structured
            else None
        )
        self._execution_state().execute_result = self.run_assigned_worker(
            {
                "description": f"execute:{self._execution_state().description}",
                "agent_type": "general",
                "_subagent_id": assignment.agent_run_id,
                "prompt": (
                    "Implement the task below according to the approved plan. "
                    "Read only the Assignment artifacts made available to "
                    "you. Make the changes, record any blocked or failed "
                    "step honestly, and never claim deterministic verification. "
                    "End with <final> plus a concise summary.\n\n"
                    f"ORIGINAL TASK:\n{self._execution_state().task_prompt}\n\n"
                    f"PLAN:\n{plan_text}"
                    + (
                        "\n\nIMPLEMENTATION REPAIR REQUEST:\n"
                        + json.dumps(repair_request, ensure_ascii=False, sort_keys=True)
                        + "\nRepair the concrete failed criteria using the referenced "
                        "frozen feedback. Preserve the original scope and acceptance "
                        "checks; do not suppress tests or claim prior verification. "
                        "If the request cannot be repaired within this order, report blocked."
                        if repair_request is not None else ""
                    )
                    + (
                        "\n\n" + findings_notes
                        if findings_notes
                        else ""
                    )
                    + (
                        # Scoped route: the fan-out survey reaches the only
                        # Worker; on the full chain the Analyst already
                        # consumed it upstream.
                        self._execution_state().research_notes
                        if self._execution_state().workflow_request.structured
                        and self._execution_state().research_notes
                        and self._execution_state().analyst_report is None
                        else ""
                    )
                    + (
                        "\n\nEXECUTOR ASSIGNMENT:\n"
                        + json.dumps(
                            executor_assignment.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nLOCKED IMPLEMENTATION ORDER:\n"
                        + json.dumps(
                            self._execution_state().implementation_order.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        if executor_assignment is not None
                        and self._execution_state().implementation_order is not None
                        else ""
                    )
                ),
                **(
                    {
                        "_result_key": executor_result_key,
                        "_result_instruction": _role_result_instruction(
                            executor_assignment,
                            result_key=executor_result_key,
                            implementation_order=self._execution_state().implementation_order,
                        ),
                    }
                    if executor_assignment is not None
                    and self._execution_state().implementation_order is not None
                    and executor_result_key is not None
                    else {}
                ),
            },
            executor_context,
            executor_assignment_channel,
        )
        _raise_task_deadline(self._execution_state().context)
        if (
            self._execution_state().mailbox is not None
            and not self._execution_state().workflow_request.structured
        ):
            try:
                self._execution_state().mailbox.write(
                    "workflow_result",
                    self._execution_state().execute_result.output,
                    author="workflow",
                )
            except Exception:  # noqa: BLE001 - legacy note is optional
                pass
        if not self._execution_state().execute_result.ok:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="execute_phase_failed",
            )
        if self._execution_state().transaction is None:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="workflow_transaction_missing",
            )
        executor_declaration: object | None = None
        if (
            executor_assignment is not None
            and self._execution_state().implementation_order is not None
        ):
            try:
                if executor_result_key is None:
                    raise ValueError("Executor result key is unavailable")
                executor_declaration = _read_role_result_payload(
                    self._execution_state().artifact_store,
                    executor_result_key,
                )
            except Exception:
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    error_code="executor_declaration_invalid",
                )
            declaration_status = (
                executor_declaration.get("status")
                if isinstance(executor_declaration, dict)
                else None
            )
            if declaration_status != "completed":
                try:
                    self._execution_state().execution_artifact = parse_executor_declaration(
                        executor_assignment,
                        executor_declaration,
                        implementation_order=self._execution_state().implementation_order,
                        scheduler_change_set=None,
                    )
                except Exception:
                    return StageResult(
                        status="failed",
                        artifact_id=_stage_receipt_artifact_id(assignment),
                        error_code="executor_declaration_invalid",
                    )
                self._execution_state().execution_artifact_id = (
                    f"artifact_{assignment.assignment_id}_"
                    f"{assignment.attempt_id}_execution"
                )
                self._execution_state().artifact_store.publish(
                    self._execution_state().execution_artifact_id,
                    self._execution_state().execution_artifact,
                    authority=self._execution_state().scheduler_artifact_authority,
                )
                return StageResult(
                    status=str(self._execution_state().execution_artifact["status"]),
                    artifact_id=self._execution_state().execution_artifact_id,
                    error_code=f"executor_{self._execution_state().execution_artifact['status']}",
                )
        try:
            self._execution_state().frozen_workspace_change = self._execution_state().transaction.freeze(
                allowed_paths=self._execution_state().locked_write_paths,
                paths_relative_to_cwd=self._execution_state().workflow_request.structured,
            )
            self._execution_state().frozen_change_set = (
                self._execution_state().frozen_workspace_change.change_set_document
            )
            if repair_request is not None and (
                self._execution_state().frozen_change_set["patchDigest"]
                == repair_request["previousPatchDigest"]
            ):
                return StageResult(
                    status="failed", artifact_id=_stage_receipt_artifact_id(assignment),
                    error_code="implementation_repair_no_change",
                )
        except _WorkflowIsolationError:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="change_set_invalid",
            )
        artifact_id = (
            f"artifact_{assignment.assignment_id}_{assignment.attempt_id}_execution"
        )
        if self._execution_state().workflow_request.structured:
            try:
                self._execution_state().change_diff_artifact = self._execution_state().frozen_workspace_change.diff_artifact_document
                self._execution_state().change_diff_artifact_id = (
                    f"artifact_{assignment.assignment_id}_"
                    f"{assignment.attempt_id}_frozen_diff"
                )
                self._execution_state().artifact_store.publish(
                    self._execution_state().change_diff_artifact_id,
                    self._execution_state().change_diff_artifact,
                    authority=self._execution_state().scheduler_artifact_authority,
                )
            except Exception:
                return StageResult(
                    status="failed",
                    artifact_id=artifact_id,
                    error_code="change_diff_artifact_invalid",
                )
        if (
            executor_assignment is not None
            and self._execution_state().implementation_order is not None
        ):
            try:
                self._execution_state().execution_artifact = parse_executor_declaration(
                    executor_assignment,
                    executor_declaration,
                    implementation_order=self._execution_state().implementation_order,
                    scheduler_change_set=self._execution_state().frozen_change_set,
                )
            except Exception:
                return StageResult(
                    status="failed",
                    artifact_id=artifact_id,
                    error_code="execution_result_invalid",
                )
            self._execution_state().execution_artifact_id = artifact_id
            self._execution_state().artifact_store.publish(
                self._execution_state().execution_artifact_id,
                self._execution_state().execution_artifact,
                authority=self._execution_state().scheduler_artifact_authority,
            )
        return StageResult(
            status="completed",
            artifact_id=artifact_id,
            patch_digest=str(self._execution_state().frozen_change_set["patchDigest"]),
        )

    def _run_reviewer(self, assignment) -> StageResult:
        if (
            self._execution_state().profile.human_confirmation_required
            and not _human_approval_is_current(
                self._execution_state().human_approval_document
            )
        ):
            return StageResult(
                status="blocked",
                artifact_id=(
                    self._execution_state().human_approval_artifact_id
                    or _stage_receipt_artifact_id(assignment)
                ),
                requires_human=True,
                error_code="human_approval_expired",
            )
        if (
            self._execution_state().execute_result is None
            or self._execution_state().verification_result is None
            or (
                self._execution_state().workflow_request.structured
                and (
                    self._execution_state().execution_artifact_id is None
                    or self._execution_state().verification_artifact_id is None
                )
            )
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="review_input_missing",
            )
        if self._execution_state().workflow_request.structured:
            reviewer_input_refs = tuple(
                ref
                for ref in (
                    self._execution_state().analyst_artifact_id,
                    self._execution_state().workflow_request_artifact_id,
                    *self._execution_state().analyst_evidence_ids,
                    (
                        self._execution_state().implementation_order.order_id
                        if self._execution_state().implementation_order is not None
                        else None
                    ),
                    self._execution_state().execution_artifact_id,
                    self._execution_state().change_diff_artifact_id,
                    self._execution_state().verification_artifact_id,
                    *self._execution_state().verification_evidence_ids,
                    self._execution_state().acceptance_evidence_matrix_id,
                )
                if ref is not None
            )
            reviewer_assignment = _build_worker_assignment(
                self._execution_state().workflow_request,
                assignment,
                context=self._execution_state().context,
                agent_budget=self._execution_state().agent_budget,
                role="reviewer",
                mode="change_review",
                objective=self._execution_state().task_prompt,
                input_refs=reviewer_input_refs,
                write_paths=self._execution_state().locked_write_paths,
            )
            result_key = _assignment_result_key(assignment, "review")
            assignment_artifact_id = _assignment_artifact_id(assignment)
            self._execution_state().artifact_store.publish(
                assignment_artifact_id,
                _assignment_artifact_document(
                    assignment,
                    reviewer_assignment,
                ),
                authority=self._execution_state().scheduler_artifact_authority,
            )
            channel = self._execution_state().artifact_store.create_assignment_channel(
                readable_keys=(*reviewer_input_refs, assignment_artifact_id),
                write_key=result_key,
            )
            channel = _RepairingRoleResultChannel(channel)
            reviewer_context = replace(
                self._execution_state().child_context,
                _subagent_mailbox=channel,
                _logical_agent_role="reviewer",
                _logical_agent_scope=reviewer_assignment.scope,
                _logical_agent_workspace_root=(
                    str(self._execution_state().transaction.cwd)
                    if self._execution_state().transaction is not None
                    else self._execution_state().context.cwd
                ),
            )
            self._execution_state().review_result = self.run_assigned_worker(
                {
                    "description": f"reviewer:{self._execution_state().description}",
                    "agent_type": "plan",
                    "_subagent_id": assignment.agent_run_id,
                    "prompt": (
                        "Inspect the frozen Change Set against every criterion. "
                        "Do not modify files and do not override deterministic "
                        "verification. For every satisfied criterion, cite the "
                        "scheduler-owned AcceptanceEvidenceMatrix, its mapped "
                        "verification evidence, and the frozen diff.\n\nASSIGNMENT:\n"
                        + json.dumps(
                            reviewer_assignment.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nEXECUTION RESULT:\n"
                        + json.dumps(
                            self._execution_state().execution_artifact
                            or self._execution_state().frozen_change_set
                            or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nLOCKED IMPLEMENTATION ORDER:\n"
                        + json.dumps(
                            (
                                self._execution_state().implementation_order.to_dict()
                                if self._execution_state().implementation_order
                                is not None
                                else {}
                            ),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nFROZEN DIFF:\n"
                        + json.dumps(
                            self._execution_state().change_diff_artifact or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nVERIFICATION RESULT:\n"
                        + json.dumps(
                            self._execution_state().verification_result,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                    "_result_key": result_key,
                    "_result_instruction": _role_result_instruction(
                        reviewer_assignment,
                        result_key=result_key,
                        previous_error=(
                            self._execution_state().role_result_parse_errors.get(
                                "reviewer"
                            )
                        ),
                    ),
                },
                reviewer_context,
                channel,
            )
            _raise_task_deadline(self._execution_state().context)
            if not self._execution_state().review_result.ok:
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    patch_digest=str(
                        self._execution_state().verification_result["patchDigest"]
                    ),
                    retryable=True,
                    error_code="review_phase_failed",
                )
            try:
                review_payload, review_repaired = (
                    _read_role_result_payload_with_repair(
                        self._execution_state().artifact_store,
                        result_key,
                    )
                )
                review_verdict = parse_role_result(
                    reviewer_assignment,
                    review_payload,
                )
            except Exception as exc:
                self._execution_state().role_result_parse_errors["reviewer"] = (
                    _role_result_error_feedback(exc)
                )
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    patch_digest=str(
                        self._execution_state().verification_result["patchDigest"]
                    ),
                    retryable=True,
                    error_code="review_result_invalid",
                )
            self._execution_state().role_result_parse_errors.pop("reviewer", None)
            if review_repaired or channel.repaired:
                _publish_role_result_repair_receipt(
                    self._execution_state(),
                    assignment,
                    "reviewer",
                )
            if (
                self._execution_state().acceptance_evidence_matrix_id is None
                or self._execution_state().acceptance_evidence_matrix is None
                or self._execution_state().change_diff_artifact_id is None
            ):
                return StageResult(
                    status="failed",
                    artifact_id=result_key,
                    error_code="acceptance_evidence_matrix_missing",
                )
            matrix_by_criterion = {
                str(item["criterionId"]): item
                for item in self._execution_state().acceptance_evidence_matrix[
                    "criteria"
                ]
                if isinstance(item, dict)
            }
            for criterion_result in review_verdict["criteria"]:
                if criterion_result["status"] != "satisfied":
                    continue
                matrix_entry = matrix_by_criterion.get(
                    str(criterion_result["criterionId"])
                )
                if not isinstance(matrix_entry, dict):
                    return StageResult(
                        status="failed",
                        artifact_id=result_key,
                        error_code="review_evidence_binding_invalid",
                    )
                required_refs = {
                    self._execution_state().acceptance_evidence_matrix_id,
                    self._execution_state().change_diff_artifact_id,
                    *(str(ref) for ref in matrix_entry.get("evidenceRefs", [])),
                }
                if not required_refs.issubset(set(criterion_result["evidenceRefs"])):
                    return StageResult(
                        status="failed",
                        artifact_id=result_key,
                        error_code="review_evidence_binding_invalid",
                    )
            self._execution_state().typed_review = {
                "reviewVersion": 1,
                "verdict": review_verdict["verdict"],
                "blockingFindings": [
                    item["statement"] for item in review_verdict["blockingFindings"]
                ],
                "warnings": [item["statement"] for item in review_verdict["warnings"]],
            }
            diff_is_complete = bool(
                self._execution_state().change_diff_artifact
                and self._execution_state().change_diff_artifact.get("complete") is True
            )
            return StageResult(
                status="completed",
                artifact_id=result_key,
                patch_digest=str(review_verdict["patchDigest"]),
                approved=(review_verdict["verdict"] == "approved" and diff_is_complete),
                repairable=(
                    diff_is_complete and self._repair_budget_available()
                    and review_supports_implementation_repair(review_verdict)
                ),
                error_code=(None if diff_is_complete else "review_evidence_truncated"),
            )
        self._execution_state().review_result = self._execution_state().run_subagent(
            {
                "description": f"review:{self._execution_state().description}",
                "agent_type": "plan",
                "_subagent_id": assignment.agent_run_id,
                "prompt": (
                    "Review the completed work below against the original "
                    "task and deterministic Verification Result. Read "
                    "workflow_plan and workflow_result when available. Do "
                    "not modify files. Before your final response, call "
                    "subagent_note_write with review verdict key "
                    f"`{self._execution_state().review_key}` and content containing ONLY a JSON "
                    "object with this exact shape: "
                    '{"reviewVersion":1,"verdict":"approved|changes_required|inconclusive",'
                    '"blockingFindings":["..."],"warnings":["..."]}. '
                    "Use approved only when there are no blocking findings; "
                    "a model verdict cannot override failed verification. "
                    "End with <final>.\n\n"
                    f"ORIGINAL TASK:\n{self._execution_state().task_prompt}\n\n"
                    "DETERMINISTIC VERIFICATION:\n"
                    f"{json.dumps(self._execution_state().verification_result, sort_keys=True)}\n\n"
                    "IMPLEMENTATION SUMMARY:\n"
                    f"{self._execution_state().execute_result.output[:12000]}"
                ),
            },
            self._execution_state().child_context,
        )
        _raise_task_deadline(self._execution_state().context)
        parsed_review, review_error = _read_workflow_review(
            self._execution_state().mailbox, self._execution_state().review_key
        )
        self._execution_state().typed_review = (
            parsed_review
            or _workflow_failure_review(review_error or "review_verdict_invalid")
        )
        return StageResult(
            status="completed"
            if self._execution_state().review_result.ok
            else "failed",
            artifact_id=_stage_receipt_artifact_id(assignment),
            patch_digest=str(
                self._execution_state().verification_result["patchDigest"]
            ),
            approved=self._execution_state().typed_review["verdict"] == "approved",
            error_code=None
            if self._execution_state().review_result.ok
            else "review_phase_failed",
        )

    def _run_adjudicator(self, assignment) -> StageResult:
        """Resolve the frozen conflict a blocked Worker stage escalated."""
        state = self._execution_state()
        conflict_package = state.pending_conflict_package
        conflict_artifact_id = state.pending_conflict_artifact_id
        if not isinstance(conflict_package, Mapping) or not isinstance(
            conflict_artifact_id, str
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="adjudication_conflict_missing",
            )
        conflict_id = str(conflict_package.get("conflictId"))
        # The closed ConflictPackage carries references, not evidence bytes.
        # Grant its referenced frozen inputs explicitly, including the Analyst
        # handoff that explains the claims behind its evidence snapshots.
        adjudication_input_artifacts = tuple(
            dict.fromkeys(
                (
                    conflict_artifact_id,
                    *(
                        ref
                        for claim in conflict_package["claims"]
                        for ref in claim["evidenceRefs"]
                    ),
                    *(
                        (state.analyst_artifact_id,)
                        if state.analyst_artifact_id is not None
                        else ()
                    ),
                )
            )
        )
        adjudicator_assignment = _build_worker_assignment(
            state.workflow_request,
            assignment,
            context=state.context,
            agent_budget=state.agent_budget,
            role="adjudicator",
            mode="resolve_conflict",
            objective=(
                "Resolve the frozen ConflictPackage: weigh each claim's "
                "evidence and return one AdjudicationVerdict.v1."
            ),
            # The contract binds the verdict to this exact conflict id.
            input_refs=(conflict_id, *adjudication_input_artifacts),
        )
        result_key = _assignment_result_key(assignment, "adjudicator")
        assignment_artifact_id = _assignment_artifact_id(assignment)
        state.artifact_store.publish(
            assignment_artifact_id,
            _assignment_artifact_document(assignment, adjudicator_assignment),
            authority=state.scheduler_artifact_authority,
        )
        channel = state.artifact_store.create_assignment_channel(
            readable_keys=(
                *adjudication_input_artifacts,
                assignment_artifact_id,
            ),
            write_key=result_key,
        )
        channel = _RepairingRoleResultChannel(channel)
        adjudicator_context = replace(
            state.child_context,
            _subagent_mailbox=channel,
            _logical_agent_role="adjudicator",
            _logical_agent_scope=adjudicator_assignment.scope,
            _logical_agent_workspace_root=(
                str(state.transaction.cwd)
                if state.transaction is not None
                else state.context.cwd
            ),
        )
        state.plan_result = self.run_assigned_worker(
            {
                "description": (
                    f"adjudicator:{state.description}"
                ),
                "agent_type": "plan",
                "_subagent_id": assignment.agent_run_id,
                "prompt": (
                    "Complete only this immutable Adjudicator Assignment. "
                    "Weigh each claim's frozen evidence; you cannot modify "
                    "files or expand the question. Select the claim the "
                    "evidence supports, resolve every claim you reject, and "
                    "bind each resolution to ConflictPackage evidence "
                    "refs.\n\nASSIGNMENT:\n"
                    + json.dumps(
                        adjudicator_assignment.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n\nCONFLICT PACKAGE:\n"
                    + json.dumps(
                        conflict_package,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                ),
                "_result_key": result_key,
                "_result_instruction": _role_result_instruction(
                    adjudicator_assignment,
                    result_key=result_key,
                    previous_error=(
                        state.role_result_parse_errors.get("adjudicator")
                    ),
                ),
            },
            adjudicator_context,
            channel,
        )
        if not state.plan_result.ok:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="adjudication_worker_failed",
            )
        try:
            verdict_payload, verdict_repaired = _read_role_result_payload_with_repair(
                state.artifact_store,
                result_key,
            )
            verdict = parse_role_result(
                adjudicator_assignment,
                verdict_payload,
                conflict_package=conflict_package,
            )
        except Exception as exc:
            state.role_result_parse_errors["adjudicator"] = (
                _role_result_error_feedback(exc)
            )
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                retryable=True,
                error_code="adjudication_result_invalid",
            )
        state.role_result_parse_errors.pop("adjudicator", None)
        verdict_artifact_id = (
            "artifact_adjudication_"
            + hashlib.sha256(
                (
                    f"{assignment.assignment_id}\0{assignment.attempt_id}"
                ).encode("utf-8")
            ).hexdigest()[:32]
        )
        state.artifact_store.publish(
            verdict_artifact_id,
            verdict,
            authority=state.scheduler_artifact_authority,
        )
        state.adjudication_verdict = verdict
        if verdict_repaired or channel.repaired:
            _publish_role_result_repair_receipt(state, assignment, "adjudicator")
        return StageResult(
            status="completed",
            artifact_id=verdict_artifact_id,
            conflict_verdict=verdict,
        )

    def run_verifier(self, assignment) -> StageResult:
        self._execution_state().verification_evidence_ids.clear()
        if (
            self._execution_state().profile.human_confirmation_required
            and not _human_approval_is_current(
                self._execution_state().human_approval_document
            )
        ):
            return StageResult(
                status="blocked",
                artifact_id=(
                    self._execution_state().human_approval_artifact_id
                    or _stage_receipt_artifact_id(assignment)
                ),
                requires_human=True,
                error_code="human_approval_expired",
            )
        if (
            self._execution_state().transaction is None
            or self._execution_state().frozen_workspace_change is None
            or self._execution_state().frozen_change_set is None
            or self._execution_state().executor_assignment_id is None
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="change_set_missing",
            )
        patch_digest = self._execution_state().frozen_workspace_change.patch_digest
        if not self._execution_state().requested_checks:
            return StageResult(
                status="inconclusive",
                artifact_id=_stage_receipt_artifact_id(assignment),
                patch_digest=patch_digest,
                error_code="verification_checks_missing",
            )
        from minicode.permissions import PermissionDeniedError, PermissionManager

        permission_cwd = str(self._execution_state().transaction.source_cwd)
        verification_permissions = getattr(
            self._execution_state().context, "permissions", None
        )
        if verification_permissions is None:
            verification_permissions = PermissionManager(
                permission_cwd,
                prompt=None,
            )
        verifier_permission_gate = (
            verification_permissions
            if self._execution_state().workflow_request.structured
            else None
        )
        try:

            def publish_verification_evidence(
                artifact_id: str,
                evidence: dict[str, object],
            ) -> None:
                self._execution_state().artifact_store.publish(
                    artifact_id,
                    evidence,
                    authority=self._execution_state().scheduler_artifact_authority,
                )
                self._execution_state().verification_evidence_ids.append(artifact_id)

            verifier = _deterministic_verifier_for_context(
                self._execution_state().context,
                trusted_git_executable=self._execution_state().transaction.trusted_git_executable,
                untrusted_executable_root=str(
                    self._execution_state().transaction.source_root
                ),
            )
            isolated_checks: list[dict[str, object]] = []
            isolated_statuses: list[str] = []
            evidence_ids_by_index: list[list[str]] = [
                [] for _ in self._execution_state().requested_checks
            ]
            verification_id: str | None = None
            # Every check receives a fresh clone and private HOME. A test,
            # build or linter can therefore never influence the bytes seen
            # by a later gate.
            # Every check receives its own fresh clone, so independent
            # checks run concurrently in a bounded pool; results, evidence
            # ids, and failure propagation are re-sequenced into request
            # order afterwards, preserving the serial contract exactly.
            requested_checks = tuple(self._execution_state().requested_checks)

            def run_one_check(check):
                with (
                    self._execution_state().frozen_workspace_change.verification_workspace(
                        read_roots=(
                            (".",)
                            if check.kind == "oracle"
                            else self._execution_state().workflow_request.scope.read_roots
                        ),
                        forbidden_paths=self._execution_state().workflow_request.scope.forbidden_paths,
                        retain_git_metadata=check.kind == "oracle",
                    ) as verification_cwd
                ):
                    per_check_evidence: list[str] = []

                    def per_check_sink(artifact_id, evidence):
                        # Publish-only: the shared evidence-id list is
                        # re-sequenced after the pool joins, in request
                        # order, exactly like the serial loop produced.
                        self._execution_state().artifact_store.publish(
                            artifact_id,
                            evidence,
                            authority=(
                                self._execution_state()
                                .scheduler_artifact_authority
                            ),
                        )
                        per_check_evidence.append(artifact_id)

                    partial = verifier.run(
                        checks=(check,),
                        cwd=str(verification_cwd),
                        workflow_id=self._execution_state().workflow_id,
                        task_id=self._execution_state().task_id,
                        execution_assignment_id=self._execution_state().executor_assignment_id,
                        patch_digest=patch_digest,
                        permissions=verifier_permission_gate,
                        permission_cwd=permission_cwd,
                        deadline_monotonic=getattr(
                            self._execution_state().context,
                            "_task_deadline_monotonic",
                            None,
                        ),
                        cancellation_checkpoint=lambda: _raise_task_deadline(
                            self._execution_state().context
                        ),
                        evidence_sink=per_check_sink,
                    )
                    # Validate the port's actual identity before aggregation;
                    # rebuilding an envelope must not rebind an old passing
                    # check result to this revision's assignment and digest.
                    partial = parse_verification_result(
                        {key: value for key, value in partial.items() if key != "resultType"},
                        workflow_id=self._execution_state().workflow_id,
                        task_id=self._execution_state().task_id,
                        execution_assignment_id=self._execution_state().executor_assignment_id,
                        patch_digest=patch_digest,
                        required_check_ids=(check.check_id,),
                    )
                return partial, per_check_evidence

            partials: list[object] = [None] * len(requested_checks)
            check_errors: list[BaseException | None] = [None] * len(requested_checks)
            worker_limit = min(_PARALLEL_VERIFICATION_WORKERS, len(requested_checks))
            if worker_limit > 1:
                with ThreadPoolExecutor(max_workers=worker_limit) as pool:
                    futures = {
                        pool.submit(run_one_check, check): index
                        for index, check in enumerate(requested_checks)
                    }
                    for future in as_completed(futures):
                        index = futures[future]
                        try:
                            partials[index], evidence_ids_by_index[index] = (
                                future.result()
                            )
                        except BaseException as exc:  # noqa: BLE001 - re-raised in order
                            check_errors[index] = exc
            else:
                for index, check in enumerate(requested_checks):
                    try:
                        partials[index], evidence_ids_by_index[index] = (
                            run_one_check(check)
                        )
                    except BaseException as exc:  # noqa: BLE001 - re-raised below
                        check_errors[index] = exc
            for error in check_errors:
                if error is not None:
                    raise error
            for partial in partials:
                verification_id = str(partial["verificationId"])
                isolated_checks.extend(partial["checks"])
                isolated_statuses.append(str(partial["status"]))
            for index in range(len(requested_checks)):
                self._execution_state().verification_evidence_ids.extend(
                    evidence_ids_by_index[index]
                )
            aggregate_status = (
                "failed"
                if "failed" in isolated_statuses
                else "inconclusive"
                if "inconclusive" in isolated_statuses
                else "passed"
            )
            self._execution_state().verification_result = parse_verification_result(
                {
                    "verificationVersion": 1,
                    "verificationId": verification_id,
                    "workflowId": self._execution_state().workflow_id,
                    "taskId": self._execution_state().task_id,
                    "executionAssignmentId": self._execution_state().executor_assignment_id,
                    "patchDigest": patch_digest,
                    "status": aggregate_status,
                    "checks": isolated_checks,
                },
                workflow_id=self._execution_state().workflow_id,
                task_id=self._execution_state().task_id,
                execution_assignment_id=self._execution_state().executor_assignment_id,
                patch_digest=patch_digest,
                required_check_ids=tuple(
                    check.check_id for check in self._execution_state().requested_checks
                ),
            )
        except (
            TurnCancellationRequested,
            ToolExecutionAbandoned,
            AgentTurnDeadlineExceeded,
            _TaskDeadlineExceeded,
            VerificationDeadlineExceeded,
        ):
            raise
        except PermissionDeniedError:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                patch_digest=patch_digest,
                error_code="verification_permission_denied",
            )
        except Exception:  # noqa: BLE001 - verifier infrastructure fails closed
            return StageResult(
                status="inconclusive",
                artifact_id=_stage_receipt_artifact_id(assignment),
                patch_digest=patch_digest,
                retryable=False,
                error_code="verifier_unavailable",
            )
        verification_status = str(self._execution_state().verification_result["status"])
        if self._execution_state().workflow_request.structured:
            referenced_evidence = tuple(
                str(check["artifactRef"])
                for check in self._execution_state().verification_result["checks"]
            )
            if referenced_evidence != tuple(
                self._execution_state().verification_evidence_ids
            ) or any(
                self._execution_state().artifact_store.read(artifact_id) is None
                for artifact_id in referenced_evidence
            ):
                return StageResult(
                    status="inconclusive",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    patch_digest=patch_digest,
                    retryable=False,
                    error_code="verification_evidence_missing",
                )
        self._execution_state().verification_artifact_id = (
            f"artifact_{assignment.assignment_id}_{assignment.attempt_id}_verification"
        )
        self._execution_state().artifact_store.publish(
            self._execution_state().verification_artifact_id,
            self._execution_state().verification_result,
            authority=self._execution_state().scheduler_artifact_authority,
        )
        if self._execution_state().workflow_request.structured:
            if self._execution_state().change_diff_artifact_id is None:
                return StageResult(
                    status="inconclusive",
                    artifact_id=self._execution_state().verification_artifact_id,
                    patch_digest=patch_digest,
                    error_code="frozen_diff_evidence_missing",
                )
            checks_by_id = {
                str(check["checkId"]): check
                for check in self._execution_state().verification_result["checks"]
            }
            matrix_criteria: list[dict[str, object]] = []
            for criterion in self._execution_state().workflow_request.criteria:
                mapped_check_ids = tuple(
                    check.check_id
                    for check in self._execution_state().workflow_request.verification_checks
                    if criterion.criterion_id in check.criterion_ids
                )
                mapped_evidence = [
                    str(checks_by_id[check_id]["artifactRef"])
                    for check_id in mapped_check_ids
                    if check_id in checks_by_id
                    and checks_by_id[check_id]["status"] == "passed"
                ]
                if len(mapped_evidence) != len(mapped_check_ids):
                    return StageResult(
                        status="inconclusive",
                        artifact_id=self._execution_state().verification_artifact_id,
                        patch_digest=patch_digest,
                        repairable=self._verification_repairable(),
                        error_code="acceptance_evidence_incomplete",
                    )
                matrix_criteria.append(
                    {
                        "criterionId": criterion.criterion_id,
                        "checkIds": list(mapped_check_ids),
                        "evidenceRefs": mapped_evidence,
                    }
                )
            self._execution_state().acceptance_evidence_matrix_id = (
                "artifact_acceptance_"
                + hashlib.sha256(
                    (
                        f"{self._execution_state().workflow_id}\0{self._execution_state().task_id}\0{patch_digest}"
                        f"\0{self._execution_state().executor_assignment_id}"
                    ).encode("utf-8")
                ).hexdigest()[:32]
            )
            self._execution_state().acceptance_evidence_matrix = {
                "matrixVersion": 1,
                "resultType": "AcceptanceEvidenceMatrix.v1",
                "workflowId": self._execution_state().workflow_id,
                "taskId": self._execution_state().task_id,
                "patchDigest": patch_digest,
                "verificationArtifactRef": self._execution_state().verification_artifact_id,
                "frozenDiffRef": self._execution_state().change_diff_artifact_id,
                "criteria": matrix_criteria,
            }
            self._execution_state().artifact_store.publish(
                self._execution_state().acceptance_evidence_matrix_id,
                self._execution_state().acceptance_evidence_matrix,
                authority=self._execution_state().scheduler_artifact_authority,
            )
        return StageResult(
            status=(
                "completed" if verification_status == "passed" else verification_status
            ),
            artifact_id=self._execution_state().verification_artifact_id,
            patch_digest=patch_digest,
            retryable=False,
            repairable=self._verification_repairable(),
            error_code=(
                None
                if verification_status == "passed"
                else f"verification_{verification_status}"
            ),
        )

    def run_human(self, assignment) -> StageResult:
        if (
            not self._execution_state().workflow_request.structured
            or self._execution_state().transaction is None
            or self._execution_state().analyst_artifact_id is None
            or self._execution_state().analyst_report is None
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="human_approval_context_missing",
            )
        sealed_handoff = _read_role_result_payload(
            self._execution_state().artifact_store,
            self._execution_state().analyst_artifact_id,
        )
        evidence_refs = sealed_handoff.get("evidenceSnapshotRefs", [])
        if not isinstance(evidence_refs, list):
            raise _WorkflowIsolationError(
                "sealed Analyst handoff has invalid evidence references"
            )
        approval_documents: dict[str, object] = {
            self._execution_state().analyst_artifact_id: sealed_handoff,
        }
        for evidence_ref in evidence_refs:
            if not isinstance(evidence_ref, str):
                raise _WorkflowIsolationError(
                    "sealed Analyst evidence reference is invalid"
                )
            approval_documents[evidence_ref] = _read_role_result_payload(
                self._execution_state().artifact_store,
                evidence_ref,
            )
        stable_analyst_digest = human_approval_analysis_digest(
            sealed_handoff,
            approval_documents,
        )
        (
            self._execution_state().approval_request_id,
            self._execution_state().approval_request_document,
            self._execution_state().human_approval_document,
        ) = _workflow_human_approval(
            self._execution_state().context,
            self._execution_state().workflow_request,
            request_digest=self._execution_state().workflow_request_digest,
            base_digest=self._execution_state().transaction.base_digest,
            analyst_artifact_id=self._execution_state().analyst_artifact_id,
            analyst_digest=stable_analyst_digest,
        )
        if (
            self._execution_state().approval_request_id is None
            or self._execution_state().approval_request_document is None
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="human_approval_request_invalid",
            )
        request_artifact_id = (
            f"artifact_{self._execution_state().approval_request_id}_request"
        )
        self._execution_state().artifact_store.publish(
            request_artifact_id,
            self._execution_state().approval_request_document,
            authority=self._execution_state().scheduler_artifact_authority,
        )
        if self._execution_state().human_approval_document is None:
            return StageResult(
                status="blocked",
                artifact_id=request_artifact_id,
                requires_human=True,
                error_code="human_approval_required",
            )
        self._execution_state().human_approval_artifact_id = (
            f"artifact_{self._execution_state().approval_request_id}"
        )
        self._execution_state().artifact_store.publish(
            self._execution_state().human_approval_artifact_id,
            self._execution_state().human_approval_document,
            authority=self._execution_state().scheduler_artifact_authority,
        )
        self._execution_state().profile = replace(
            self._execution_state().profile,
            human_confirmation_granted=True,
            human_approval_id=self._execution_state().approval_request_id,
        )
        return StageResult(
            status="completed",
            artifact_id=self._execution_state().human_approval_artifact_id,
            approved=True,
        )

    def commit_change(self, request) -> bool:
        if (
            self._execution_state().transaction is None
            or self._execution_state().frozen_workspace_change is None
        ):
            return False
        if (
            self._execution_state().profile.human_confirmation_required
            and not _human_approval_is_current(
                self._execution_state().human_approval_document
            )
        ):
            self._execution_state().commit_error = _WorkflowIsolationError(
                "human approval expired before commit"
            )
            return False
        try:
            self._execution_state().frozen_workspace_change.promote(
                approved_patch_digest=request.patch_digest,
            )
        except BaseException as exc:
            # Promotion is the commit linearization point. Cleanup, lock
            # release, or observer failures after it may degrade finalization,
            # but can never turn an applied parent change back into "not
            # committed" or suppress its mandatory delivery.
            if self._execution_state().transaction.parent_promotion_completed:
                return True
            if self._execution_state().transaction.parent_promotion_unknown:
                raise _WorkflowCommitStateUnknown(
                    "parent promotion state is unknown"
                ) from exc
            if isinstance(exc, _WorkflowIsolationError):
                self._execution_state().commit_error = exc
                return False
            raise
        return True

    def prepare_commit(self, request) -> bool:
        """Seal commit authority before the parent workspace can be changed."""
        if not self._execution_state().workflow_request.structured:
            self._execution_state().commit_ready_status = "not_applicable"
            return True
        if (
            self._execution_state().transaction is None
            or self._execution_state().verification_artifact_id is None
            or not request.artifact_ids
        ):
            self._execution_state().commit_ready_status = "projection_failed"
            return False
        review_artifact_id = request.artifact_ids[-1]

        def artifact_digest(artifact_id: str) -> str:
            artifact = self._execution_state().artifact_store.read(artifact_id)
            if artifact is None:
                raise ValueError("commit authority artifact is missing")
            return (
                "sha256:" + hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
            )

        try:
            artifact_index = [
                {
                    "artifactId": artifact_id,
                    "contentDigest": artifact_digest(artifact_id),
                }
                for artifact_id in self._execution_state().artifact_store.list_artifact_ids()
            ]
            artifact_set_digest = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        artifact_index,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
            )
            commit_ready = project_scheduler_commit_ready(
                workflow_id=request.workflow_id,
                task_id=request.task_id,
                rule_id=request.rule_id,
                request_digest=self._execution_state().workflow_request_digest,
                base_digest=self._execution_state().transaction.base_digest,
                patch_digest=request.patch_digest,
                verification_digest=artifact_digest(
                    self._execution_state().verification_artifact_id
                ),
                review_digest=artifact_digest(review_artifact_id),
                approval_digest=(
                    artifact_digest(self._execution_state().human_approval_artifact_id)
                    if self._execution_state().human_approval_artifact_id is not None
                    else None
                ),
                artifact_set_digest=artifact_set_digest,
            )
        except Exception:  # noqa: BLE001 - malformed authority fails closed
            self._execution_state().commit_ready_status = "projection_failed"
            return False

        event_sink = getattr(self._execution_state().context, "_event_sink", None)
        recorder = getattr(
            event_sink,
            "record_scheduler_commit_ready",
            None,
        )
        final_audit_recorder = getattr(
            event_sink,
            "record_scheduler_audit",
            None,
        )
        production_enforced = (
            getattr(
                self._execution_state().context, "_central_workflow_v2_enforced", False
            )
            is True
        )
        commit_audit_waived = (
            getattr(
                self._execution_state().context, "_workflow_commit_audit_waived", False
            )
            is True
        )
        if not callable(recorder):
            # Fail closed by default: a structured workflow must not change the
            # parent workspace without a persisted commit-ready record. Only an
            # explicit non-production waiver reopens the lenient path, and it
            # is recorded as commitReadyStatus=waived in the finalization
            # status/audit projection.
            if commit_audit_waived and not production_enforced:
                self._execution_state().commit_ready_status = "waived"
                return True
            self._execution_state().commit_ready_status = "recorder_missing"
            return False
        if production_enforced and not callable(final_audit_recorder):
            self._execution_state().commit_ready_status = "recorder_missing"
            return False
        _raise_task_deadline(self._execution_state().context)
        try:
            persisted = recorder(commit_ready)
        except (
            TurnCancellationRequested,
            ToolExecutionAbandoned,
            AgentTurnDeadlineExceeded,
            _TaskDeadlineExceeded,
        ):
            raise
        except Exception:  # noqa: BLE001 - persistence gate fails closed
            self._execution_state().commit_ready_status = "persistence_failed"
            return False
        self._execution_state().commit_ready_status = (
            "persisted" if persisted is True else "persistence_failed"
        )
        _raise_task_deadline(self._execution_state().context)
        return persisted is True

    def record_transition(self, transition) -> None:
        self._execution_state().transitions.append(transition)
        artifact_id = transition.artifact_id
        if artifact_id is None:
            return
        self._execution_state().artifact_store.seal_stage_result(
            artifact_id,
            {
                "receiptVersion": 1,
                "resultType": "SchedulerStageReceipt.v1",
                "workflowId": transition.workflow_id,
                "taskId": transition.task_id,
                "assignmentId": transition.assignment_id,
                "attemptId": transition.attempt_id,
                "agentRunId": transition.agent_run_id,
                "decisionId": transition.decision_id,
                "parentDecisionId": transition.parent_decision_id,
                "ruleId": transition.rule_id,
                "stage": transition.stage.value,
                "outcome": transition.outcome,
                "errorCode": transition.error_code,
            },
            authority=self._execution_state().scheduler_artifact_authority,
        )


def _run_workflow_phases(
    input_data: dict,
    context,
    depth: int,
    *,
    subagent_id: str,
    workflow_journal: SubagentRunJournal | None,
    transaction: _WorkflowWorkspaceTransaction | None,
    run_subagent: _RunSubagent,
) -> ToolResult:
    return _WorkflowRunSession(
        input_data,
        context,
        depth,
        subagent_id=subagent_id,
        workflow_journal=workflow_journal,
        transaction=transaction,
        run_subagent=run_subagent,
    ).run()


__all__ = ("run_workflow",)
