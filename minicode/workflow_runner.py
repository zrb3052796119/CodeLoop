"""Complete centrally scheduled multi-agent workflow execution.

The task tool supplies validated input and the existing child-agent runner.
This module privately owns isolation, phase state, scheduler ports, evidence,
promotion, audit, trace and truthful terminal projection.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import stat
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import AgentTurnDeadlineExceeded
from minicode.central_scheduler import (
    CentralScheduler,
    DispatchStage,
    StageResult,
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
    parse_executor_declaration,
    parse_implementation_order,
    parse_role_result,
    parse_verification_result,
    parse_workflow_request,
)
from minicode.scheduler_artifacts import (
    AssignmentArtifactChannel,
    SchedulerArtifactStore,
)
from minicode.scheduler_audit import (
    human_approval_analysis_digest,
    project_scheduler_audit,
    project_scheduler_commit_ready,
)
from minicode.scheduler_trace import (
    SCHEDULER_TRACE_MARKER,
    project_scheduler_trace,
    render_scheduler_trace,
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
from minicode.subagent_result import (
    SUBAGENT_RESULT_MARKER,
    extract_subagent_result,
    project_subagent_result,
    render_subagent_result,
)
from minicode.subagent_types import AGENT_TYPES, MAX_AGENT_DEPTH
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
from minicode.verification_observation import project_verification
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
    if trusted_git_executable is not None:
        trusted_executables["git"] = trusted_git_executable
    return DeterministicVerifier(
        sandbox_backend=backend,
        trusted_executables=trusted_executables,
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
_WORKFLOW_OUTPUT_MAX_CHARS = 8000
_WORKFLOW_RESULT_SUMMARY_MAX_CHARS = 1000
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
    role_model_limit = {
        "analyst": AGENT_TYPES["plan"]["max_turns"],
        "executor": AGENT_TYPES["general"]["max_turns"],
        "reviewer": AGENT_TYPES["plan"]["max_turns"],
        "adjudicator": AGENT_TYPES["plan"]["max_turns"],
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
                    "Analyst evidence line range exceeds the snapshot file"
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


def _role_result_instruction(
    assignment: Assignment,
    *,
    result_key: str,
    implementation_order: ImplementationOrder | None = None,
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
    return (
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


def _bounded_workflow_section(label: str, content: str, limit: int) -> tuple[str, bool]:
    text = str(content or "")
    if len(text) <= limit:
        return f"{label}\n{text}", False
    suffix = "\n... [section truncated]"
    return f"{label}\n{text[: max(0, limit - len(suffix))]}{suffix}", True


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


def _derive_workflow_result(
    *,
    subagent_id: str,
    workflow_status: str,
    execute_result: ToolResult,
    phase_results: list[ToolResult],
    typed_review: dict[str, object],
    analyst_report: dict[str, object] | None = None,
    execution_artifact: dict[str, object] | None = None,
    verification_result: dict[str, object] | None = None,
    structured_mode: bool = False,
) -> dict[str, object]:
    """Aggregate typed phase hand-backs without trusting prose as evidence."""
    phase_reports = [
        report
        for report in (
            extract_subagent_result(result.output) for result in phase_results
        )
        if report is not None
    ]
    execute_report = extract_subagent_result(execute_result.output)
    has_scheduler_artifacts = structured_mode or any(
        item is not None
        for item in (analyst_report, execution_artifact, verification_result)
    )
    if has_scheduler_artifacts:
        canonical_findings = (
            analyst_report.get("findings", [])
            if isinstance(analyst_report, dict)
            else []
        )
        finding_statements = [
            str(item.get("statement", "")).strip()
            for item in canonical_findings
            if isinstance(item, dict) and str(item.get("statement", "")).strip()
        ]
        change_set = (
            execution_artifact.get("changeSet")
            if isinstance(execution_artifact, dict)
            else None
        )
        changed_files = (
            change_set.get("files", []) if isinstance(change_set, dict) else []
        )
        if changed_files:
            summary = (
                f"Central scheduler {workflow_status} a frozen change set for "
                f"{len(changed_files)} file(s)."
            )
        elif finding_statements:
            summary = "; ".join(finding_statements)[:_WORKFLOW_RESULT_SUMMARY_MAX_CHARS]
        else:
            summary = f"Central scheduler ended the workflow as {workflow_status}."
    else:
        summary = (
            str(execute_report["summary"])[:_WORKFLOW_RESULT_SUMMARY_MAX_CHARS]
            if execute_report is not None
            else str(execute_result.output or "workflow completed")[
                :_WORKFLOW_RESULT_SUMMARY_MAX_CHARS
            ]
        )

    files: list[dict[str, str]] = []
    seen_files: set[tuple[str, str]] = set()
    risks: list[str] = []
    source_reports = [] if has_scheduler_artifacts else phase_reports
    for report in source_reports:
        for item in report.get("files", []):
            if not isinstance(item, dict):
                continue
            pair = (str(item.get("path", "")), str(item.get("action", "unknown")))
            if not pair[0] or pair in seen_files or len(files) >= 40:
                continue
            seen_files.add(pair)
            files.append({"path": pair[0][:500], "action": pair[1]})
        for item in report.get("risks", []):
            text = str(item).strip()[:500]
            if text and text not in risks and len(risks) < 20:
                risks.append(text)
    if has_scheduler_artifacts:
        change_set = (
            execution_artifact.get("changeSet")
            if isinstance(execution_artifact, dict)
            else None
        )
        changed_files = (
            change_set.get("files", []) if isinstance(change_set, dict) else []
        )
        for item in changed_files:
            if not isinstance(item, dict):
                continue
            pair = (str(item.get("path", "")), str(item.get("action", "unknown")))
            if not pair[0] or pair in seen_files or len(files) >= 40:
                continue
            seen_files.add(pair)
            files.append({"path": pair[0][:500], "action": pair[1]})
        if isinstance(analyst_report, dict):
            if not files:
                for finding in analyst_report.get("findings", []):
                    if not isinstance(finding, dict):
                        continue
                    for evidence in finding.get("evidence", []):
                        if not isinstance(evidence, dict):
                            continue
                        pair = (str(evidence.get("path", "")), "read")
                        if not pair[0] or pair in seen_files or len(files) >= 40:
                            continue
                        seen_files.add(pair)
                        files.append({"path": pair[0][:500], "action": "read"})
            for unknown in analyst_report.get("unknowns", []):
                text = f"Unknown: {str(unknown).strip()}"[:500]
                if text != "Unknown: " and text not in risks and len(risks) < 20:
                    risks.append(text)
            for risk in analyst_report.get("risks", []):
                if not isinstance(risk, dict):
                    continue
                text = (
                    f"{str(risk.get('severity', 'unknown'))}: "
                    f"{str(risk.get('statement', '')).strip()}"
                )[:500]
                if text and text not in risks and len(risks) < 20:
                    risks.append(text)
        if isinstance(execution_artifact, dict):
            for blocker in execution_artifact.get("blockers", []):
                text = f"Executor blocker: {str(blocker).strip()}"[:500]
                if (
                    text != "Executor blocker: "
                    and text not in risks
                    and len(risks) < 20
                ):
                    risks.append(text)
    for field in ("blockingFindings", "warnings"):
        values = typed_review.get(field, [])
        if not isinstance(values, list):
            continue
        for item in values:
            text = str(item).strip()[:500]
            if text and text not in risks and len(risks) < 20:
                risks.append(text)

    verdict = str(typed_review.get("verdict", "inconclusive"))
    deterministic_status = (
        verification_result.get("status")
        if isinstance(verification_result, dict)
        else None
    )
    verification_status = (
        str(deterministic_status)
        if deterministic_status in {"passed", "failed", "inconclusive"}
        else "not_run"
        if has_scheduler_artifacts and execution_artifact is None
        else "passed"
        if workflow_status == "completed" and verdict == "approved"
        else "failed"
        if verdict == "changes_required"
        else "inconclusive"
    )
    verification_checks = (
        [
            (
                f"{str(check.get('checkId', 'check'))}: "
                f"{str(check.get('status', 'inconclusive'))} "
                f"[{str(check.get('artifactRef', 'evidence unavailable'))}]"
            )[:500]
            for check in verification_result.get("checks", [])[:20]
            if isinstance(check, dict)
        ]
        if isinstance(verification_result, dict)
        else []
        if has_scheduler_artifacts and execution_artifact is None
        else [f"workflow review verdict: {verdict}"]
    )
    report_content = json.dumps(
        {
            "resultVersion": 1,
            "summary": summary or "workflow completed without a summary",
            "files": files,
            "risks": risks,
            "verification": {
                "status": verification_status,
                "checks": verification_checks,
            },
        },
        ensure_ascii=False,
    )
    projected = project_subagent_result(
        report_content,
        subagent_id=subagent_id,
        agent_type="workflow",
        outcome=workflow_status,
        fallback_summary=summary,
    )
    projected["contractStatus"] = "derived"
    return projected


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
    _emit_subagent_observation(
        context,
        subagent_id=subagent_id,
        result_contract_status=result_contract_status,
        agent_type="workflow",
        outcome=outcome,
        model_turns=model_turns,
        tool_calls=tool_calls,
        duration_ms=duration_ms,
        model_turn_limit=model_turn_limit,
        phase_count=phase_count,
        max_phases=_WORKFLOW_MAX_PHASES,
        result_truncated=result_truncated,
    )
    if journal is not None:
        budget = getattr(context, "_agent_budget", None)
        snapshot = getattr(budget, "snapshot", None)
        try:
            journal.finish(
                subagent_id,
                outcome=(
                    outcome
                    if outcome in {"completed", "failed", "budget_exceeded"}
                    else "failed"
                ),
                model_turns=model_turns,
                tool_calls=tool_calls,
                duration_ms=duration_ms,
                max_turns=_WORKFLOW_MAX_PHASES,
                result_truncated=result_truncated,
                budget=snapshot() if callable(snapshot) else None,
            )
        except Exception:  # noqa: BLE001 - sidecar observation is optional
            pass


class _WorkflowCommitStateUnknown(BaseException):
    """Promotion was interrupted and the parent is neither base nor target."""


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


def run_workflow(
    input_data: dict,
    context,
    *,
    depth: int,
    subagent_id: str,
    run_subagent: _RunSubagent,
) -> ToolResult:
    """Run a workflow against a disposable workspace transaction."""
    agent_budget = getattr(context, "_agent_budget", None)
    if agent_budget is None:
        runtime = getattr(context, "_runtime", None)
        agent_budget = AgentTurnBudget.from_runtime(
            runtime if isinstance(runtime, dict) else {}
        )
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
        "acceptance_evidence_matrix",
        "acceptance_evidence_matrix_id",
        "agent_budget",
        "analyst_artifact_id",
        "analyst_evidence_ids",
        "analyst_report",
        "approval_request_document",
        "approval_request_id",
        "artifact_store",
        "change_diff_artifact",
        "change_diff_artifact_id",
        "child_context",
        "commit_error",
        "commit_ready_status",
        "context",
        "description",
        "execute_result",
        "execution_artifact",
        "execution_artifact_id",
        "executor_assignment_id",
        "frozen_change_set",
        "frozen_workspace_change",
        "human_approval_artifact_id",
        "human_approval_document",
        "implementation_order",
        "locked_write_paths",
        "mailbox",
        "plan_result",
        "profile",
        "requested_checks",
        "research_notes",
        "review_key",
        "review_result",
        "run_subagent",
        "scheduler_artifact_authority",
        "task_id",
        "task_prompt",
        "transaction",
        "transitions",
        "typed_review",
        "verification_artifact_id",
        "verification_evidence_ids",
        "verification_result",
        "workflow_id",
        "workflow_request",
        "workflow_request_artifact_id",
        "workflow_request_digest",
        "audit_status",
        "dispatch_plan",
        "finalization_status",
        "phase_results",
        "scheduler_outcome",
        "scheduler_trace",
        "subagent_id",
        "trace_projection_failed",
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
        if input_data.get("workflow_request") is not None and input_data.get(
            "parallel_explore"
        ):
            return ToolResult(
                ok=False,
                output=(
                    "error[structured_parallel_bypass_rejected]: legacy parallel "
                    "exploration is not part of the central DispatchPlan"
                ),
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
        transitions: list[object] = []
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

        self.acceptance_evidence_matrix = acceptance_evidence_matrix
        self.acceptance_evidence_matrix_id = acceptance_evidence_matrix_id
        self.agent_budget = agent_budget
        self.analyst_artifact_id = analyst_artifact_id
        self.analyst_evidence_ids = analyst_evidence_ids
        self.analyst_report = analyst_report
        self.approval_request_document = approval_request_document
        self.approval_request_id = approval_request_id
        self.artifact_store = artifact_store
        self.change_diff_artifact = change_diff_artifact
        self.change_diff_artifact_id = change_diff_artifact_id
        self.child_context = child_context
        self.commit_error = commit_error
        self.commit_ready_status = commit_ready_status
        self.context = context
        self.description = description
        self.execute_result = execute_result
        self.execution_artifact = execution_artifact
        self.execution_artifact_id = execution_artifact_id
        self.executor_assignment_id = executor_assignment_id
        self.frozen_change_set = frozen_change_set
        self.frozen_workspace_change = frozen_workspace_change
        self.human_approval_artifact_id = human_approval_artifact_id
        self.human_approval_document = human_approval_document
        self.implementation_order = implementation_order
        self.locked_write_paths = locked_write_paths
        self.mailbox = mailbox
        self.plan_result = plan_result
        self.profile = profile
        self.requested_checks = requested_checks
        self.research_notes = research_notes
        self.review_key = review_key
        self.review_result = review_result
        self.run_subagent = run_subagent
        self.scheduler_artifact_authority = scheduler_artifact_authority
        self.task_id = task_id
        self.task_prompt = task_prompt
        self.transaction = transaction
        self.transitions = transitions
        self.typed_review = typed_review
        self.verification_artifact_id = verification_artifact_id
        self.verification_evidence_ids = verification_evidence_ids
        self.verification_result = verification_result
        self.workflow_id = workflow_id
        self.workflow_request = workflow_request
        self.workflow_request_artifact_id = workflow_request_artifact_id
        self.workflow_request_digest = workflow_request_digest

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

        acceptance_evidence_matrix = self.acceptance_evidence_matrix
        acceptance_evidence_matrix_id = self.acceptance_evidence_matrix_id
        analyst_artifact_id = self.analyst_artifact_id
        analyst_report = self.analyst_report
        approval_request_document = self.approval_request_document
        approval_request_id = self.approval_request_id
        change_diff_artifact = self.change_diff_artifact
        change_diff_artifact_id = self.change_diff_artifact_id
        commit_error = self.commit_error
        commit_ready_status = self.commit_ready_status
        execute_result = self.execute_result
        execution_artifact = self.execution_artifact
        execution_artifact_id = self.execution_artifact_id
        executor_assignment_id = self.executor_assignment_id
        frozen_change_set = self.frozen_change_set
        frozen_workspace_change = self.frozen_workspace_change
        human_approval_artifact_id = self.human_approval_artifact_id
        human_approval_document = self.human_approval_document
        implementation_order = self.implementation_order
        locked_write_paths = self.locked_write_paths
        plan_result = self.plan_result
        profile = self.profile
        review_result = self.review_result
        typed_review = self.typed_review
        verification_artifact_id = self.verification_artifact_id
        verification_result = self.verification_result

        self.scheduler_outcome = scheduler_outcome
        self.dispatch_plan = dispatch_plan
        self.finalization_status = "completed"

        self.check_finalization_deadline()
        phase_results = [
            *explore_results,
            *(
                result
                for result in (plan_result, execute_result, review_result)
                if result is not None
            ),
        ]
        self.phase_results = phase_results
        self.subagent_id = subagent_id
        self.scheduler_trace: dict[str, object] | None = None
        self.trace_projection_failed = False

        try:
            scheduler_trace_text = self.project_trace_authority()
        except BaseException as exc:
            # A one-shot host interrupt after promotion must not skip the durable
            # audit. Retry only this bounded, pure projection once; never rerun a
            # Worker, verifier, or commit port.
            if not isinstance(exc, Exception) and scheduler_outcome.committed:
                try:
                    scheduler_trace_text = self.project_trace_authority()
                except BaseException:
                    self.trace_projection_failed = True
            elif not isinstance(exc, Exception):
                raise
            else:
                self.trace_projection_failed = True
            if self.trace_projection_failed:
                self.finalization_status = "projection_failed"
                scheduler_trace_text = (
                    "=== SCHEDULER TRACE UNAVAILABLE ===\nprojection_failed"
                )
        if self.scheduler_trace is None:
            self.trace_projection_failed = True
            self.finalization_status = "projection_failed"
        self.audit_status = (
            "projection_or_persistence_failed"
            if self.scheduler_trace is None
            else "pending"
        )
        if self.scheduler_trace is not None:
            try:
                artifact_ids = artifact_store.list_artifact_ids()
                artifact_snapshot = tuple(
                    artifact
                    for artifact_id in artifact_ids
                    if (artifact := artifact_store.read(artifact_id)) is not None
                )
                if len(artifact_snapshot) != len(artifact_ids):
                    raise RuntimeError("scheduler artifact snapshot is inconsistent")
                scheduler_audit = project_scheduler_audit(
                    profile=profile,
                    plan=dispatch_plan,
                    trace=self.scheduler_trace,
                    artifacts=artifact_snapshot,
                    contract_mode=("v2" if workflow_request.structured else "legacy"),
                )
                self.check_finalization_deadline()
                audit_recorder = getattr(
                    getattr(context, "_event_sink", None),
                    "record_scheduler_audit",
                    None,
                )
                if not callable(audit_recorder):
                    self.audit_status = "not_configured"
                else:
                    persisted = audit_recorder(scheduler_audit)
                    self.audit_status = (
                        "persisted" if persisted is True else "persistence_failed"
                    )
                    # Cancellation/deadline is a status fact, never permission to
                    # erase the bounded durable audit projection.
                    self.check_finalization_deadline()
            except BaseException as exc:  # committed authority survives interrupts
                if not isinstance(exc, Exception) and not scheduler_outcome.committed:
                    raise
                self.audit_status = "projection_or_persistence_failed"

        if scheduler_outcome.status == "timed_out":
            failure_stage = (
                scheduler_outcome.failure_stage.value
                if scheduler_outcome.failure_stage is not None
                else "unknown"
            )
            timeout_contract_status = "unavailable"
            timeout_suffix = ""
            if workflow_request.structured:
                structured_result = self.project_terminal_result(
                    execute_result or plan_result,
                    outcome="failed",
                    verification_status="inconclusive",
                    checks=["global_deadline_exceeded"],
                )
                timeout_contract_status = "derived"
                timeout_suffix = "\n\n" + render_subagent_result(structured_result)
            _emit_workflow_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status=timeout_contract_status,
                journal=workflow_journal,
                outcome="failed",
                results=phase_results,
                phase_count=len(phase_results),
                started=started,
                model_turn_limit=model_turn_limit,
                result_truncated=False,
            )
            return self.finalize_tool_result(
                ToolResult(
                    ok=False,
                    output=(
                        "error[sub_agent_deadline_exceeded]: central workflow "
                        f"timed out at stage {failure_stage}; no isolated changes "
                        "were promoted\n\n" + scheduler_trace_text + timeout_suffix
                    ),
                )
            )

        if scheduler_outcome.status == "waiting_human":
            structured_result = self.project_terminal_result(
                plan_result,
                outcome="failed",
                verification_status="inconclusive",
                checks=[
                    scheduler_outcome.terminal_reason
                    or "human approval required before execution"
                ],
            )
            human_reason = (
                scheduler_outcome.terminal_reason or "human_approval_required"
            )
            analysis_text = (
                json.dumps(analyst_report, ensure_ascii=False, sort_keys=True)
                if workflow_request.structured and analyst_report is not None
                else (plan_result.output if plan_result is not None else "")[:4000]
            )
            output = (
                f"[Workflow {description} waiting_human]\n"
                "  Phases: Analyst -> Human gate\n\n"
                f"error[human_approval_required]: {human_reason}; execution was not "
                "started.\n"
                + (
                    f"approvalRequestId: {approval_request_id}\n"
                    if approval_request_id is not None
                    else ""
                )
                + "\n"
                "=== ANALYSIS ===\n"
                f"{analysis_text[:4000]}\n\n"
                + scheduler_trace_text
                + "\n\n"
                + render_subagent_result(structured_result)
            )
            _emit_workflow_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="derived",
                journal=workflow_journal,
                outcome="failed",
                results=phase_results,
                phase_count=(1 if explore_results else 0) + (1 if plan_result else 0),
                started=started,
                model_turn_limit=model_turn_limit,
                result_truncated=False,
            )
            return self.finalize_tool_result(
                ToolResult(ok=False, output=output, awaitUser=True)
            )

        if (
            profile.kind.value == "read_only"
            and scheduler_outcome.status == "completed"
        ):
            parent_only = DispatchStage.PARENT in scheduler_outcome.completed_stages
            structured_result = self.project_terminal_result(
                plan_result,
                outcome="completed",
                verification_status="not_run",
                checks=[],
            )
            analysis_text = (
                json.dumps(analyst_report, ensure_ascii=False, sort_keys=True)
                if workflow_request.structured and analyst_report is not None
                else plan_result.output
                if plan_result is not None
                else "Parent-only route selected; no Worker was dispatched."
            )
            output = (
                f"[Workflow {description} completed]\n"
                f"  Phases: {'Parent' if parent_only else 'Analyst'}\n\n"
                "=== ANALYSIS ===\n"
                f"{analysis_text[:5000]}\n\n"
                + scheduler_trace_text
                + "\n\n"
                + render_subagent_result(structured_result)
            )
            _emit_workflow_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="derived",
                journal=workflow_journal,
                outcome="completed",
                results=phase_results,
                phase_count=(1 if explore_results else 0) + (1 if plan_result else 0),
                started=started,
                model_turn_limit=model_turn_limit,
                result_truncated=False,
            )
            return self.finalize_tool_result(ToolResult(ok=True, output=output))

        if scheduler_outcome.failure_stage is DispatchStage.ANALYST:
            failed_result = plan_result or ToolResult(
                ok=False, output="plan unavailable"
            )
            failure_contract_status = "unavailable"
            failure_suffix = ""
            if workflow_request.structured:
                structured_result = self.project_terminal_result(
                    failed_result,
                    outcome="failed",
                    verification_status="not_run",
                    checks=[scheduler_outcome.terminal_reason or "analysis failed"],
                )
                failure_contract_status = "derived"
                failure_suffix = "\n\n" + render_subagent_result(structured_result)
            failure_narrative = (
                json.dumps(analyst_report, ensure_ascii=False, sort_keys=True)
                if workflow_request.structured and analyst_report is not None
                else (
                    "Structured Analyst result was unavailable; see the Scheduler "
                    f"reason code: {scheduler_outcome.terminal_reason}."
                    if workflow_request.structured
                    else failed_result.output[:8000]
                )
            )
            _emit_workflow_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status=failure_contract_status,
                journal=workflow_journal,
                outcome="failed",
                results=phase_results,
                phase_count=(1 if explore_results else 0) + 1,
                started=started,
                model_turn_limit=model_turn_limit,
                result_truncated=len(failed_result.output) > 8000,
            )
            return self.finalize_tool_result(
                ToolResult(
                    ok=False,
                    output=(
                        "[Workflow plan phase failed]\n\n"
                        + failure_narrative[:8000]
                        + "\n\n"
                        + scheduler_trace_text
                        + failure_suffix
                    ),
                )
            )
        if scheduler_outcome.failure_stage is DispatchStage.EXECUTOR:
            failed_result = execute_result or ToolResult(
                ok=False,
                output=scheduler_outcome.terminal_reason or "execution unavailable",
            )
            failure_contract_status = "unavailable"
            failure_suffix = ""
            if workflow_request.structured:
                structured_result = self.project_terminal_result(
                    failed_result,
                    outcome="failed",
                    verification_status="not_run",
                    checks=[scheduler_outcome.terminal_reason or "execution failed"],
                )
                failure_contract_status = "derived"
                failure_suffix = "\n\n" + render_subagent_result(structured_result)
            failure_narrative = (
                json.dumps(execution_artifact, ensure_ascii=False, sort_keys=True)
                if workflow_request.structured and execution_artifact is not None
                else (
                    "Structured Executor result was unavailable; see the Scheduler "
                    f"reason code: {scheduler_outcome.terminal_reason}."
                    if workflow_request.structured
                    else failed_result.output[:8000]
                )
            )
            _emit_workflow_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status=failure_contract_status,
                journal=workflow_journal,
                outcome="failed",
                results=phase_results,
                phase_count=(1 if explore_results else 0) + 2,
                started=started,
                model_turn_limit=model_turn_limit,
                result_truncated=len(failed_result.output) > 8000,
            )
            return self.finalize_tool_result(
                ToolResult(
                    ok=False,
                    output=(
                        "[Workflow execute phase failed]\n\n"
                        + failure_narrative[:8000]
                        + "\n\n"
                        + scheduler_trace_text
                        + failure_suffix
                    ),
                )
            )
        if scheduler_outcome.failure_stage is DispatchStage.VERIFIER:
            self.typed_review = _workflow_failure_review(
                scheduler_outcome.terminal_reason or "verification_failed"
            )
            failed_execute = execute_result or ToolResult(
                ok=False,
                output="execution result unavailable",
            )
            structured_result = _derive_workflow_result(
                subagent_id=subagent_id,
                workflow_status="failed",
                execute_result=failed_execute,
                phase_results=phase_results,
                typed_review=self.typed_review,
                analyst_report=(
                    analyst_report if workflow_request.structured else None
                ),
                execution_artifact=(
                    execution_artifact if workflow_request.structured else None
                ),
                verification_result=(
                    verification_result if workflow_request.structured else None
                ),
                structured_mode=workflow_request.structured,
            )
            verification_text = json.dumps(
                verification_result
                or {
                    "resultType": "VerificationResult.v1",
                    "status": "inconclusive",
                    "patchDigest": scheduler_outcome.patch_digest,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            execution_text = (
                json.dumps(execution_artifact, ensure_ascii=False, sort_keys=True)
                if workflow_request.structured and execution_artifact is not None
                else failed_execute.output[:3000]
            )
            output = (
                f"[Workflow {description} failed]\n"
                "  Phases: explore -> plan -> execute -> verifier\n\n"
                "=== DETERMINISTIC VERIFICATION ===\n"
                f"{verification_text}\n\n"
                "=== EXECUTE ===\n"
                f"{execution_text[:3000]}\n\n"
                + scheduler_trace_text
                + "\n\n"
                + render_subagent_result(structured_result)
            )
            _emit_workflow_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="derived",
                journal=workflow_journal,
                outcome="failed",
                results=phase_results,
                phase_count=(1 if explore_results else 0) + 2,
                started=started,
                model_turn_limit=model_turn_limit,
                result_truncated=False,
            )
            return self.finalize_tool_result(
                ToolResult(
                    ok=False,
                    output=output,
                )
            )

        if DispatchStage.REVIEWER not in dispatch_plan.stages:
            workflow_ok = scheduler_outcome.status == "completed"
            structured_result = self.project_terminal_result(
                execute_result,
                outcome="completed" if workflow_ok else "failed",
                verification_status="passed" if workflow_ok else "inconclusive",
                checks=[
                    "deterministic verification: "
                    + str(
                        (verification_result or {}).get(
                            "verificationId",
                            scheduler_outcome.terminal_reason or "unavailable",
                        )
                    )
                ],
            )
            verification_text = json.dumps(
                verification_result or {},
                ensure_ascii=False,
                sort_keys=True,
            )
            execution_text = (
                json.dumps(execution_artifact, ensure_ascii=False, sort_keys=True)
                if workflow_request.structured and execution_artifact is not None
                else (execute_result.output if execute_result else "")[:4000]
            )
            output = (
                f"[Workflow {description} "
                f"{'completed' if workflow_ok else 'failed'}]\n"
                "  Phases: Executor -> Verifier\n\n"
                "=== DETERMINISTIC VERIFICATION ===\n"
                f"{verification_text}\n\n"
                "=== EXECUTE ===\n"
                f"{execution_text[:4000]}\n\n"
                + scheduler_trace_text
                + "\n\n"
                + render_subagent_result(structured_result)
            )
            _emit_workflow_observation(
                context,
                subagent_id=subagent_id,
                result_contract_status="derived",
                journal=workflow_journal,
                outcome="completed" if workflow_ok else "failed",
                results=phase_results,
                phase_count=(1 if explore_results else 0)
                + (1 if execute_result else 0),
                started=started,
                model_turn_limit=model_turn_limit,
                result_truncated=False,
            )
            return self.finalize_tool_result(ToolResult(ok=workflow_ok, output=output))

        plan_result = plan_result or ToolResult(
            ok=False,
            output="analysis result unavailable",
        )
        execute_result = execute_result or ToolResult(
            ok=False,
            output="execution result unavailable",
        )
        review_result = review_result or ToolResult(
            ok=False,
            output="review result unavailable",
        )
        if (
            scheduler_outcome.status == "failed"
            and scheduler_outcome.failure_stage is DispatchStage.REVIEWER
        ):
            review_failure = scheduler_outcome.terminal_reason or "review_gate_rejected"
            scheduler_finding = (
                f"error[{review_failure}]: the Scheduler rejected the review gate; "
                "no isolated changes were promoted"
            )
            if self.typed_review.get("verdict") == "approved":
                # An approved Worker verdict must never survive a scheduler-owned
                # digest/evidence gate rejection as the presented authority.
                self.typed_review = _workflow_failure_review(review_failure)
                self.typed_review["blockingFindings"] = [scheduler_finding]
            else:
                # Preserve the Worker's actual blocking/inconclusive reason for
                # compatibility and diagnosis, then append scheduler authority.
                existing_findings = self.typed_review.get("blockingFindings", [])
                self.typed_review["blockingFindings"] = [
                    *(
                        list(existing_findings)
                        if isinstance(existing_findings, list)
                        else []
                    ),
                    scheduler_finding,
                ]
        elif commit_error is not None:
            self.typed_review = _workflow_failure_review("workflow_commit_failed")
            self.typed_review["blockingFindings"] = [
                "error[workflow_commit_failed]: approved isolated changes were "
                f"not applied to the parent workspace ({commit_error})"
            ]
        duration_ms = int((time.time() - started) * 1000)
        workflow_ok = bool(scheduler_outcome.status == "completed")
        workflow_status = "completed" if workflow_ok else "failed"
        header = (
            f"[Workflow {description} {workflow_status}]\n"
            f"  Phases: explore -> plan -> execute -> review\n"
            f"  Duration: {duration_ms / 1000:.1f}s"
        )
        review_authority = json.dumps(
            self.typed_review,
            ensure_ascii=False,
            sort_keys=True,
        )
        research_text = "\n\n".join(result.output for result in explore_results)
        if workflow_request.structured:
            section_specs = (
                ("=== REVIEW VERDICT ===", review_authority, 3000),
                (
                    "=== DETERMINISTIC VERIFICATION ===",
                    json.dumps(
                        verification_result or {},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    3000,
                ),
                (
                    "=== EXECUTION RESULT ===",
                    json.dumps(
                        execution_artifact or {},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    3000,
                ),
                (
                    "=== ANALYST REPORT ===",
                    json.dumps(
                        analyst_report or {},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    2000,
                ),
            )
        else:
            section_specs = (
                ("=== REVIEW VERDICT ===", review_authority, 3000),
                (
                    "=== REVIEW NARRATIVE ==="
                    if review_result.ok
                    else "=== Workflow review phase failed ===",
                    review_result.output,
                    1200,
                ),
                ("=== EXECUTE ===", execute_result.output, 1600),
                ("=== PLAN ===", plan_result.output, 1000),
                ("=== RESEARCH ===", research_text, 500),
            )
        sections: list[str] = []
        result_truncated = False
        for label, content, limit in section_specs:
            if label == "=== RESEARCH ===" and not content:
                continue
            section, truncated = _bounded_workflow_section(label, content, limit)
            sections.append(section)
            result_truncated = result_truncated or truncated
        output = header + "\n\n" + "\n\n".join(sections)
        if len(output) > _WORKFLOW_OUTPUT_MAX_CHARS:
            result_truncated = True
            suffix = "\n\n... [workflow output truncated]"
            output = output[: _WORKFLOW_OUTPUT_MAX_CHARS - len(suffix)] + suffix

        all_phase_results = [
            *explore_results,
            plan_result,
            execute_result,
            review_result,
        ]
        structured_result = _derive_workflow_result(
            subagent_id=subagent_id,
            workflow_status=workflow_status,
            execute_result=execute_result,
            phase_results=all_phase_results,
            typed_review=self.typed_review,
            analyst_report=analyst_report if workflow_request.structured else None,
            execution_artifact=(
                execution_artifact if workflow_request.structured else None
            ),
            verification_result=(
                verification_result if workflow_request.structured else None
            ),
            structured_mode=workflow_request.structured,
        )
        artifact_suffix = (
            "\n\n"
            + scheduler_trace_text
            + "\n\n"
            + render_subagent_result(structured_result)
        )
        if len(output) + len(artifact_suffix) > _WORKFLOW_OUTPUT_MAX_CHARS:
            result_truncated = True
            truncation_notice = "\n\n... [workflow output truncated]"
            narrative_limit = max(
                0,
                _WORKFLOW_OUTPUT_MAX_CHARS
                - len(artifact_suffix)
                - len(truncation_notice),
            )
            output = output[:narrative_limit] + truncation_notice
        output += artifact_suffix

        _emit_workflow_observation(
            context,
            subagent_id=subagent_id,
            result_contract_status="derived",
            journal=workflow_journal,
            outcome=workflow_status,
            results=all_phase_results,
            phase_count=(1 if explore_results else 0) + 3,
            started=started,
            model_turn_limit=model_turn_limit,
            result_truncated=result_truncated,
        )
        return self.finalize_tool_result(
            ToolResult(
                ok=workflow_ok,
                output=output,
                verification=(
                    project_verification(
                        kind="review",
                        passed=True,
                        source="workflow_review",
                    )
                    if workflow_ok
                    else None
                ),
            )
        )

    def check_finalization_deadline(
        self,
    ) -> bool:
        try:
            _raise_task_deadline(self.context)
        except TurnCancellationRequested:
            if not self.scheduler_outcome.committed:
                raise
            self.finalization_status = "cancelled_after_commit"
            return False
        except (_TaskDeadlineExceeded, AgentTurnDeadlineExceeded):
            self.finalization_status = (
                "timed_out_after_commit"
                if self.scheduler_outcome.committed
                else "timed_out"
            )
            return False
        return True

    def project_trace_authority(
        self,
    ) -> str:
        self.scheduler_trace = project_scheduler_trace(
            self.dispatch_plan,
            self.scheduler_outcome,
            self.transitions,
        )
        return render_scheduler_trace(self.scheduler_trace)

    def _project_final_tool_result(self, result: ToolResult) -> ToolResult:
        self.check_finalization_deadline()
        failed = (
            self.finalization_status != "completed"
            or self.commit_ready_status
            in {"persistence_failed", "projection_failed", "recorder_missing"}
            or (
                self.workflow_request.structured
                and getattr(
                    self.context,
                    "_central_workflow_v2_enforced",
                    False,
                )
                is True
                and self.audit_status == "not_configured"
            )
            or self.audit_status
            in {
                "persistence_failed",
                "projection_or_persistence_failed",
            }
        )
        prefix = ""
        if failed:
            prefix = (
                "error[workflow_finalization_incomplete]: "
                f"changesCommitted={str(self.scheduler_outcome.committed).lower()}, "
                f"finalizationStatus={self.finalization_status}, "
                f"auditStatus={self.audit_status}\n\n"
            )

        projected_result = extract_subagent_result(result.output)

        def compact_result(value: dict[str, object]) -> dict[str, object]:
            verification = value.get("verification")
            verification_status = (
                verification.get("status")
                if isinstance(verification, dict)
                else "inconclusive"
            )
            return {
                "resultVersion": value["resultVersion"],
                "subagentId": value["subagentId"],
                "agentType": value["agentType"],
                "outcome": value["outcome"],
                "contractStatus": "derived",
                "summary": (
                    "Workflow result projection compacted; inspect the durable "
                    "scheduler audit for complete evidence."
                    if self.audit_status == "persisted"
                    else "Workflow result projection compacted; complete "
                    "evidence is unavailable because the final audit was not "
                    "persisted."
                ),
                "files": [],
                "risks": [],
                "verification": {
                    "status": verification_status,
                    "checks": [],
                },
            }

        def project_compact_trace(count: int) -> dict[str, object]:
            if self.scheduler_trace is None:
                raise RuntimeError("scheduler trace is unavailable")
            projected = dict(self.scheduler_trace)
            source = list(self.scheduler_trace["transitions"])
            required_indices = {len(source) - 1}
            completed_by_stage: dict[str, int] = {}
            dispatched_by_identity: dict[tuple[str, str, str, str], int] = {}
            completed_identities: list[tuple[str, str, str, str]] = []
            attempt_indices: dict[tuple[str, str, str, str], list[int]] = {}
            commit_requested_index: int | None = None
            for index, item in enumerate(source):
                stage = str(item["stage"])
                outcome = str(item["outcome"])
                identity = (
                    stage,
                    str(item["assignmentId"]),
                    str(item["attemptId"]),
                    str(item["agentRunId"]),
                )
                if outcome == "dispatched":
                    dispatched_by_identity[identity] = index
                if stage not in {"parent", "commit"}:
                    attempt_indices.setdefault(identity, []).append(index)
                if outcome == "completed":
                    # Keep the final successful attempt for every stage. This
                    # preserves the exact role artifacts consumed by the
                    # commit seal while dropping redundant retry chatter.
                    completed_by_stage[stage] = index
                    if stage not in {"parent", "commit"}:
                        completed_identities.append(identity)
                if stage == "commit" and outcome == "commit_requested":
                    commit_requested_index = index
            required_indices.update(completed_by_stage.values())
            for identity in completed_identities:
                dispatch_index = dispatched_by_identity.get(identity)
                if dispatch_index is None:
                    raise RuntimeError("completed scheduler stage has no dispatch")
                required_indices.add(dispatch_index)
            if commit_requested_index is not None:
                required_indices.add(commit_requested_index)
            elif bool(self.scheduler_trace["committed"]):
                raise RuntimeError("committed scheduler trace has no commit request")
            # Required terminals are inseparable from their attempt dispatch.
            # In particular, a retained ``failed -> retrying`` pair without
            # its dispatch is not a valid lifecycle and must never be emitted.
            for index in tuple(required_indices):
                item = source[index]
                stage = str(item["stage"])
                outcome = str(item["outcome"])
                if stage in {"parent", "commit"} or outcome == "dispatched":
                    continue
                identity = (
                    stage,
                    str(item["assignmentId"]),
                    str(item["attemptId"]),
                    str(item["agentRunId"]),
                )
                dispatch_index = dispatched_by_identity.get(identity)
                if dispatch_index is not None:
                    required_indices.add(dispatch_index)
                elif outcome not in {"timed_out", "waiting_human"}:
                    raise RuntimeError("required scheduler terminal has no dispatch")

            target_count = max(count, len(required_indices))
            diagnostic_outcomes = {
                "blocked",
                "failed",
                "inconclusive",
                "rejected",
                "retrying",
                "timed_out",
                "waiting_human",
            }
            selected_indices = set(required_indices)
            optional_groups = sorted(
                (
                    tuple(indices)
                    for indices in attempt_indices.values()
                    if not set(indices).issubset(required_indices)
                ),
                key=lambda indices: (
                    any(
                        str(source[index]["outcome"]) in diagnostic_outcomes
                        for index in indices
                    ),
                    max(indices),
                ),
                reverse=True,
            )
            # An attempt is an atomic public-trace unit. Complete retry
            # diagnostics remain in the durable audit, but a compact public
            # projection may drop the whole failed attempt to stay bounded.
            for indices in optional_groups:
                missing = set(indices) - selected_indices
                if len(selected_indices) + len(missing) > target_count:
                    continue
                selected_indices.update(missing)
            selected = [dict(source[index]) for index in sorted(selected_indices)]
            parent_decision_id: str | None = None
            for sequence, transition in enumerate(selected, start=1):
                transition["sequence"] = sequence
                transition["parentDecisionId"] = parent_decision_id
                parent_decision_id = str(transition["decisionId"])
            projected["transitions"] = selected
            return projected

        def status_text(
            *,
            trace_projection: str,
            result_projection: str,
        ) -> str:
            status_document = {
                "finalizationVersion": 1,
                "changesCommitted": self.scheduler_outcome.committed,
                "commitReadyStatus": self.commit_ready_status,
                "finalizationStatus": self.finalization_status,
                "auditStatus": self.audit_status,
                "traceProjection": trace_projection,
                "resultProjection": result_projection,
            }
            return "=== WORKFLOW FINALIZATION v1 ===\n" + json.dumps(
                status_document, sort_keys=True
            )

        marker_index = result.output.rfind("\n\n" + SCHEDULER_TRACE_MARKER)
        if marker_index < 0:
            marker_index = result.output.rfind("\n\n" + SUBAGENT_RESULT_MARKER)
        narrative = result.output[:marker_index] if marker_index >= 0 else result.output

        rendered_result = (
            render_subagent_result(projected_result)
            if projected_result is not None
            else None
        )
        result_projection = "full" if rendered_result is not None else "absent"
        selected_trace: str | None = None
        selected_status: str | None = None
        trace_projection = "full"

        def select_trace() -> None:
            nonlocal selected_trace, selected_status, trace_projection
            if self.scheduler_trace is None or self.trace_projection_failed:
                candidate_status = status_text(
                    trace_projection="failed",
                    result_projection=result_projection,
                )
                authority_parts = [candidate_status]
                if rendered_result is not None:
                    authority_parts.append(rendered_result)
                authority = prefix + "\n\n".join(authority_parts)
                if len(authority) <= _WORKFLOW_OUTPUT_MAX_CHARS:
                    selected_trace = ""
                    selected_status = candidate_status
                    trace_projection = "failed"
                return
            transition_count = len(self.scheduler_trace["transitions"])
            seen_projection_sizes: set[int] = set()
            for count in range(transition_count, 0, -1):
                trace_document = project_compact_trace(count)
                projection_size = len(trace_document["transitions"])
                if projection_size in seen_projection_sizes:
                    continue
                seen_projection_sizes.add(projection_size)
                candidate_projection = (
                    "full" if projection_size == transition_count else "compacted"
                )
                try:
                    candidate_trace = render_scheduler_trace(trace_document)
                except ValueError:
                    # Optional transitions are selected by diagnostic value;
                    # skip intermediate counts that split an attempt lifecycle.
                    continue
                candidate_status = status_text(
                    trace_projection=candidate_projection,
                    result_projection=result_projection,
                )
                authority_parts = [candidate_status, candidate_trace]
                if rendered_result is not None:
                    authority_parts.append(rendered_result)
                authority = prefix + "\n\n".join(authority_parts)
                if len(authority) <= _WORKFLOW_OUTPUT_MAX_CHARS:
                    selected_trace = candidate_trace
                    selected_status = candidate_status
                    trace_projection = candidate_projection
                    return

        select_trace()
        if selected_trace is None and projected_result is not None:
            rendered_result = render_subagent_result(compact_result(projected_result))
            result_projection = "compacted"
            select_trace()
        if selected_trace is None or selected_status is None:
            # Closed contracts bound a one-transition trace plus the compact
            # result well below the public cap. Reaching this branch means an
            # internal invariant, not user narrative, has been violated.
            raise RuntimeError("workflow authority projection exceeds output cap")

        authority_parts = [selected_status]
        if selected_trace:
            authority_parts.append(selected_trace)
        if rendered_result is not None:
            authority_parts.append(rendered_result)
        authority_output = prefix + "\n\n".join(authority_parts)
        narrative_budget = _WORKFLOW_OUTPUT_MAX_CHARS - len(authority_output) - 2
        if narrative and narrative_budget > 0:
            if len(narrative) > narrative_budget:
                notice = "... [workflow narrative truncated]"
                narrative = (
                    narrative[: max(0, narrative_budget - len(notice))] + notice
                    if narrative_budget >= len(notice)
                    else ""
                )
            if narrative:
                finalized_output = narrative + "\n\n" + authority_output
            else:
                finalized_output = authority_output
        else:
            finalized_output = authority_output
        return ToolResult(
            ok=result.ok and not failed,
            output=finalized_output,
            backgroundTask=result.backgroundTask,
            awaitUser=result.awaitUser,
            verification=result.verification if not failed else None,
            _must_deliver=self.scheduler_outcome.committed,
        )

    def finalize_tool_result(self, result: ToolResult) -> ToolResult:
        try:
            return self._project_final_tool_result(result)
        except BaseException as exc:  # committed truth must still be delivered
            if not isinstance(exc, Exception) and not self.scheduler_outcome.committed:
                raise
            fallback_status = {
                "finalizationVersion": 1,
                "changesCommitted": self.scheduler_outcome.committed,
                "commitReadyStatus": self.commit_ready_status,
                "finalizationStatus": "projection_failed",
                "auditStatus": self.audit_status,
                "traceProjection": "failed",
                "resultProjection": "failed",
            }
            output = (
                "error[workflow_finalization_incomplete]: "
                f"changesCommitted={str(self.scheduler_outcome.committed).lower()}, "
                "finalizationStatus=projection_failed\n\n"
                "=== WORKFLOW FINALIZATION v1 ===\n"
                + json.dumps(fallback_status, sort_keys=True)
            )
            return ToolResult(
                ok=False,
                output=output[:_WORKFLOW_OUTPUT_MAX_CHARS],
                _must_deliver=self.scheduler_outcome.committed,
            )

    def project_terminal_result(
        self,
        source: ToolResult | None,
        *,
        outcome: str,
        verification_status: str,
        checks: list[str],
    ) -> dict[str, object]:
        if self.workflow_request.structured:
            return _derive_workflow_result(
                subagent_id=self.subagent_id,
                workflow_status=outcome,
                execute_result=source
                or ToolResult(ok=False, output="scheduler result unavailable"),
                phase_results=self.phase_results,
                typed_review=self.typed_review,
                analyst_report=self.analyst_report,
                execution_artifact=self.execution_artifact,
                verification_result=self.verification_result,
                structured_mode=True,
            )
        source_report = (
            extract_subagent_result(source.output) if source is not None else None
        )
        summary = (
            str(source_report["summary"])
            if source_report is not None
            else str(source.output)[:4000]
            if source is not None
            else "The central scheduler completed without dispatching a Worker."
        )
        report_content = json.dumps(
            {
                "resultVersion": 1,
                "summary": summary,
                "files": (
                    list(source_report.get("files", []))
                    if source_report is not None
                    else []
                ),
                "risks": (
                    list(source_report.get("risks", []))
                    if source_report is not None
                    else []
                ),
                "verification": {
                    "status": verification_status,
                    "checks": checks,
                },
            },
            ensure_ascii=False,
        )
        projected = project_subagent_result(
            report_content,
            subagent_id=self.subagent_id,
            agent_type="workflow",
            outcome=outcome,
            fallback_summary=summary,
        )
        projected["contractStatus"] = "derived"
        return projected

    def run_assigned_worker(
        self,
        worker_input: dict,
        worker_context,
        channel: AssignmentArtifactChannel | None,
    ) -> ToolResult:
        """Run one Worker and revoke its result key at the return boundary."""
        try:
            return self.run_subagent(worker_input, worker_context)
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
        return StageResult(
            status="failed",
            artifact_id=_stage_receipt_artifact_id(assignment),
            error_code="unsupported_worker_stage",
        )

    def _run_analyst(self, assignment) -> StageResult:
        self.analyst_evidence_ids.clear()
        if self.workflow_request.structured:
            analyst_assignment = _build_worker_assignment(
                self.workflow_request,
                assignment,
                context=self.context,
                agent_budget=self.agent_budget,
                role="analyst",
                mode=(
                    "investigate"
                    if self.workflow_request.kind == "read_only"
                    else "prepare_change"
                ),
                objective=self.task_prompt,
                input_refs=(self.workflow_request_artifact_id,),
            )
            result_key = _assignment_result_key(assignment, "analyst")
            assignment_artifact_id = _assignment_artifact_id(assignment)
            self.artifact_store.publish(
                assignment_artifact_id,
                _assignment_artifact_document(
                    assignment,
                    analyst_assignment,
                ),
                authority=self.scheduler_artifact_authority,
            )
            channel = self.artifact_store.create_assignment_channel(
                readable_keys=(
                    *analyst_assignment.input_refs,
                    assignment_artifact_id,
                ),
                write_key=result_key,
            )
            analyst_context = replace(
                self.child_context,
                _subagent_mailbox=channel,
                _logical_agent_role="analyst",
                _logical_agent_scope=analyst_assignment.scope,
                _logical_agent_workspace_root=(
                    str(self.transaction.cwd)
                    if self.transaction is not None
                    else self.context.cwd
                ),
            )
            self.plan_result = self.run_assigned_worker(
                {
                    "description": f"analyst:{self.description}",
                    "agent_type": "plan",
                    "_subagent_id": assignment.agent_run_id,
                    "prompt": (
                        "Complete only this immutable Analyst Assignment. "
                        "Every material claim needs a repository path and "
                        "line range. Do not modify files.\n\nASSIGNMENT:\n"
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
                    ),
                },
                analyst_context,
                channel,
            )
            if self.plan_result.ok:
                try:
                    self.analyst_report = parse_role_result(
                        analyst_assignment,
                        _read_role_result_payload(
                            self.artifact_store,
                            result_key,
                        ),
                    )
                except Exception:
                    return StageResult(
                        status="failed",
                        artifact_id=_stage_receipt_artifact_id(assignment),
                        retryable=True,
                        error_code="analyst_result_invalid",
                    )
                try:
                    if self.transaction is None:
                        raise _WorkflowIsolationError(
                            "workflow transaction is unavailable"
                        )
                    evidence_documents = _freeze_analyst_evidence(
                        self.analyst_report,
                        workspace_root=self.transaction.cwd,
                        base_digest=self.transaction.base_digest,
                        stage_assignment=assignment,
                        operation_checkpoint=lambda: _raise_task_deadline(self.context),
                    )
                    for evidence_id, evidence_document in evidence_documents:
                        self.artifact_store.publish(
                            evidence_id,
                            evidence_document,
                            authority=self.scheduler_artifact_authority,
                        )
                        self.analyst_evidence_ids.append(evidence_id)
                    self.analyst_artifact_id = (
                        f"artifact_{assignment.assignment_id}_"
                        f"{assignment.attempt_id}_analyst_handoff"
                    )
                    self.artifact_store.publish(
                        self.analyst_artifact_id,
                        {
                            "handoffVersion": 1,
                            "resultType": "AnalystHandoff.v1",
                            "report": self.analyst_report,
                            "evidenceSnapshotRefs": list(self.analyst_evidence_ids),
                        },
                        authority=self.scheduler_artifact_authority,
                    )
                except _WorkflowIsolationError:
                    return StageResult(
                        status="failed",
                        artifact_id=_stage_receipt_artifact_id(assignment),
                        retryable=True,
                        error_code="analyst_evidence_invalid",
                    )
                if self.analyst_report["status"] != "completed":
                    analyst_status = str(self.analyst_report["status"])
                    return StageResult(
                        status=analyst_status,
                        artifact_id=self.analyst_artifact_id,
                        error_code=f"analyst_result_{analyst_status}",
                    )
                if self.analyst_report["unknowns"]:
                    return StageResult(
                        status="inconclusive",
                        artifact_id=self.analyst_artifact_id,
                        error_code=("analyst_unknowns_require_request_revision"),
                    )
                if analyst_report_requires_reclassification(
                    analyst_assignment,
                    self.analyst_report,
                ):
                    return StageResult(
                        status="blocked",
                        artifact_id=self.analyst_artifact_id,
                        error_code=("analyst_risk_requires_reclassification"),
                    )
        else:
            self.plan_result = self.run_subagent(
                {
                    "description": f"plan:{self.description}",
                    "agent_type": "plan",
                    "_subagent_id": assignment.agent_run_id,
                    "prompt": (
                        "Produce a concrete, step-by-step implementation plan "
                        "for this task. Do not modify files. Write the final "
                        "plan to the shared mailbox with subagent_note_write "
                        "using key workflow_plan. End with <final> and a "
                        "numbered plan plus the files you expect to touch."
                        f"{self.research_notes}\n\nTASK:\n{self.task_prompt}"
                    ),
                },
                self.child_context,
            )
        _raise_task_deadline(self.context)
        if self.mailbox is not None and not self.workflow_request.structured:
            try:
                self.mailbox.write(
                    "workflow_plan",
                    self.plan_result.output,
                    author="workflow",
                )
            except Exception:  # noqa: BLE001 - legacy note is optional
                pass
        terminal_analyst_artifact_id = (
            self.analyst_artifact_id
            if self.plan_result.ok and self.workflow_request.structured
            else _stage_receipt_artifact_id(assignment)
            if self.workflow_request.structured
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
            status="completed" if self.plan_result.ok else "failed",
            artifact_id=terminal_analyst_artifact_id,
            retryable=not self.plan_result.ok,
            error_code=None if self.plan_result.ok else "plan_phase_failed",
        )

    def _run_executor(self, assignment) -> StageResult:
        if self.profile.human_confirmation_required and not _human_approval_is_current(
            self.human_approval_document
        ):
            return StageResult(
                status="blocked",
                artifact_id=(
                    self.human_approval_artifact_id
                    or _stage_receipt_artifact_id(assignment)
                ),
                requires_human=True,
                error_code="human_approval_expired",
            )
        self.executor_assignment_id = assignment.assignment_id
        if self.plan_result is None and not self.workflow_request.structured:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="plan_artifact_missing",
            )
        executor_assignment: Assignment | None = None
        if self.workflow_request.structured:
            if self.transaction is None:
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    error_code="workflow_transaction_missing",
                )
            executor_input_refs = (
                (
                    self.analyst_artifact_id,
                    self.workflow_request_artifact_id,
                    *self.analyst_evidence_ids,
                    *(
                        (self.human_approval_artifact_id,)
                        if self.human_approval_artifact_id is not None
                        else ()
                    ),
                )
                if self.analyst_report is not None
                and self.analyst_artifact_id is not None
                else ()
            )
            executor_assignment = _build_worker_assignment(
                self.workflow_request,
                assignment,
                context=self.context,
                agent_budget=self.agent_budget,
                role="executor",
                mode="implement",
                objective=self.task_prompt,
                input_refs=executor_input_refs,
            )
            candidate = (
                self.analyst_report.get("implementationOrderCandidate")
                if self.analyst_report is not None
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
                            item.criterion_id for item in self.workflow_request.criteria
                        ],
                    }
                    for step in candidate_steps
                    if isinstance(step, dict)
                ]
                self.locked_write_paths = tuple(
                    dict.fromkeys(
                        path for step in raw_steps for path in step["allowedPaths"]
                    )
                )
            else:
                self.locked_write_paths = self.workflow_request.scope.write_paths
                raw_steps = [
                    {
                        "id": f"s_path_{index}",
                        "action": self.task_prompt,
                        "allowedPaths": [path],
                        "criterionIds": [
                            item.criterion_id for item in self.workflow_request.criteria
                        ],
                    }
                    for index, path in enumerate(
                        self.locked_write_paths,
                        start=1,
                    )
                ]
            order_raw = {
                "orderVersion": 1,
                "orderId": (
                    f"order_{assignment.assignment_id}_{assignment.attempt_id}"
                ),
                "assignmentId": assignment.assignment_id,
                "baseDigest": self.transaction.base_digest,
                "allowedPaths": list(self.locked_write_paths),
                "steps": raw_steps,
                "criterionIds": [
                    item.criterion_id for item in self.workflow_request.criteria
                ],
                "verificationRequests": [
                    " ".join(check.argv)
                    for check in self.workflow_request.verification_checks
                ],
            }
            try:
                self.implementation_order = parse_implementation_order(
                    executor_assignment,
                    order_raw,
                )
                self.artifact_store.publish(
                    self.implementation_order.order_id,
                    self.implementation_order.to_dict(),
                    authority=self.scheduler_artifact_authority,
                )
                executor_assignment = _build_worker_assignment(
                    self.workflow_request,
                    assignment,
                    context=self.context,
                    agent_budget=self.agent_budget,
                    role="executor",
                    mode="implement",
                    objective=self.task_prompt,
                    input_refs=(
                        *executor_input_refs,
                        self.implementation_order.order_id,
                    ),
                )
                self.implementation_order = parse_implementation_order(
                    executor_assignment,
                    order_raw,
                )
            except Exception:
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    error_code="implementation_order_invalid",
                )
        effective_plan = self.plan_result or ToolResult(
            ok=True,
            output=(
                "Execute the immutable WorkflowRequest.v1 scope and "
                "acceptance criteria without expanding either."
            ),
        )
        plan_text = (
            effective_plan.output[:12000]
            if not self.workflow_request.structured
            else (
                "The locked ImplementationOrder below is the sole "
                "executable plan authority."
            )
        )
        executor_result_key: str | None = None
        executor_assignment_channel: AssignmentArtifactChannel | None = None
        executor_channel = getattr(self.child_context, "_subagent_mailbox", None)
        if executor_assignment is not None and self.implementation_order is not None:
            executor_result_key = _assignment_result_key(assignment, "exec")
            assignment_artifact_id = _assignment_artifact_id(assignment)
            self.artifact_store.publish(
                assignment_artifact_id,
                _assignment_artifact_document(
                    assignment,
                    executor_assignment,
                ),
                authority=self.scheduler_artifact_authority,
            )
            executor_channel = self.artifact_store.create_assignment_channel(
                readable_keys=(
                    *executor_assignment.input_refs,
                    assignment_artifact_id,
                ),
                write_key=executor_result_key,
            )
            executor_assignment_channel = executor_channel
        executor_context = replace(
            self.child_context,
            _subagent_mailbox=executor_channel,
            _logical_agent_role="executor",
            _logical_agent_scope=(
                executor_assignment.scope
                if executor_assignment is not None
                else {
                    "read_roots": (".",),
                    "write_paths": (".",),
                    "forbidden_paths": (".git",),
                }
            ),
            _logical_agent_workspace_root=(
                str(
                    self.transaction.cwd
                    if self.workflow_request.structured
                    else self.transaction.workspace_root
                )
                if self.transaction is not None
                else self.context.cwd
            ),
        )
        self.execute_result = self.run_assigned_worker(
            {
                "description": f"execute:{self.description}",
                "agent_type": "general",
                "_subagent_id": assignment.agent_run_id,
                "prompt": (
                    "Implement the task below according to the approved plan. "
                    "Read only the Assignment artifacts made available to "
                    "you. Make the changes, record any blocked or failed "
                    "step honestly, and never claim deterministic verification. "
                    "End with <final> plus a concise summary.\n\n"
                    f"ORIGINAL TASK:\n{self.task_prompt}\n\n"
                    f"PLAN:\n{plan_text}"
                    + (
                        "\n\nEXECUTOR ASSIGNMENT:\n"
                        + json.dumps(
                            executor_assignment.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nLOCKED IMPLEMENTATION ORDER:\n"
                        + json.dumps(
                            self.implementation_order.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        if executor_assignment is not None
                        and self.implementation_order is not None
                        else ""
                    )
                ),
                **(
                    {
                        "_result_key": executor_result_key,
                        "_result_instruction": _role_result_instruction(
                            executor_assignment,
                            result_key=executor_result_key,
                            implementation_order=self.implementation_order,
                        ),
                    }
                    if executor_assignment is not None
                    and self.implementation_order is not None
                    and executor_result_key is not None
                    else {}
                ),
            },
            executor_context,
            executor_assignment_channel,
        )
        _raise_task_deadline(self.context)
        if self.mailbox is not None and not self.workflow_request.structured:
            try:
                self.mailbox.write(
                    "workflow_result",
                    self.execute_result.output,
                    author="workflow",
                )
            except Exception:  # noqa: BLE001 - legacy note is optional
                pass
        if not self.execute_result.ok:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="execute_phase_failed",
            )
        if self.transaction is None:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="workflow_transaction_missing",
            )
        executor_declaration: object | None = None
        if executor_assignment is not None and self.implementation_order is not None:
            try:
                if executor_result_key is None:
                    raise ValueError("Executor result key is unavailable")
                executor_declaration = _read_role_result_payload(
                    self.artifact_store,
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
                    self.execution_artifact = parse_executor_declaration(
                        executor_assignment,
                        executor_declaration,
                        implementation_order=self.implementation_order,
                        scheduler_change_set=None,
                    )
                except Exception:
                    return StageResult(
                        status="failed",
                        artifact_id=_stage_receipt_artifact_id(assignment),
                        error_code="executor_declaration_invalid",
                    )
                self.execution_artifact_id = (
                    f"artifact_{assignment.assignment_id}_"
                    f"{assignment.attempt_id}_execution"
                )
                self.artifact_store.publish(
                    self.execution_artifact_id,
                    self.execution_artifact,
                    authority=self.scheduler_artifact_authority,
                )
                return StageResult(
                    status=str(self.execution_artifact["status"]),
                    artifact_id=self.execution_artifact_id,
                    error_code=f"executor_{self.execution_artifact['status']}",
                )
        try:
            self.frozen_workspace_change = self.transaction.freeze(
                allowed_paths=self.locked_write_paths,
                paths_relative_to_cwd=self.workflow_request.structured,
            )
            self.frozen_change_set = self.frozen_workspace_change.change_set_document
        except _WorkflowIsolationError:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="change_set_invalid",
            )
        artifact_id = (
            f"artifact_{assignment.assignment_id}_{assignment.attempt_id}_execution"
        )
        if self.workflow_request.structured:
            try:
                self.change_diff_artifact = (
                    self.frozen_workspace_change.diff_artifact_document
                )
                self.change_diff_artifact_id = (
                    f"artifact_{assignment.assignment_id}_"
                    f"{assignment.attempt_id}_frozen_diff"
                )
                self.artifact_store.publish(
                    self.change_diff_artifact_id,
                    self.change_diff_artifact,
                    authority=self.scheduler_artifact_authority,
                )
            except Exception:
                return StageResult(
                    status="failed",
                    artifact_id=artifact_id,
                    error_code="change_diff_artifact_invalid",
                )
        if executor_assignment is not None and self.implementation_order is not None:
            try:
                self.execution_artifact = parse_executor_declaration(
                    executor_assignment,
                    executor_declaration,
                    implementation_order=self.implementation_order,
                    scheduler_change_set=self.frozen_change_set,
                )
            except Exception:
                return StageResult(
                    status="failed",
                    artifact_id=artifact_id,
                    error_code="execution_result_invalid",
                )
            self.execution_artifact_id = artifact_id
            self.artifact_store.publish(
                self.execution_artifact_id,
                self.execution_artifact,
                authority=self.scheduler_artifact_authority,
            )
        return StageResult(
            status="completed",
            artifact_id=artifact_id,
            patch_digest=str(self.frozen_change_set["patchDigest"]),
        )

    def _run_reviewer(self, assignment) -> StageResult:
        if self.profile.human_confirmation_required and not _human_approval_is_current(
            self.human_approval_document
        ):
            return StageResult(
                status="blocked",
                artifact_id=(
                    self.human_approval_artifact_id
                    or _stage_receipt_artifact_id(assignment)
                ),
                requires_human=True,
                error_code="human_approval_expired",
            )
        if (
            self.execute_result is None
            or self.verification_result is None
            or (
                self.workflow_request.structured
                and (
                    self.execution_artifact_id is None
                    or self.verification_artifact_id is None
                )
            )
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="review_input_missing",
            )
        if self.workflow_request.structured:
            reviewer_input_refs = tuple(
                ref
                for ref in (
                    self.analyst_artifact_id,
                    self.workflow_request_artifact_id,
                    *self.analyst_evidence_ids,
                    (
                        self.implementation_order.order_id
                        if self.implementation_order is not None
                        else None
                    ),
                    self.execution_artifact_id,
                    self.change_diff_artifact_id,
                    self.verification_artifact_id,
                    *self.verification_evidence_ids,
                    self.acceptance_evidence_matrix_id,
                )
                if ref is not None
            )
            reviewer_assignment = _build_worker_assignment(
                self.workflow_request,
                assignment,
                context=self.context,
                agent_budget=self.agent_budget,
                role="reviewer",
                mode="change_review",
                objective=self.task_prompt,
                input_refs=reviewer_input_refs,
                write_paths=self.locked_write_paths,
            )
            result_key = _assignment_result_key(assignment, "review")
            assignment_artifact_id = _assignment_artifact_id(assignment)
            self.artifact_store.publish(
                assignment_artifact_id,
                _assignment_artifact_document(
                    assignment,
                    reviewer_assignment,
                ),
                authority=self.scheduler_artifact_authority,
            )
            channel = self.artifact_store.create_assignment_channel(
                readable_keys=(*reviewer_input_refs, assignment_artifact_id),
                write_key=result_key,
            )
            reviewer_context = replace(
                self.child_context,
                _subagent_mailbox=channel,
                _logical_agent_role="reviewer",
                _logical_agent_scope=reviewer_assignment.scope,
                _logical_agent_workspace_root=(
                    str(self.transaction.cwd)
                    if self.transaction is not None
                    else self.context.cwd
                ),
            )
            self.review_result = self.run_assigned_worker(
                {
                    "description": f"reviewer:{self.description}",
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
                            self.execution_artifact or self.frozen_change_set or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nLOCKED IMPLEMENTATION ORDER:\n"
                        + json.dumps(
                            (
                                self.implementation_order.to_dict()
                                if self.implementation_order is not None
                                else {}
                            ),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nFROZEN DIFF:\n"
                        + json.dumps(
                            self.change_diff_artifact or {},
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n\nVERIFICATION RESULT:\n"
                        + json.dumps(
                            self.verification_result,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                    "_result_key": result_key,
                    "_result_instruction": _role_result_instruction(
                        reviewer_assignment,
                        result_key=result_key,
                    ),
                },
                reviewer_context,
                channel,
            )
            _raise_task_deadline(self.context)
            if not self.review_result.ok:
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    patch_digest=str(self.verification_result["patchDigest"]),
                    retryable=True,
                    error_code="review_phase_failed",
                )
            try:
                review_verdict = parse_role_result(
                    reviewer_assignment,
                    _read_role_result_payload(
                        self.artifact_store,
                        result_key,
                    ),
                )
            except Exception:
                return StageResult(
                    status="failed",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    patch_digest=str(self.verification_result["patchDigest"]),
                    retryable=True,
                    error_code="review_result_invalid",
                )
            if (
                self.acceptance_evidence_matrix_id is None
                or self.acceptance_evidence_matrix is None
                or self.change_diff_artifact_id is None
            ):
                return StageResult(
                    status="failed",
                    artifact_id=result_key,
                    error_code="acceptance_evidence_matrix_missing",
                )
            matrix_by_criterion = {
                str(item["criterionId"]): item
                for item in self.acceptance_evidence_matrix["criteria"]
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
                    self.acceptance_evidence_matrix_id,
                    self.change_diff_artifact_id,
                    *(str(ref) for ref in matrix_entry.get("evidenceRefs", [])),
                }
                if not required_refs.issubset(set(criterion_result["evidenceRefs"])):
                    return StageResult(
                        status="failed",
                        artifact_id=result_key,
                        error_code="review_evidence_binding_invalid",
                    )
            self.typed_review = {
                "reviewVersion": 1,
                "verdict": review_verdict["verdict"],
                "blockingFindings": [
                    item["statement"] for item in review_verdict["blockingFindings"]
                ],
                "warnings": [item["statement"] for item in review_verdict["warnings"]],
            }
            diff_is_complete = bool(
                self.change_diff_artifact
                and self.change_diff_artifact.get("complete") is True
            )
            return StageResult(
                status="completed",
                artifact_id=result_key,
                patch_digest=str(review_verdict["patchDigest"]),
                approved=(review_verdict["verdict"] == "approved" and diff_is_complete),
                error_code=(None if diff_is_complete else "review_evidence_truncated"),
            )
        self.review_result = self.run_subagent(
            {
                "description": f"review:{self.description}",
                "agent_type": "plan",
                "_subagent_id": assignment.agent_run_id,
                "prompt": (
                    "Review the completed work below against the original "
                    "task and deterministic Verification Result. Read "
                    "workflow_plan and workflow_result when available. Do "
                    "not modify files. Before your final response, call "
                    "subagent_note_write with review verdict key "
                    f"`{self.review_key}` and content containing ONLY a JSON "
                    "object with this exact shape: "
                    '{"reviewVersion":1,"verdict":"approved|changes_required|inconclusive",'
                    '"blockingFindings":["..."],"warnings":["..."]}. '
                    "Use approved only when there are no blocking findings; "
                    "a model verdict cannot override failed verification. "
                    "End with <final>.\n\n"
                    f"ORIGINAL TASK:\n{self.task_prompt}\n\n"
                    "DETERMINISTIC VERIFICATION:\n"
                    f"{json.dumps(self.verification_result, sort_keys=True)}\n\n"
                    "IMPLEMENTATION SUMMARY:\n"
                    f"{self.execute_result.output[:12000]}"
                ),
            },
            self.child_context,
        )
        _raise_task_deadline(self.context)
        parsed_review, review_error = _read_workflow_review(
            self.mailbox, self.review_key
        )
        self.typed_review = parsed_review or _workflow_failure_review(
            review_error or "review_verdict_invalid"
        )
        return StageResult(
            status="completed" if self.review_result.ok else "failed",
            artifact_id=_stage_receipt_artifact_id(assignment),
            patch_digest=str(self.verification_result["patchDigest"]),
            approved=self.typed_review["verdict"] == "approved",
            error_code=None if self.review_result.ok else "review_phase_failed",
        )

    def run_verifier(self, assignment) -> StageResult:
        self.verification_evidence_ids.clear()
        if self.profile.human_confirmation_required and not _human_approval_is_current(
            self.human_approval_document
        ):
            return StageResult(
                status="blocked",
                artifact_id=(
                    self.human_approval_artifact_id
                    or _stage_receipt_artifact_id(assignment)
                ),
                requires_human=True,
                error_code="human_approval_expired",
            )
        if (
            self.transaction is None
            or self.frozen_workspace_change is None
            or self.frozen_change_set is None
            or self.executor_assignment_id is None
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="change_set_missing",
            )
        patch_digest = self.frozen_workspace_change.patch_digest
        if not self.requested_checks:
            return StageResult(
                status="inconclusive",
                artifact_id=_stage_receipt_artifact_id(assignment),
                patch_digest=patch_digest,
                error_code="verification_checks_missing",
            )
        from minicode.permissions import PermissionDeniedError, PermissionManager

        permission_cwd = str(self.transaction.source_cwd)
        verification_permissions = getattr(self.context, "permissions", None)
        if verification_permissions is None:
            verification_permissions = PermissionManager(
                permission_cwd,
                prompt=None,
            )
        verifier_permission_gate = (
            verification_permissions if self.workflow_request.structured else None
        )
        try:

            def publish_verification_evidence(
                artifact_id: str,
                evidence: dict[str, object],
            ) -> None:
                self.artifact_store.publish(
                    artifact_id,
                    evidence,
                    authority=self.scheduler_artifact_authority,
                )
                self.verification_evidence_ids.append(artifact_id)

            verifier = _deterministic_verifier_for_context(
                self.context,
                trusted_git_executable=self.transaction.trusted_git_executable,
                untrusted_executable_root=str(self.transaction.source_root),
            )
            isolated_checks: list[dict[str, object]] = []
            isolated_statuses: list[str] = []
            verification_id: str | None = None
            # Every check receives a fresh clone and private HOME. A test,
            # build or linter can therefore never influence the bytes seen
            # by a later gate.
            for check in self.requested_checks:
                with self.frozen_workspace_change.verification_workspace(
                    read_roots=(
                        (".",)
                        if check.kind == "oracle"
                        else self.workflow_request.scope.read_roots
                    ),
                    forbidden_paths=self.workflow_request.scope.forbidden_paths,
                    retain_git_metadata=check.kind == "oracle",
                ) as verification_cwd:
                    partial = verifier.run(
                        checks=(check,),
                        cwd=str(verification_cwd),
                        workflow_id=self.workflow_id,
                        task_id=self.task_id,
                        execution_assignment_id=self.executor_assignment_id,
                        patch_digest=patch_digest,
                        permissions=verifier_permission_gate,
                        permission_cwd=permission_cwd,
                        deadline_monotonic=getattr(
                            self.context,
                            "_task_deadline_monotonic",
                            None,
                        ),
                        cancellation_checkpoint=lambda: _raise_task_deadline(
                            self.context
                        ),
                        evidence_sink=publish_verification_evidence,
                    )
                verification_id = str(partial["verificationId"])
                isolated_checks.extend(partial["checks"])
                isolated_statuses.append(str(partial["status"]))
            aggregate_status = (
                "failed"
                if "failed" in isolated_statuses
                else "inconclusive"
                if "inconclusive" in isolated_statuses
                else "passed"
            )
            self.verification_result = parse_verification_result(
                {
                    "verificationVersion": 1,
                    "verificationId": verification_id,
                    "workflowId": self.workflow_id,
                    "taskId": self.task_id,
                    "executionAssignmentId": self.executor_assignment_id,
                    "patchDigest": patch_digest,
                    "status": aggregate_status,
                    "checks": isolated_checks,
                },
                workflow_id=self.workflow_id,
                task_id=self.task_id,
                execution_assignment_id=self.executor_assignment_id,
                patch_digest=patch_digest,
                required_check_ids=tuple(
                    check.check_id for check in self.requested_checks
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
        verification_status = str(self.verification_result["status"])
        if self.workflow_request.structured:
            referenced_evidence = tuple(
                str(check["artifactRef"])
                for check in self.verification_result["checks"]
            )
            if referenced_evidence != tuple(self.verification_evidence_ids) or any(
                self.artifact_store.read(artifact_id) is None
                for artifact_id in referenced_evidence
            ):
                return StageResult(
                    status="inconclusive",
                    artifact_id=_stage_receipt_artifact_id(assignment),
                    patch_digest=patch_digest,
                    retryable=False,
                    error_code="verification_evidence_missing",
                )
        self.verification_artifact_id = (
            f"artifact_{assignment.assignment_id}_{assignment.attempt_id}_verification"
        )
        self.artifact_store.publish(
            self.verification_artifact_id,
            self.verification_result,
            authority=self.scheduler_artifact_authority,
        )
        if self.workflow_request.structured:
            if self.change_diff_artifact_id is None:
                return StageResult(
                    status="inconclusive",
                    artifact_id=self.verification_artifact_id,
                    patch_digest=patch_digest,
                    error_code="frozen_diff_evidence_missing",
                )
            checks_by_id = {
                str(check["checkId"]): check
                for check in self.verification_result["checks"]
            }
            matrix_criteria: list[dict[str, object]] = []
            for criterion in self.workflow_request.criteria:
                mapped_check_ids = tuple(
                    check.check_id
                    for check in self.workflow_request.verification_checks
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
                        artifact_id=self.verification_artifact_id,
                        patch_digest=patch_digest,
                        error_code="acceptance_evidence_incomplete",
                    )
                matrix_criteria.append(
                    {
                        "criterionId": criterion.criterion_id,
                        "checkIds": list(mapped_check_ids),
                        "evidenceRefs": mapped_evidence,
                    }
                )
            self.acceptance_evidence_matrix_id = (
                "artifact_acceptance_"
                + hashlib.sha256(
                    (f"{self.workflow_id}\0{self.task_id}\0{patch_digest}").encode(
                        "utf-8"
                    )
                ).hexdigest()[:32]
            )
            self.acceptance_evidence_matrix = {
                "matrixVersion": 1,
                "resultType": "AcceptanceEvidenceMatrix.v1",
                "workflowId": self.workflow_id,
                "taskId": self.task_id,
                "patchDigest": patch_digest,
                "verificationArtifactRef": self.verification_artifact_id,
                "frozenDiffRef": self.change_diff_artifact_id,
                "criteria": matrix_criteria,
            }
            self.artifact_store.publish(
                self.acceptance_evidence_matrix_id,
                self.acceptance_evidence_matrix,
                authority=self.scheduler_artifact_authority,
            )
        return StageResult(
            status=(
                "completed" if verification_status == "passed" else verification_status
            ),
            artifact_id=self.verification_artifact_id,
            patch_digest=patch_digest,
            retryable=False,
            error_code=(
                None
                if verification_status == "passed"
                else f"verification_{verification_status}"
            ),
        )

    def run_human(self, assignment) -> StageResult:
        if (
            not self.workflow_request.structured
            or self.transaction is None
            or self.analyst_artifact_id is None
            or self.analyst_report is None
        ):
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="human_approval_context_missing",
            )
        sealed_handoff = _read_role_result_payload(
            self.artifact_store,
            self.analyst_artifact_id,
        )
        evidence_refs = sealed_handoff.get("evidenceSnapshotRefs", [])
        if not isinstance(evidence_refs, list):
            raise _WorkflowIsolationError(
                "sealed Analyst handoff has invalid evidence references"
            )
        approval_documents: dict[str, object] = {
            self.analyst_artifact_id: sealed_handoff,
        }
        for evidence_ref in evidence_refs:
            if not isinstance(evidence_ref, str):
                raise _WorkflowIsolationError(
                    "sealed Analyst evidence reference is invalid"
                )
            approval_documents[evidence_ref] = _read_role_result_payload(
                self.artifact_store,
                evidence_ref,
            )
        stable_analyst_digest = human_approval_analysis_digest(
            sealed_handoff,
            approval_documents,
        )
        (
            self.approval_request_id,
            self.approval_request_document,
            self.human_approval_document,
        ) = _workflow_human_approval(
            self.context,
            self.workflow_request,
            request_digest=self.workflow_request_digest,
            base_digest=self.transaction.base_digest,
            analyst_artifact_id=self.analyst_artifact_id,
            analyst_digest=stable_analyst_digest,
        )
        if self.approval_request_id is None or self.approval_request_document is None:
            return StageResult(
                status="failed",
                artifact_id=_stage_receipt_artifact_id(assignment),
                error_code="human_approval_request_invalid",
            )
        request_artifact_id = f"artifact_{self.approval_request_id}_request"
        self.artifact_store.publish(
            request_artifact_id,
            self.approval_request_document,
            authority=self.scheduler_artifact_authority,
        )
        if self.human_approval_document is None:
            return StageResult(
                status="blocked",
                artifact_id=request_artifact_id,
                requires_human=True,
                error_code="human_approval_required",
            )
        self.human_approval_artifact_id = f"artifact_{self.approval_request_id}"
        self.artifact_store.publish(
            self.human_approval_artifact_id,
            self.human_approval_document,
            authority=self.scheduler_artifact_authority,
        )
        self.profile = replace(
            self.profile,
            human_confirmation_granted=True,
            human_approval_id=self.approval_request_id,
        )
        return StageResult(
            status="completed",
            artifact_id=self.human_approval_artifact_id,
            approved=True,
        )

    def commit_change(self, request) -> bool:
        if self.transaction is None or self.frozen_workspace_change is None:
            return False
        if self.profile.human_confirmation_required and not _human_approval_is_current(
            self.human_approval_document
        ):
            self.commit_error = _WorkflowIsolationError(
                "human approval expired before commit"
            )
            return False
        try:
            self.frozen_workspace_change.promote(
                approved_patch_digest=request.patch_digest,
            )
        except BaseException as exc:
            # Promotion is the commit linearization point. Cleanup, lock
            # release, or observer failures after it may degrade finalization,
            # but can never turn an applied parent change back into "not
            # committed" or suppress its mandatory delivery.
            if self.transaction.parent_promotion_completed:
                return True
            if self.transaction.parent_promotion_unknown:
                raise _WorkflowCommitStateUnknown(
                    "parent promotion state is unknown"
                ) from exc
            if isinstance(exc, _WorkflowIsolationError):
                self.commit_error = exc
                return False
            raise
        return True

    def prepare_commit(self, request) -> bool:
        """Seal commit authority before the parent workspace can be changed."""
        if not self.workflow_request.structured:
            self.commit_ready_status = "not_applicable"
            return True
        if (
            self.transaction is None
            or self.verification_artifact_id is None
            or not request.artifact_ids
        ):
            self.commit_ready_status = "projection_failed"
            return False
        review_artifact_id = request.artifact_ids[-1]

        def artifact_digest(artifact_id: str) -> str:
            artifact = self.artifact_store.read(artifact_id)
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
                for artifact_id in self.artifact_store.list_artifact_ids()
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
                request_digest=self.workflow_request_digest,
                base_digest=self.transaction.base_digest,
                patch_digest=request.patch_digest,
                verification_digest=artifact_digest(self.verification_artifact_id),
                review_digest=artifact_digest(review_artifact_id),
                approval_digest=(
                    artifact_digest(self.human_approval_artifact_id)
                    if self.human_approval_artifact_id is not None
                    else None
                ),
                artifact_set_digest=artifact_set_digest,
            )
        except Exception:  # noqa: BLE001 - malformed authority fails closed
            self.commit_ready_status = "projection_failed"
            return False

        event_sink = getattr(self.context, "_event_sink", None)
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
            getattr(self.context, "_central_workflow_v2_enforced", False) is True
        )
        if not callable(recorder):
            if production_enforced:
                self.commit_ready_status = "recorder_missing"
                return False
            self.commit_ready_status = "not_configured"
            return True
        if production_enforced and not callable(final_audit_recorder):
            self.commit_ready_status = "recorder_missing"
            return False
        _raise_task_deadline(self.context)
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
            self.commit_ready_status = "persistence_failed"
            return False
        self.commit_ready_status = (
            "persisted" if persisted is True else "persistence_failed"
        )
        _raise_task_deadline(self.context)
        return persisted is True

    def record_transition(self, transition) -> None:
        self.transitions.append(transition)
        artifact_id = transition.artifact_id
        if artifact_id is None:
            return
        self.artifact_store.seal_stage_result(
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
            authority=self.scheduler_artifact_authority,
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
