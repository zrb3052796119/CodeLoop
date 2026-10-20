"""Durable, Run-scoped audit records for centrally scheduled workflows.

Scheduler artifacts are content-bearing and therefore do not belong in the
content-free parent event stream or sub-agent journal.  This module stores an
exact, bounded snapshot below the owning Run directory.  Artifact JSON is
written byte-for-byte; a manifest containing the effective classification,
dispatch plan, validated scheduler trace, and artifact digests is written last
as the workflow's commit marker.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from minicode.central_scheduler import (
    SUPPORTED_SCHEDULER_POLICY_VERSIONS,
    CentralScheduler,
    DispatchPlan,
    DispatchStage,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.scheduler_artifacts import (
    MAX_SCHEDULER_ARTIFACT_BYTES,
    MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW,
    MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES,
    SchedulerArtifact,
)
from minicode.review_probe_source import (
    REVIEW_PROBE_SOURCE_PREFIX, review_probe_source_artifact_id, validate_review_probe_source,
)
from minicode.multi_agent_contracts import (
    Assignment,
    ContractValidationError,
    ImplementationOrder,
    WorkflowVerificationCheck,
    REVIEW_COVERAGE_CONSTRAINT_ID,
    REVIEW_COVERAGE_CONSTRAINT_TEXT,
    analyst_report_requires_reclassification,
    parse_assignment,
    parse_implementation_order,
    parse_role_result,
    parse_review_coverage_evidence,
    parse_review_probe_correction,
    review_probe_correction_artifact_id,
    parse_verification_result,
    parse_workflow_request,
)
from minicode.scheduler_trace import (
    extract_scheduler_trace,
    render_scheduler_trace,
)
from minicode.workflow_repair import (
    CONTINUATION_TRANSITION_ERROR,
    REPAIR_TRANSITION_ERROR,
    implementation_repair_input_refs,
    parse_implementation_repair_request,
    review_supports_implementation_repair,
    verification_supports_implementation_repair,
)


SCHEDULER_AUDIT_VERSION = 2
SCHEDULER_COMMIT_READY_VERSION = 1

_WORKFLOW_ID_RE = re.compile(r"^wf_[A-Za-z0-9][A-Za-z0-9_.-]{3,127}$")
_TASK_ID_RE = re.compile(r"^task_[A-Za-z0-9][A-Za-z0-9_.-]{3,127}$")
_RULE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$")
_COMMIT_READY_ID_RE = re.compile(r"^commit_ready_[0-9a-f]{64}$")
_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_WORKFLOWS_PER_RUN = 100
_MAX_MANIFEST_BYTES = 512 * 1024
_MAX_COMMIT_READY_BYTES = 4 * 1024
_FROZEN_DIFF_LIMIT = 12_000
_FROZEN_DIFF_MARKER = b"\n...<truncated>...\n"
_COMMIT_READY_RESULT_TYPE = "SchedulerCommitReady.v1"
_COMMIT_READY_FILENAME = "commit-ready.json"

# A V2 failure trace is evidence, not free-form narration.  Role artifacts
# below provide stronger, content-derived checks where possible; receipts cover
# failures that happen before a role can publish a result.  Keeping the latter
# in a closed stage/outcome vocabulary prevents a trace and its receipt from
# mutually certifying an invented cause.
_V2_TERMINAL_CAUSES: dict[tuple[str, str], frozenset[str | None]] = {
    ("analyst", "failed"): frozenset(
        {
            "analyst_artifact_missing",
            "analyst_evidence_invalid",
            "analyst_result_failed",
            "analyst_result_invalid",
            "plan_phase_failed",
            "stage_port_failed",
        }
    ),
    ("analyst", "blocked"): frozenset(
        {"analyst_risk_requires_reclassification"}
    ),
    ("analyst", "inconclusive"): frozenset(
        {
            "analyst_result_inconclusive",
            "analyst_unknowns_require_request_revision",
        }
    ),
    ("executor", "failed"): frozenset(
        {
            "change_diff_artifact_invalid",
            "change_set_invalid",
            "execute_phase_failed",
            "execution_result_invalid",
            "executor_budget_exceeded",
            "executor_declaration_invalid",
            "executor_failed",
            "implementation_order_invalid",
            "implementation_repair_unavailable",
            "implementation_repair_no_change",
            "stage_port_failed",
            "workflow_transaction_missing",
        }
    ),
    ("executor", "blocked"): frozenset({"executor_blocked"}),
    ("verifier", "failed"): frozenset(
        {
            "change_set_missing",
            "stage_port_failed",
            "verification_failed",
            "verification_permission_denied",
        }
    ),
    ("verifier", "inconclusive"): frozenset(
        {
            "acceptance_evidence_incomplete",
            "frozen_diff_evidence_missing",
            "verification_checks_missing",
            "verification_evidence_missing",
            "verification_inconclusive",
            "verifier_unavailable",
        }
    ),
    ("reviewer", "failed"): frozenset(
        {
            "acceptance_evidence_matrix_missing",
            "review_evidence_binding_invalid",
            "review_input_missing",
            "review_phase_failed",
            "review_result_invalid",
            "stage_port_failed",
        }
    ),
    ("human", "failed"): frozenset(
        {
            "human_approval_context_missing",
            "human_approval_request_invalid",
            "stage_port_failed",
        }
    ),
    ("commit", "failed"): frozenset(
        {"commit_failed", "commit_ready_persistence_failed"}
    ),
    ("human", "waiting_human"): frozenset({"human_approval_required"}),
    ("executor", "waiting_human"): frozenset({"human_approval_expired"}),
    ("verifier", "waiting_human"): frozenset({"human_approval_expired"}),
    ("reviewer", "waiting_human"): frozenset({"human_approval_expired"}),
    ("human", "rejected"): frozenset({"stage_result_invalid"}),
    ("executor", "rejected"): frozenset(
        {"executor_patch_digest_missing", "stage_result_invalid"}
    ),
    ("verifier", "rejected"): frozenset(
        {"stage_result_invalid", "verification_patch_digest_mismatch"}
    ),
    ("reviewer", "rejected"): frozenset(
        {
            "review_evidence_truncated",
            "review_not_approved",
            "review_patch_digest_mismatch",
            "stage_result_invalid",
        }
    ),
    ("analyst", "rejected"): frozenset({"stage_result_invalid"}),
    ("adjudicator", "rejected"): frozenset(
        {
            "adjudicated_reclassification_required",
            "adjudication_conditions_unresolved",
            "adjudication_decision_invalid",
            "adjudication_decision_not_binding",
            "adjudication_human_required",
            "adjudication_inconclusive",
            "stage_result_invalid",
        }
    ),
    ("adjudicator", "failed"): frozenset(
        {
            "adjudication_conflict_missing",
            "adjudication_result_invalid",
            "adjudication_worker_failed",
            "stage_port_failed",
        }
    ),
}
_V2_RETRYABLE_CAUSES: dict[str, frozenset[str]] = {
    "analyst": frozenset(
        {
            "analyst_artifact_missing",
            "analyst_evidence_invalid",
            "analyst_result_invalid",
            "plan_phase_failed",
        }
    ),
    "reviewer": frozenset({"review_phase_failed", "review_result_invalid"}),
    "adjudicator": frozenset({"adjudication_result_invalid"}),
}
_V2_RECEIPT_CAUSES: dict[str, frozenset[str]] = {
    "analyst": frozenset(
        {
            "analyst_artifact_missing",
            "analyst_evidence_invalid",
            "analyst_result_invalid",
            "plan_phase_failed",
        }
    ),
    "executor": frozenset(
        {
            "change_diff_artifact_invalid",
            "change_set_invalid",
            "execute_phase_failed",
            "execution_result_invalid",
            "executor_budget_exceeded",
            "executor_declaration_invalid",
            "implementation_order_invalid",
            "implementation_repair_unavailable",
            "implementation_repair_no_change",
            "workflow_transaction_missing",
        }
    ),
    "verifier": frozenset(
        {
            "change_set_missing",
            "verification_checks_missing",
            "verification_evidence_missing",
            "verification_permission_denied",
            "verifier_unavailable",
        }
    ),
    "reviewer": frozenset(
        {"review_input_missing", "review_phase_failed", "review_result_invalid"}
    ),
    "human": frozenset(
        {"human_approval_context_missing", "human_approval_request_invalid"}
    ),
    "adjudicator": frozenset(
        {
            "adjudication_conflict_missing",
            "adjudication_result_invalid",
            "adjudication_worker_failed",
        }
    ),
}
_V2_ALL_RECEIPT_CAUSES = frozenset(
    cause
    for causes in _V2_RECEIPT_CAUSES.values()
    for cause in causes
)
_V2_NULL_ARTIFACT_CAUSES = frozenset(
    {
        "commit_failed",
        "commit_ready_persistence_failed",
        "global_deadline_exceeded",
        "round_budget_insufficient",
        "stage_port_failed",
        "stage_result_invalid",
    }
)

_COMMIT_READY_FIELDS = frozenset(
    {
        "commitReadyVersion",
        "resultType",
        "commitReadyId",
        "workflowId",
        "taskId",
        "ruleId",
        "requestDigest",
        "baseDigest",
        "patchDigest",
        "verificationDigest",
        "reviewDigest",
        "approvalDigest",
        "artifactSetDigest",
    }
)

_MANIFEST_FIELDS = frozenset(
    {
        "auditVersion",
        "contractMode",
        "workflowId",
        "taskId",
        "effectiveProfile",
        "dispatchPlan",
        "schedulerTrace",
        "artifacts",
    }
)
_PROFILE_FIELDS = frozenset(
    {
        "taskId",
        "kind",
        "risk",
        "complex",
        "parentHasSufficientContext",
        "scopeClear",
        "acceptanceExecutable",
        "humanConfirmationRequired",
        "humanConfirmationGranted",
        "humanApprovalId",
    }
)
_PLAN_FIELDS = frozenset(
    {
        "taskId",
        "policyVersion",
        "ruleId",
        "stages",
        "requiresHuman",
    }
)
_ARTIFACT_INDEX_FIELDS = frozenset(
    {"artifactId", "contentSha256", "byteCount"}
)


class SchedulerAuditError(RuntimeError):
    """Base error for scheduler audit projection or storage."""


class SchedulerAuditValidationError(SchedulerAuditError, ValueError):
    """Caller-supplied scheduler audit state is invalid."""


class SchedulerAuditStorageError(SchedulerAuditError):
    """Persisted scheduler audit state is unavailable or unsafe."""


@dataclass(frozen=True, slots=True)
class SchedulerCommitReady:
    """Immutable authorization binding required immediately before commit."""

    commit_ready_id: str
    workflow_id: str
    task_id: str
    rule_id: str
    request_digest: str
    base_digest: str
    patch_digest: str
    verification_digest: str
    review_digest: str
    approval_digest: str | None
    artifact_set_digest: str
    commit_ready_version: int = SCHEDULER_COMMIT_READY_VERSION

    def to_dict(self) -> dict[str, object]:
        """Return the exact closed ``SchedulerCommitReady.v1`` document."""
        return {
            "commitReadyVersion": self.commit_ready_version,
            "resultType": _COMMIT_READY_RESULT_TYPE,
            "commitReadyId": self.commit_ready_id,
            "workflowId": self.workflow_id,
            "taskId": self.task_id,
            "ruleId": self.rule_id,
            "requestDigest": self.request_digest,
            "baseDigest": self.base_digest,
            "patchDigest": self.patch_digest,
            "verificationDigest": self.verification_digest,
            "reviewDigest": self.review_digest,
            "approvalDigest": self.approval_digest,
            "artifactSetDigest": self.artifact_set_digest,
        }


def _commit_ready_identity_document(
    *,
    workflow_id: object,
    task_id: object,
    rule_id: object,
    request_digest: object,
    base_digest: object,
    patch_digest: object,
    verification_digest: object,
    review_digest: object,
    approval_digest: object,
    artifact_set_digest: object,
) -> dict[str, object]:
    if (
        not isinstance(workflow_id, str)
        or _WORKFLOW_ID_RE.fullmatch(workflow_id) is None
        or not isinstance(task_id, str)
        or _TASK_ID_RE.fullmatch(task_id) is None
        or not isinstance(rule_id, str)
        or _RULE_ID_RE.fullmatch(rule_id) is None
    ):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready routing identity is invalid"
        )
    digests = (
        request_digest,
        base_digest,
        patch_digest,
        verification_digest,
        review_digest,
        artifact_set_digest,
    )
    if any(
        not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None
        for digest in digests
    ):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready digest binding is invalid"
        )
    if approval_digest is not None and (
        not isinstance(approval_digest, str)
        or _DIGEST_RE.fullmatch(approval_digest) is None
    ):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready approval binding is invalid"
        )
    return {
        "commitReadyVersion": SCHEDULER_COMMIT_READY_VERSION,
        "resultType": _COMMIT_READY_RESULT_TYPE,
        "workflowId": workflow_id,
        "taskId": task_id,
        "ruleId": rule_id,
        "requestDigest": request_digest,
        "baseDigest": base_digest,
        "patchDigest": patch_digest,
        "verificationDigest": verification_digest,
        "reviewDigest": review_digest,
        "approvalDigest": approval_digest,
        "artifactSetDigest": artifact_set_digest,
    }


def _derive_commit_ready_id(identity: Mapping[str, object]) -> str:
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "commit_ready_" + hashlib.sha256(encoded).hexdigest()


def project_scheduler_commit_ready(
    *,
    workflow_id: object,
    task_id: object,
    rule_id: object,
    request_digest: object,
    base_digest: object,
    patch_digest: object,
    verification_digest: object,
    review_digest: object,
    approval_digest: object,
    artifact_set_digest: object,
) -> SchedulerCommitReady:
    """Validate all commit authority bindings and derive their stable ID."""
    identity = _commit_ready_identity_document(
        workflow_id=workflow_id,
        task_id=task_id,
        rule_id=rule_id,
        request_digest=request_digest,
        base_digest=base_digest,
        patch_digest=patch_digest,
        verification_digest=verification_digest,
        review_digest=review_digest,
        approval_digest=approval_digest,
        artifact_set_digest=artifact_set_digest,
    )
    return SchedulerCommitReady(
        commit_ready_id=_derive_commit_ready_id(identity),
        workflow_id=str(identity["workflowId"]),
        task_id=str(identity["taskId"]),
        rule_id=str(identity["ruleId"]),
        request_digest=str(identity["requestDigest"]),
        base_digest=str(identity["baseDigest"]),
        patch_digest=str(identity["patchDigest"]),
        verification_digest=str(identity["verificationDigest"]),
        review_digest=str(identity["reviewDigest"]),
        approval_digest=(
            str(identity["approvalDigest"])
            if identity["approvalDigest"] is not None
            else None
        ),
        artifact_set_digest=str(identity["artifactSetDigest"]),
    )


def parse_scheduler_commit_ready(value: object) -> SchedulerCommitReady:
    """Parse only an exact, self-consistent ``SchedulerCommitReady.v1``."""
    if not isinstance(value, dict) or set(value) != _COMMIT_READY_FIELDS:
        raise SchedulerAuditValidationError(
            "scheduler commit-ready document has an invalid schema"
        )
    if (
        type(value.get("commitReadyVersion")) is not int
        or value["commitReadyVersion"] != SCHEDULER_COMMIT_READY_VERSION
        or value.get("resultType") != _COMMIT_READY_RESULT_TYPE
    ):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready version is invalid"
        )
    projected = project_scheduler_commit_ready(
        workflow_id=value.get("workflowId"),
        task_id=value.get("taskId"),
        rule_id=value.get("ruleId"),
        request_digest=value.get("requestDigest"),
        base_digest=value.get("baseDigest"),
        patch_digest=value.get("patchDigest"),
        verification_digest=value.get("verificationDigest"),
        review_digest=value.get("reviewDigest"),
        approval_digest=value.get("approvalDigest"),
        artifact_set_digest=value.get("artifactSetDigest"),
    )
    commit_ready_id = value.get("commitReadyId")
    if (
        not isinstance(commit_ready_id, str)
        or _COMMIT_READY_ID_RE.fullmatch(commit_ready_id) is None
        or commit_ready_id != projected.commit_ready_id
    ):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready identity is inconsistent"
        )
    return projected


def human_approval_analysis_digest(
    handoff: object,
    artifact_documents: Mapping[str, object],
) -> str:
    """Hash stable Analyst semantics, excluding run/attempt identifiers."""
    if (
        not isinstance(handoff, dict)
        or handoff.get("resultType") != "AnalystHandoff.v1"
        or set(handoff)
        != {
            "handoffVersion",
            "resultType",
            "report",
            "evidenceSnapshotRefs",
        }
        or handoff.get("handoffVersion") != 1
    ):
        raise SchedulerAuditValidationError("Analyst handoff is invalid")
    report = handoff.get("report")
    refs = handoff.get("evidenceSnapshotRefs")
    if not isinstance(report, dict) or not isinstance(refs, list):
        raise SchedulerAuditValidationError("Analyst handoff is invalid")
    stable_report = {
        key: value for key, value in report.items() if key != "assignmentId"
    }
    stable_evidence: list[dict[str, object]] = []
    for ref in refs:
        evidence = artifact_documents.get(str(ref))
        if (
            not isinstance(ref, str)
            or not isinstance(evidence, dict)
            or evidence.get("resultType") != "EvidenceSnapshot.v1"
        ):
            raise SchedulerAuditValidationError(
                "Analyst handoff evidence is invalid"
            )
        stable_evidence.append(
            {
                key: value
                for key, value in evidence.items()
                if key not in {"assignmentId", "attemptId"}
            }
        )
    projected = {
        "analysisVersion": 1,
        "report": stable_report,
        "evidenceSnapshots": stable_evidence,
    }
    encoded = json.dumps(
        projected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SchedulerAudit:
    """One sealed workflow audit joined to its exact canonical artifacts."""

    profile: TaskProfile
    plan: DispatchPlan
    trace: dict[str, object]
    artifacts: tuple[SchedulerArtifact, ...]
    contract_mode: str = "legacy"
    audit_version: int = SCHEDULER_AUDIT_VERSION

    @property
    def workflow_id(self) -> str:
        return str(self.trace["workflowId"])

    @property
    def task_id(self) -> str:
        return self.profile.task_id


@dataclass(frozen=True, slots=True)
class SchedulerAuditSummary:
    """Content-free discovery record for one sealed scheduler audit."""

    workflow_id: str
    task_id: str
    status: str
    rule_id: str
    committed: bool | None
    terminal_reason: str | None
    artifact_count: int


def _closed_mapping(
    value: object,
    fields: frozenset[str],
    *,
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise SchedulerAuditValidationError(f"{label} has an invalid schema")
    return value


def _closed_json_document(content: str) -> object:
    def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SchedulerAuditValidationError(
                    "scheduler artifact contains duplicate JSON keys"
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        raise SchedulerAuditValidationError(
            "scheduler artifact contains non-finite JSON"
        )

    try:
        return json.loads(
            content,
            object_pairs_hook=closed_object,
            parse_constant=reject_constant,
        )
    except SchedulerAuditValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise SchedulerAuditValidationError(
            "scheduler artifact is not valid JSON"
        ) from exc


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if depth > 64:
        raise SchedulerAuditValidationError("scheduler artifact JSON is too deep")
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise SchedulerAuditValidationError(
                "scheduler artifact contains non-finite JSON"
            )
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise SchedulerAuditValidationError(
                "scheduler artifact JSON keys must be strings"
            )
        for item in value.values():
            _validate_json_value(item, depth=depth + 1)
        return
    raise SchedulerAuditValidationError("scheduler artifact is not JSON-safe")


def _canonical_artifact_content(artifact: SchedulerArtifact) -> bytes:
    if not isinstance(artifact, SchedulerArtifact):
        raise SchedulerAuditValidationError(
            "scheduler audit artifacts must be SchedulerArtifact values"
        )
    if _ARTIFACT_ID_RE.fullmatch(artifact.artifact_id) is None:
        raise SchedulerAuditValidationError("scheduler artifact id is invalid")
    if not isinstance(artifact.content, str):
        raise SchedulerAuditValidationError("scheduler artifact content is invalid")
    try:
        encoded = artifact.content.encode("utf-8")
    except UnicodeError as exc:
        raise SchedulerAuditValidationError("scheduler artifact encoding is invalid") from exc
    if not encoded or len(encoded) > MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES:
        raise SchedulerAuditValidationError("scheduler artifact size is invalid")
    parsed = _closed_json_document(artifact.content)
    _validate_json_value(parsed)
    probe_source = (artifact.artifact_id.startswith(REVIEW_PROBE_SOURCE_PREFIX)
                    or isinstance(parsed, dict) and parsed.get("resultType") == "ReviewProbeSource.v1")
    if probe_source:
        # Source is host data, never a model-owned role contract. Validate its
        # complete identity independently before the shared total-byte bound.
        if (not validate_review_probe_source(parsed)
                or artifact.artifact_id != review_probe_source_artifact_id(parsed["probeId"])):
            raise SchedulerAuditValidationError("scheduler review probe source identity is invalid")
    elif len(encoded) > MAX_SCHEDULER_ARTIFACT_BYTES:
        # Other large artifacts must independently prove a complete frozen
        # patch; ordinary role contracts retain their original limit.
        _validate_frozen_diff(parsed,
                             base_digest=parsed.get("baseDigest") if isinstance(parsed, dict) else None,
                             patch_digest=parsed.get("patchDigest") if isinstance(parsed, dict) else None,
                             require_complete=True)
    canonical = json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical != artifact.content:
        raise SchedulerAuditValidationError(
            "scheduler artifact content is not canonical JSON"
        )
    return encoded


def _normalize_trace(trace: object) -> dict[str, object]:
    if not isinstance(trace, Mapping):
        raise SchedulerAuditValidationError("scheduler trace is invalid")
    try:
        rendered = render_scheduler_trace(trace)
    except (TypeError, ValueError) as exc:
        raise SchedulerAuditValidationError("scheduler trace is invalid") from exc
    normalized = extract_scheduler_trace(rendered)
    if normalized is None:
        raise SchedulerAuditValidationError("scheduler trace is invalid")
    return normalized


def _validate_v2_transition_causes(
    trace: Mapping[str, object],
    *,
    receipt_artifact_ids: frozenset[str],
) -> None:
    """Validate scheduler-owned V2 causes independently of trace narration."""
    transitions = trace.get("transitions")
    if not isinstance(transitions, list):  # guarded by trace normalization
        raise SchedulerAuditValidationError("scheduler V2 transitions are invalid")
    repair_assignments = {
        transitions[index + 1]["assignmentId"]
        for index, item in enumerate(transitions[:-1])
        if item.get("outcome") == "repairing"
    }
    previous: Mapping[str, object] | None = None
    for transition in transitions:
        if not isinstance(transition, Mapping):  # guarded by normalization
            raise SchedulerAuditValidationError(
                "scheduler V2 transition is invalid"
            )
        stage = str(transition.get("stage"))
        outcome = str(transition.get("outcome"))
        error_code = transition.get("errorCode")
        artifact_id = transition.get("artifactId")
        if error_code in {
            "implementation_repair_unavailable", "implementation_repair_no_change"
        } and transition.get("assignmentId") not in repair_assignments:
            raise SchedulerAuditValidationError("scheduler repair failure lacks its repair assignment")

        if outcome in {"completed", "dispatched", "commit_requested"}:
            if error_code is not None:
                raise SchedulerAuditValidationError(
                    "scheduler V2 successful transition has a terminal cause"
                )
        elif outcome == "timed_out":
            if (
                error_code not in {
                    "global_deadline_exceeded",
                    "round_budget_insufficient",
                }
                or artifact_id is not None
            ):
                raise SchedulerAuditValidationError(
                    "scheduler V2 timeout cause is inconsistent"
                )
        elif outcome == "repairing":
            if (
                stage not in {"verifier", "reviewer"}
                or error_code not in {REPAIR_TRANSITION_ERROR, CONTINUATION_TRANSITION_ERROR}
                or previous is None
                or previous.get("outcome") not in {"failed", "inconclusive", "rejected"}
                or artifact_id is None
                or any(previous.get(key) != transition.get(key) for key in (
                    "stage", "assignmentId", "attemptId", "agentRunId", "artifactId"
                ))
            ):
                raise SchedulerAuditValidationError("scheduler V2 repair cause is inconsistent")
        elif outcome == "retrying":
            allowed = _V2_RETRYABLE_CAUSES.get(stage, frozenset())
            if (
                not isinstance(error_code, str)
                or error_code not in allowed
                or artifact_id is not None
                or previous is None
                or previous.get("stage") != stage
                or previous.get("assignmentId")
                != transition.get("assignmentId")
                or previous.get("attemptId") != transition.get("attemptId")
                or previous.get("agentRunId") != transition.get("agentRunId")
                or previous.get("errorCode") != error_code
                or previous.get("outcome")
                not in {"blocked", "failed", "inconclusive", "rejected"}
            ):
                raise SchedulerAuditValidationError(
                    "scheduler V2 retry cause is inconsistent"
                )
        else:
            allowed = _V2_TERMINAL_CAUSES.get((stage, outcome), frozenset())
            if error_code not in allowed:
                raise SchedulerAuditValidationError(
                    "scheduler V2 terminal cause is outside the closed policy"
                )

        if outcome in {"dispatched", "retrying", "commit_requested"} and (
            artifact_id is not None
        ):
            raise SchedulerAuditValidationError(
                "scheduler V2 control transition cannot reference an artifact"
            )
        if stage == "commit" and artifact_id is not None:
            raise SchedulerAuditValidationError(
                "scheduler V2 commit transition cannot reference an artifact"
            )
        if error_code in {"stage_port_failed", "stage_result_invalid"} and (
            artifact_id is not None
        ):
            raise SchedulerAuditValidationError(
                "scheduler V2 scheduler-owned failure cannot cite role evidence"
            )
        if outcome not in {"retrying", "repairing"}:
            is_receipt = artifact_id in receipt_artifact_ids
            receipt_causes = _V2_RECEIPT_CAUSES.get(stage, frozenset())
            cause_requires_receipt = error_code in receipt_causes
            if is_receipt != cause_requires_receipt:
                raise SchedulerAuditValidationError(
                    "scheduler V2 stage receipt cause is inconsistent"
                )
            if (
                isinstance(error_code, str)
                and error_code in _V2_ALL_RECEIPT_CAUSES
                and error_code not in receipt_causes
            ):
                raise SchedulerAuditValidationError(
                    "scheduler V2 stage receipt cause belongs to another stage"
                )
            if error_code in _V2_NULL_ARTIFACT_CAUSES:
                if artifact_id is not None:
                    raise SchedulerAuditValidationError(
                        "scheduler V2 scheduler cause must not cite an artifact"
                    )
            elif outcome not in {
                "completed",
                "dispatched",
                "commit_requested",
            } and not cause_requires_receipt:
                if not isinstance(artifact_id, str) or is_receipt:
                    raise SchedulerAuditValidationError(
                        "scheduler V2 semantic cause requires role authority"
                    )
        previous = transition


def _role_payload(document: object) -> object:
    if not isinstance(document, dict):
        return document
    return {
        key: value
        for key, value in document.items()
        if key != "resultType"
    }


def _path_is_within_requested_scope(
    path: str,
    roots: tuple[str, ...],
) -> bool:
    return any(
        (root == "." and not path.startswith("/"))
        or path == root
        or path.startswith(root.rstrip("/") + "/")
        for root in roots
    )


def _order_scope_within_assignment(
    order: ImplementationOrder,
    assignment: Assignment,
) -> bool:
    """An Executor order may narrow — never widen — the Assignment write scope.

    The order contract parser already rejects order paths outside the
    Assignment scope; this audit check keeps that fail-closed direction but
    accepts a strict subset, since an order that writes less than the
    Assignment allows is a legitimate narrowing by the Analyst.  An empty
    ``allowed_paths`` is legal: the order writes nothing at all.
    """
    return all(
        _path_is_within_requested_scope(path, assignment.scope.write_paths)
        for path in order.allowed_paths
    )


def _validate_analyst_completion(
    assignment: Assignment,
    *,
    attempt_id: str,
    document: object,
    artifact_documents: Mapping[str, object],
    require_completed: bool = True,
) -> dict[str, object]:
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "handoffVersion",
            "resultType",
            "report",
            "evidenceSnapshotRefs",
        }
        or document.get("handoffVersion") != 1
        or document.get("resultType") != "AnalystHandoff.v1"
        or not isinstance(document.get("report"), dict)
        or not isinstance(document.get("evidenceSnapshotRefs"), list)
    ):
        raise SchedulerAuditValidationError(
            "scheduler Analyst handoff is invalid"
        )
    report = document["report"]
    try:
        parsed_report = parse_role_result(
            assignment,
            _role_payload(report),
        )
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise SchedulerAuditValidationError(
            "scheduler AnalystReport is invalid"
        ) from exc
    if parsed_report != report or (
        require_completed and parsed_report.get("status") != "completed"
    ):
        raise SchedulerAuditValidationError(
            "scheduler AnalystReport is not canonical"
        )

    refs = document["evidenceSnapshotRefs"]
    flattened_claims: list[tuple[str, dict[str, object]]] = []
    for finding in parsed_report["findings"]:
        for evidence in finding["evidence"]:
            flattened_claims.append((str(finding["claimId"]), evidence))
    if (
        len(refs) != len(flattened_claims)
        or len(set(str(ref) for ref in refs)) != len(refs)
    ):
        raise SchedulerAuditValidationError(
            "scheduler Analyst evidence mapping is incomplete"
        )
    fields = {
        "evidenceSnapshotVersion",
        "resultType",
        "assignmentId",
        "attemptId",
        "claimId",
        "baseDigest",
        "path",
        "lineStart",
        "lineEnd",
        "fileDigest",
        "contentDigest",
    }
    for ref, (claim_id, source) in zip(refs, flattened_claims, strict=True):
        evidence = artifact_documents.get(str(ref))
        if (
            not isinstance(ref, str)
            or not isinstance(evidence, dict)
            or set(evidence) != fields
            or evidence.get("evidenceSnapshotVersion") != 1
            or evidence.get("resultType") != "EvidenceSnapshot.v1"
            or evidence.get("assignmentId") != assignment.assignment_id
            or evidence.get("attemptId") != attempt_id
            or evidence.get("claimId") != claim_id
            or evidence.get("path") != source["path"]
            or evidence.get("lineStart") != source["lineStart"]
            or evidence.get("lineEnd") != source["lineEnd"]
            or _DIGEST_RE.fullmatch(str(evidence.get("baseDigest"))) is None
            or _DIGEST_RE.fullmatch(str(evidence.get("fileDigest"))) is None
            or _DIGEST_RE.fullmatch(str(evidence.get("contentDigest"))) is None
        ):
            raise SchedulerAuditValidationError(
                "scheduler Analyst evidence is invalid"
            )
    return parsed_report


def _adjudicated_continuation_attempts(
    normalized_trace: Mapping[str, object],
    assignments_by_attempt: Mapping[tuple[str, str], Assignment],
    artifact_documents: Mapping[str, object],
) -> set[tuple[str, str]]:
    """Attempts resumed by a binding adjudication that selected continuation.

    An escalated blocked Worker attempt may legally reach ``completed`` when
    the following ADJUDICATOR block's validated verdict chose ``select_b``
    with no outstanding conditions —
    the continuation claim ordered second in its frozen ConflictPackage.
    """

    transitions = normalized_trace.get("transitions")
    if not isinstance(transitions, list):
        return set()
    resumed: set[tuple[str, str]] = set()
    index = 0
    while index < len(transitions):
        item = transitions[index]
        if not isinstance(item, Mapping) or (
            item.get("stage") != "adjudicator"
            or item.get("outcome") != "dispatched"
        ):
            index += 1
            continue
        blocked = (
            transitions[index - 1]
            if index > 0 and isinstance(transitions[index - 1], Mapping)
            else None
        )
        if blocked is None or blocked.get("outcome") != "blocked":
            index += 1
            continue
        verdict_document = None
        cursor = index
        while cursor < len(transitions):
            step = transitions[cursor]
            if not isinstance(step, Mapping) or step.get("stage") != "adjudicator":
                break
            if step.get("outcome") == "completed":
                verdict_document = artifact_documents.get(
                    str(step.get("artifactId"))
                )
            cursor += 1
        if isinstance(verdict_document, dict):
            verdict = _role_payload(verdict_document)
            assignment = assignments_by_attempt.get(
                (
                    str(
                        transitions[index].get("assignmentId")
                    ),
                    str(transitions[index].get("attemptId")),
                )
            )
            if assignment is not None:
                conflict_id = verdict.get("conflictId") if isinstance(
                    verdict, Mapping
                ) else None
                package = next(
                    (
                        document
                        for document in artifact_documents.values()
                        if isinstance(document, dict)
                        and document.get("resultType") == "ConflictPackage.v1"
                        and document.get("conflictId") == conflict_id
                    ),
                    None,
                )
                try:
                    parsed = parse_role_result(
                        assignment,
                        verdict,
                        conflict_package=package,
                    )
                except (ContractValidationError, TypeError, ValueError):
                    parsed = None
                if (
                    isinstance(parsed, dict)
                    and parsed.get("decision") == "select_b"
                    and parsed.get("conditions") == []
                ):
                    resumed.add(
                        (
                            str(blocked.get("assignmentId")),
                            str(blocked.get("attemptId")),
                        )
                    )
        index = cursor
    return resumed


def _expected_analyst_transition(
    assignment: Assignment,
    report: Mapping[str, object],
) -> tuple[str, str | None]:
    """Replay the scheduler-owned Analyst gate in its exact precedence."""
    if report.get("status") != "completed":
        status = str(report.get("status"))
        return status, f"analyst_result_{status}"
    unknowns = report.get("unknowns")
    if isinstance(unknowns, list) and unknowns:
        return "inconclusive", "analyst_unknowns_require_request_revision"
    if analyst_report_requires_reclassification(assignment, report):
        return "blocked", "analyst_risk_requires_reclassification"
    return "completed", None


def _validate_verification_stream(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "byteCount",
        "digest",
        "preview",
        "previewBase64",
        "truncated",
    }:
        return False
    byte_count = value.get("byteCount")
    preview = value.get("preview")
    preview_base64 = value.get("previewBase64")
    if not (
        type(byte_count) is int
        and byte_count >= 0
        and _DIGEST_RE.fullmatch(str(value.get("digest"))) is not None
        and isinstance(preview, str)
        and isinstance(preview_base64, str)
        and len(preview) <= 5_000
        and len(preview_base64) <= 8_000
        and type(value.get("truncated")) is bool
        and value["truncated"] == (byte_count > 4_000)
    ):
        return False
    try:
        encoded_preview = base64.b64decode(preview_base64, validate=True)
    except (binascii.Error, ValueError):
        return False
    if preview != encoded_preview.decode("utf-8", errors="replace"):
        return False
    if value["truncated"] is False:
        return (
            len(encoded_preview) == byte_count
            and "sha256:" + hashlib.sha256(encoded_preview).hexdigest()
            == value["digest"]
        )
    return "\n...<truncated>...\n" in preview


def _validate_verification_evidence(
    document: object,
    *,
    artifact_id: str,
    workflow_id: str,
    execution_assignment_id: str,
    patch_digest: str,
    result_check: Mapping[str, object],
    request_check: object,
) -> None:
    fields = {
        "evidenceVersion",
        "checkId",
        "kind",
        "argv",
        "resolvedExecutable",
        "commandDigest",
        "status",
        "exitCode",
        "durationMs",
        "failureReason",
        "stdout",
        "stderr",
    }
    argv = tuple(getattr(request_check, "argv", ()))
    expected_id = "artifact_verification_" + hashlib.sha256(
        (
            f"{workflow_id}\0{execution_assignment_id}\0"
            f"{patch_digest}\0{result_check.get('checkId')}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    expected_command_digest = "sha256:" + hashlib.sha256(
        "\0".join(argv).encode("utf-8")
    ).hexdigest()
    resolved = document.get("resolvedExecutable") if isinstance(document, dict) else None
    failure_reason = document.get("failureReason") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document.get("evidenceVersion") != 1
        or artifact_id != expected_id
        or document.get("checkId") != result_check.get("checkId")
        or document.get("kind") != result_check.get("kind")
        or document.get("kind") != getattr(request_check, "kind", None)
        or document.get("argv") != list(argv)
        or document.get("commandDigest") != expected_command_digest
        or document.get("status") != result_check.get("status")
        or document.get("exitCode") != result_check.get("exitCode")
        or document.get("durationMs") != result_check.get("durationMs")
        or not (resolved is None or isinstance(resolved, str))
        or not (
            failure_reason is None
            or (
                isinstance(failure_reason, str)
                and _RULE_ID_RE.fullmatch(failure_reason) is not None
            )
        )
        or (
            result_check.get("status") in {"passed", "failed"}
            and failure_reason is not None
        )
        or not _validate_verification_stream(document.get("stdout"))
        or not _validate_verification_stream(document.get("stderr"))
    ):
        raise SchedulerAuditValidationError(
            "scheduler verification evidence is invalid"
        )


def _validate_frozen_diff(
    document: object,
    *,
    base_digest: str,
    patch_digest: str,
    require_complete: bool,
) -> None:
    fields = {
        "diffVersion",
        "baseDigest",
        "patchDigest",
        "byteCount",
        "complete",
        "encoding",
        "diff",
    }
    diff = document.get("diff") if isinstance(document, dict) else None
    byte_count = document.get("byteCount") if isinstance(document, dict) else None
    complete = document.get("complete") if isinstance(document, dict) else None
    encoding = document.get("encoding") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or type(document.get("diffVersion")) is not int
        or document.get("diffVersion") != 1
        or not isinstance(base_digest, str) or _DIGEST_RE.fullmatch(base_digest) is None
        or not isinstance(patch_digest, str) or _DIGEST_RE.fullmatch(patch_digest) is None
        or document.get("baseDigest") != base_digest
        or document.get("patchDigest") != patch_digest
        or encoding not in {"utf-8", "base64"}
        or type(byte_count) is not int
        or byte_count < 0
        or type(complete) is not bool
        or not isinstance(diff, str)
        or (complete is not True and len(diff) > 36_100)
    ):
        raise SchedulerAuditValidationError(
            "scheduler frozen diff is invalid"
        )
    if encoding == "base64":
        try:
            encoded = base64.b64decode(diff, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SchedulerAuditValidationError(
                "scheduler frozen diff is invalid"
            ) from exc
        if diff != base64.b64encode(encoded).decode("ascii"):
            raise SchedulerAuditValidationError(
                "scheduler frozen diff is invalid"
            )
    else:
        try:
            encoded = diff.encode("utf-8")
        except UnicodeError as exc:
            raise SchedulerAuditValidationError(
                "scheduler frozen diff is invalid"
            ) from exc
    if complete:
        try:
            # Full patch evidence uses the existing workflow storage budget.
            # Independently encode it, then bind every byte to digest/count.
            document_size = len(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, UnicodeError) as exc:
            raise SchedulerAuditValidationError(
                "scheduler frozen diff is invalid"
            ) from exc
        if (
            encoding != "utf-8"
            or document_size > MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES
            or len(encoded) != byte_count
            or "sha256:" + hashlib.sha256(encoded).hexdigest() != patch_digest
        ):
            raise SchedulerAuditValidationError(
                "scheduler frozen diff is invalid"
            )
    elif byte_count <= _FROZEN_DIFF_LIMIT:
        # An unreadable or JSON-expansive full patch is retained as exact
        # bounded base64 evidence, but may never authorize commit.
        if (
            encoding != "base64"
            or len(encoded) != byte_count
            or "sha256:" + hashlib.sha256(encoded).hexdigest() != patch_digest
        ):
            raise SchedulerAuditValidationError(
                "scheduler frozen diff is invalid"
            )
    else:
        if (
            len(encoded) != _FROZEN_DIFF_LIMIT + len(_FROZEN_DIFF_MARKER)
            or encoded[
                _FROZEN_DIFF_LIMIT // 2 :
                _FROZEN_DIFF_LIMIT // 2 + len(_FROZEN_DIFF_MARKER)
            ]
            != _FROZEN_DIFF_MARKER
        ):
            raise SchedulerAuditValidationError(
                "scheduler frozen diff is invalid"
            )
    if require_complete and complete is not True:
        raise SchedulerAuditValidationError(
            "committed scheduler frozen diff must be complete"
        )


def _has_pending_executor_feedback(document: object) -> bool:
    return (
        isinstance(document, Mapping)
        and document.get("resultType") == "ExecutionCandidate.v1"
        and document.get("status") == "awaiting_verification"
        and document.get("handoffReason") in {
            "executor_write_feedback_pending", "executor_submission_feedback_pending",
        }
    )


def _validate_review_probe_command(receipt, *, documents, requested_checks) -> None:
    """Authorize a fixed host derivation or an exact historical command."""
    if "executionBinding" in receipt:
        from minicode.review_probe_execution import validate_review_probe_execution_binding

        try:
            checks = tuple(check.to_execution_check() if isinstance(check, WorkflowVerificationCheck)
                           else check for check in requested_checks)
            validate_review_probe_execution_binding(dict(receipt), checks,
                probe_source_digest=documents[receipt["sourceRef"]]["sourceDigest"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SchedulerAuditValidationError("review probe command exceeds the approved checks") from exc
        return
    evidence = receipt["evidence"]
    approved_commands = {(check.check_id, check.kind, tuple(check.argv)) for check in requested_checks}
    if (evidence["checkId"], evidence["kind"], tuple(evidence["argv"])) not in approved_commands:
        raise SchedulerAuditValidationError("review probe command exceeds the approved checks")


def _review_probe_repair_refs(documents, *, assignment_id: str, patch_digest: str, requested_checks) -> tuple[str, ...]:
    """Recover exact latest probe feedback, without granting review approval."""
    latest = {}
    for ref, document in documents.items():
        if (not isinstance(document, Mapping) or document.get("resultType") != "ReviewProbeExecution.v1"
                or document.get("assignmentId") != assignment_id or document.get("patchDigest") != patch_digest):
            continue
        probe_id, sequence = document.get("probeId"), document.get("sequence")
        if not isinstance(probe_id, str) or type(sequence) is not int or sequence < 1:
            raise SchedulerAuditValidationError("repair review probe history is invalid")
        previous = latest.get(probe_id)
        if previous is None or sequence > previous[1]["sequence"]:
            latest[probe_id] = (ref, document)
    if not latest:
        return ()
    owner = next((document.get("assignment") for document in documents.values()
                  if isinstance(document, Mapping) and document.get("resultType") == "AssignmentArtifact.v1"
                  and document.get("stage") == "reviewer" and isinstance(document.get("assignment"), Mapping)
                  and document["assignment"].get("assignmentId") == assignment_id), None)
    try:
        assignment = parse_assignment(owner)
        rows = [{"criterionId": criterion.criterion_id, "rationale": "Retained probe feedback, not an approval.",
                 "probeIds": [], "sourceEvidence": []} for criterion in assignment.acceptance_criteria]
        probes = [{"probeId": document["probeId"], "sourceRef": document.get("sourceRef"), "receiptRef": ref}
                  for ref, document in sorted(latest.values(), key=lambda item: item[0])]
        # This validation envelope is never published as a model verdict or
        # coverage artifact. Unknown decisions confer no completion authority;
        # it reuses the full source/receipt parser for the repair's input refs.
        parse_review_coverage_evidence(assignment, {
            "coverageVersion": 1, "resultType": "ReviewCoverageEvidence.v1",
            "workflowId": assignment.workflow_id, "taskId": assignment.task_id,
            "assignmentId": assignment_id, "patchDigest": patch_digest, "criteria": rows, "probes": probes,
        }, review={"assignmentId": assignment_id, "patchDigest": patch_digest, "verdict": "inconclusive",
                   "criteria": [{"criterionId": row["criterionId"], "status": "unknown"} for row in rows]},
           artifact_documents=documents)
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise SchedulerAuditValidationError("repair review probe evidence is invalid") from exc
    for _ref, document in latest.values():
        _validate_review_probe_command(document, documents=documents, requested_checks=requested_checks)
    corrections = _validated_probe_corrections(
        assignment=assignment, documents=documents, role_artifact_ids=set(),
        prior_reviewer_ids={assignment_id}, requested_checks=requested_checks,
    )
    correction_refs = sorted(ref for ref, document in corrections.items()
                             if document["assignmentId"] == assignment_id and document["patchDigest"] == patch_digest)
    return tuple(dict.fromkeys([
        *(ref for probe in probes for ref in (probe["sourceRef"], probe["receiptRef"])), *correction_refs,
    ]))


def _validated_repair_inputs(
    trace: Mapping[str, object],
    documents: Mapping[str, object],
    *,
    request_artifact_id: str,
    requested_checks=(),
) -> dict[str, tuple[str, ...]]:
    """Rejoin each repair's new Assignment to its exact failed revision."""
    repair_documents = {
        artifact_id: document for artifact_id, document in documents.items()
        if isinstance(document, dict)
        and document.get("resultType") == "ImplementationRepairRequest.v1"
    }
    observed: set[str] = set()
    inputs: dict[str, tuple[str, ...]] = {}
    execution = verification = None
    repair_index = 0
    previous_continuation: Mapping[str, object] | None = None
    transitions = trace["transitions"]
    for index, transition in enumerate(transitions):
        stage, outcome = transition["stage"], transition["outcome"]
        if stage == "executor" and outcome == "completed":
            execution = transition
            verification = None
        elif stage == "verifier" and outcome not in {"dispatched", "retrying", "repairing"}:
            verification = transition
        if outcome != "repairing":
            continue
        repair_index += 1
        following = transitions[index + 1]
        assignment_id = str(following["assignmentId"])
        repair_id = f"artifact_repair_{assignment_id}"
        execution_document = documents.get(str(execution["artifactId"])) if execution else None
        verification_document = documents.get(str(verification["artifactId"])) if verification else None
        if not isinstance(execution_document, dict) or not isinstance(verification_document, dict):
            raise SchedulerAuditValidationError("scheduler repair lacks its previous execution evidence")
        checks = verification_document.get("checks")
        change_set = execution_document.get("changeSet")
        if not isinstance(checks, list) or not isinstance(change_set, dict):
            raise SchedulerAuditValidationError("scheduler repair previous evidence is invalid")
        evidence_refs = [check.get("artifactRef") for check in checks if isinstance(check, dict)]
        repair_evidence_refs = list(evidence_refs)
        if stage == "reviewer":
            repair_evidence_refs.extend(_review_probe_repair_refs(
                documents, assignment_id=transition["assignmentId"], patch_digest=change_set.get("patchDigest"),
                requested_checks=requested_checks,
            ))
        expected = {
            "repairVersion": 1,
            "resultType": "ImplementationRepairRequest.v1",
            "workflowId": trace["workflowId"],
            "taskId": trace["taskId"],
            "assignmentId": assignment_id,
            "repairIndex": repair_index,
            "sourceStage": stage,
            "sourceArtifactRef": transition["artifactId"],
            "requestArtifactRef": request_artifact_id,
            "previousExecutionArtifactRef": execution["artifactId"],
            "previousOrderRef": execution_document.get("orderId"),
            "previousDiffArtifactRef": f"artifact_{execution['assignmentId']}_{execution['attemptId']}_frozen_diff",
            "previousPatchDigest": change_set.get("patchDigest"),
            "previousVerificationArtifactRef": verification["artifactId"],
            "evidenceRefs": list(dict.fromkeys(repair_evidence_refs)),
        }
        source = documents.get(str(transition["artifactId"]))
        supported = (
            verification_supports_implementation_repair(
                source, [documents.get(str(ref)) for ref in evidence_refs],
                pending_write_feedback=(
                    _has_pending_executor_feedback(execution_document)
                    and source is verification_document
                    and source.get("resultType") == "VerificationResult.v1"
                    and verification.get("outcome") == "inconclusive"
                    and verification.get("errorCode") == "acceptance_evidence_incomplete"
                ),
            )
            if stage == "verifier"
            else review_supports_implementation_repair(source)
        )
        if not supported:
            raise SchedulerAuditValidationError("scheduler repair is not bound to repairable failed evidence")
        preparation_failed = any(
            item["assignmentId"] == assignment_id
            and item["attemptId"] == following["attemptId"]
            and item["stage"] == "executor"
            and item["outcome"] == "failed"
            and item["errorCode"] == "implementation_repair_unavailable"
            for item in transitions[index + 1:]
        )
        continuation_requested = transition["errorCode"] == CONTINUATION_TRANSITION_ERROR
        if repair_id not in repair_documents and (
            following["outcome"] == "timed_out" or preparation_failed
        ):
            # A failed preparation or pre-dispatch deadline never exercised
            # continuation authority. Preserve its terminal failure audit;
            # every revision that actually proceeds still needs its quote.
            continue
        try:
            repair = parse_implementation_repair_request(repair_documents.get(repair_id))
        except (TypeError, ValueError) as exc:
            raise SchedulerAuditValidationError("scheduler repair request is invalid") from exc
        continuation = repair.get("continuation")
        if continuation_requested != (continuation is not None):
            raise SchedulerAuditValidationError("scheduler continuation authority does not match its transition")
        if continuation is not None:
            if previous_continuation is not None and (
                continuation["sessionId"] != previous_continuation["sessionId"]
                or any(previous_continuation[key] is not None and (
                    continuation[key] is None or continuation[key] > previous_continuation[key]
                ) for key in ("limitModelCalls", "limitTotalTokens", "deadlineMonotonic"))
                or continuation["usedModelCalls"] <= previous_continuation["usedModelCalls"]
                or continuation["usedTotalTokens"] < previous_continuation["usedTotalTokens"]
            ):
                raise SchedulerAuditValidationError("scheduler continuation resets its session or budget")
            previous_continuation = continuation
            expected["continuation"] = continuation
        if repair != expected:
            raise SchedulerAuditValidationError("scheduler repair is not bound to its failed revision")
        # Only the new submission-feedback path permits an unchanged patch to
        # advance after correcting its declaration. Validate the durable part
        # of that exception here; the producer additionally requires a live
        # same-owner session, a newly admitted model call and native acceptance.
        if execution_document.get("handoffReason") == "executor_submission_feedback_pending":
            successor = next((item for item in transitions[index + 1:]
                              if item["stage"] == "executor" and item["outcome"] == "completed"
                              and item["assignmentId"] == assignment_id
                              and item["attemptId"] == following["attemptId"]), None)
            successor_document = documents.get(str(successor["artifactId"])) if successor else None
            if (isinstance(successor_document, Mapping)
                    and isinstance(successor_document.get("changeSet"), Mapping)
                    and successor_document["changeSet"].get("patchDigest") == change_set.get("patchDigest")
                    and not (
                        continuation is not None and stage == "verifier"
                        and source is verification_document
                        and verification_document.get("status") == "passed"
                        and successor_document.get("resultType") == "ExecutionResult.v1"
                        and successor_document.get("status") == "completed"
                    )):
                raise SchedulerAuditValidationError(
                    "unchanged submission-feedback repair lacks a fresh accepted declaration"
                )
        observed.add(repair_id)
        inputs[assignment_id] = implementation_repair_input_refs(repair_id, repair)
    if observed != set(repair_documents):
        raise SchedulerAuditValidationError("scheduler repair request has no authorized transition")
    return inputs


def _validated_probe_corrections(*, assignment, documents, role_artifact_ids,
                                 prior_reviewer_ids, requested_checks):
    """Replay explicit host retirements; old receipts remain mandatory evidence."""
    corrections, retired = {}, set()
    for ref, document in documents.items():
        if (not isinstance(document, Mapping) or document.get("resultType") != "ReviewProbeCorrection.v1"
                or document.get("workflowId") != assignment.workflow_id):
            continue
        if document.get("assignmentId") not in prior_reviewer_ids:
            continue
        owner = assignment
        if document["assignmentId"] != assignment.assignment_id:
            raw_owner = next((entry.get("assignment") for entry in documents.values()
                              if isinstance(entry, Mapping) and entry.get("resultType") == "AssignmentArtifact.v1"
                              and entry.get("stage") == "reviewer" and isinstance(entry.get("assignment"), Mapping)
                              and entry["assignment"].get("assignmentId") == document["assignmentId"]), None)
            try:
                owner = parse_assignment(raw_owner)
            except (ContractValidationError, TypeError, ValueError) as exc:
                raise SchedulerAuditValidationError("probe correction Reviewer owner is invalid") from exc
        try:
            parsed = parse_review_probe_correction(owner, document, artifact_documents=documents,
                                                   required_checks=requested_checks)
        except (ContractValidationError, TypeError, ValueError) as exc:
            raise SchedulerAuditValidationError("scheduler probe correction evidence is invalid") from exc
        if parsed != document or ref != review_probe_correction_artifact_id(parsed):
            raise SchedulerAuditValidationError("scheduler probe correction identity is invalid")
        refs = {ref, parsed["oldSourceRef"], parsed["newSourceRef"], parsed["newReceiptRef"], *parsed["oldReceiptRefs"]}
        if refs & role_artifact_ids:
            raise SchedulerAuditValidationError("probe correction cannot use a worker result artifact")
        if parsed["oldProbeId"] in retired:
            raise SchedulerAuditValidationError("review probe cannot be retired more than once")
        retired.add(parsed["oldProbeId"])
        corrections[ref] = parsed
    # Cycles cannot erase all obligations, even with individually valid receipts.
    edges = {doc["oldProbeId"]: doc["newProbeId"] for doc in corrections.values()}
    for start in edges:
        seen, current = set(), start
        while current in edges:
            if current in seen:
                raise SchedulerAuditValidationError("review probe correction contains a cycle")
            seen.add(current)
            current = edges[current]
    return corrections


def validate_review_coverage(
    *, review, assignment, documents, role_artifact_ids, prior_reviewer_ids, requested_checks,
    patch_digest: str | None = None,
) -> None:
    """Validate coverage before handoff/commit and again during durable replay.

    Inputs are the scheduler-owned Assignment, artifact snapshot and approved
    checks. A model's correctly named reference is not publication authority.
    The optional patch binds the live gate to the actual frozen candidate.
    """
    try:
        review = parse_role_result(assignment, _role_payload(review))
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise SchedulerAuditValidationError("scheduler review coverage contract is invalid") from exc
    if patch_digest is not None and review["patchDigest"] != patch_digest:
        raise SchedulerAuditValidationError("scheduler review coverage patch is not current")
    ref = review.get("coverageArtifactRef")
    if ref is None:
        # Marked approvals were rejected by parse_role_result already. Negative
        # feedback and historical unmarked verdicts remain readable.
        return
    document = documents.get(ref)
    if ref in role_artifact_ids:
        raise SchedulerAuditValidationError("review coverage cannot be a worker result artifact")
    try:
        parsed = parse_review_coverage_evidence(
            assignment, document, review=review, artifact_documents=documents,
        )
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise SchedulerAuditValidationError("scheduler review coverage is invalid") from exc
    if parsed != document:
        raise SchedulerAuditValidationError("scheduler review coverage is not canonical")
    selected = {probe["probeId"]: probe for probe in parsed["probes"]}
    corrections = _validated_probe_corrections(
        assignment=assignment, documents=documents, role_artifact_ids=role_artifact_ids,
        prior_reviewer_ids=prior_reviewer_ids, requested_checks=requested_checks,
    )
    retired = {document["oldProbeId"] for document in corrections.values()}
    if retired.intersection(selected):
        raise SchedulerAuditValidationError("review coverage selected a retired probe")
    active_probes = set()
    latest_current = {}
    for candidate in documents.values():
        if not isinstance(candidate, Mapping) or candidate.get("resultType") != "ReviewProbeExecution.v1":
            continue
        if (candidate.get("workflowId") != assignment.workflow_id
                or candidate.get("assignmentId") not in prior_reviewer_ids):
            continue
        probe_id, sequence = candidate.get("probeId"), candidate.get("sequence")
        if not isinstance(probe_id, str) or type(sequence) is not int or sequence < 1:
            raise SchedulerAuditValidationError("scheduler review probe history is invalid")
        if candidate.get("status") in {"passed", "failed"}:
            active_probes.add(probe_id)
        if candidate.get("assignmentId") == assignment.assignment_id:
            latest_current[probe_id] = max(sequence, latest_current.get(probe_id, 0))
    active_probes.difference_update(retired)
    if not active_probes.issubset(selected):
        raise SchedulerAuditValidationError("scheduler review coverage omitted an active probe")
    for probe in parsed["probes"]:
        if probe["sourceRef"] in role_artifact_ids or probe["receiptRef"] in role_artifact_ids:
            raise SchedulerAuditValidationError("review probe evidence cannot be a worker result artifact")
        receipt = documents[probe["receiptRef"]]
        _validate_review_probe_command(receipt, documents=documents, requested_checks=requested_checks)
        if receipt["sequence"] < latest_current.get(probe["probeId"], 0):
            raise SchedulerAuditValidationError("review coverage discarded a newer probe result")


def project_scheduler_audit(
    *,
    profile: TaskProfile,
    plan: DispatchPlan,
    trace: Mapping[str, object],
    artifacts: tuple[SchedulerArtifact, ...],
    contract_mode: str = "legacy",
) -> SchedulerAudit:
    """Project effective scheduler-owned state into one sealed audit value."""

    if not isinstance(profile, TaskProfile):
        raise SchedulerAuditValidationError("effective profile is invalid")
    if contract_mode not in {"legacy", "v2"}:
        raise SchedulerAuditValidationError("scheduler contract mode is invalid")
    if not isinstance(plan, DispatchPlan):
        raise SchedulerAuditValidationError("dispatch plan is invalid")
    if (
        not isinstance(plan.task_id, str)
        or not isinstance(plan.stages, tuple)
        or any(not isinstance(stage, DispatchStage) for stage in plan.stages)
        or not isinstance(plan.rule_id, str)
        or type(plan.policy_version) is not int
        or plan.policy_version not in SUPPORTED_SCHEDULER_POLICY_VERSIONS
        or type(plan.requires_human) is not bool
    ):
        raise SchedulerAuditValidationError("dispatch plan is invalid")
    if CentralScheduler().plan(profile, policy_version=plan.policy_version) != plan:
        raise SchedulerAuditValidationError(
            "dispatch plan does not match the effective profile"
        )
    normalized_trace = _normalize_trace(trace)
    if contract_mode != "v2" and any(
        transition["outcome"] == "repairing" for transition in normalized_trace["transitions"]
    ):
        raise SchedulerAuditValidationError("implementation repair requires structured audit authority")
    if (
        normalized_trace.get("taskId") != profile.task_id
        or normalized_trace.get("ruleId") != plan.rule_id
        or normalized_trace.get("policyVersion") != plan.policy_version
    ):
        raise SchedulerAuditValidationError(
            "scheduler trace does not match effective routing"
        )
    workflow_id = normalized_trace.get("workflowId")
    if (
        not isinstance(workflow_id, str)
        or _WORKFLOW_ID_RE.fullmatch(workflow_id) is None
    ):
        raise SchedulerAuditValidationError("scheduler workflow id is invalid")
    if not isinstance(artifacts, tuple):
        raise SchedulerAuditValidationError("scheduler artifacts must be a tuple")
    if len(artifacts) > MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW:
        raise SchedulerAuditValidationError("scheduler audit has too many artifacts")
    artifact_ids: set[str] = set()
    assignment_artifacts: dict[tuple[str, str], tuple[str, str]] = {}
    assignments_by_attempt: dict[tuple[str, str], Assignment] = {}
    receipt_artifacts: dict[str, dict[str, object]] = {}
    approval_requests: dict[str, dict[str, object]] = {}
    approvals: dict[str, dict[str, object]] = {}
    acceptance_matrices: dict[str, dict[str, object]] = {}
    workflow_requests: dict[str, object] = {}
    workflow_objective_digests: dict[str, str] = {}
    artifact_documents: dict[str, object] = {}
    candidate_artifact_ids: set[str] = set()
    total_bytes = 0
    for artifact in artifacts:
        encoded = _canonical_artifact_content(artifact)
        if artifact.artifact_id in artifact_ids:
            raise SchedulerAuditValidationError(
                "scheduler audit artifact id is duplicated"
            )
        artifact_ids.add(artifact.artifact_id)
        total_bytes += len(encoded)
        document = _closed_json_document(artifact.content)
        artifact_documents[artifact.artifact_id] = document
        if isinstance(document, dict) and document.get("resultType") == "ExecutionCandidate.v1":
            # A host-observed candidate is only an input to independent final
            # verification/review. Historical routes without that review may
            # never gain promotion authority from this newer artifact type.
            if (contract_mode != "v2" or plan.policy_version < 4
                    or DispatchStage.REVIEWER not in plan.stages):
                raise SchedulerAuditValidationError(
                    "scheduler checked candidate requires mandatory Reviewer authority"
                )
            candidate_artifact_ids.add(artifact.artifact_id)
        if isinstance(document, dict) and document.get("resultType") == (
            "AssignmentArtifact.v1"
        ):
            expected_fields = {
                "assignmentArtifactVersion",
                "resultType",
                "attemptId",
                "agentRunId",
                "stage",
                "ruleId",
                "assignment",
            }
            assignment = document.get("assignment")
            if (
                set(document) != expected_fields
                or document.get("assignmentArtifactVersion") != 1
                or not isinstance(assignment, dict)
                or assignment.get("workflowId") != workflow_id
                or assignment.get("taskId") != profile.task_id
                or not isinstance(assignment.get("assignmentId"), str)
                or not isinstance(document.get("attemptId"), str)
                or not isinstance(document.get("agentRunId"), str)
            ):
                raise SchedulerAuditValidationError(
                    "scheduler Assignment artifact is invalid"
                )
            try:
                parsed_assignment = parse_assignment(assignment)
            except (ContractValidationError, TypeError, ValueError) as exc:
                raise SchedulerAuditValidationError(
                    "scheduler Assignment artifact is invalid"
                ) from exc
            if (
                parsed_assignment.role.value != document.get("stage")
                or document.get("ruleId") != plan.rule_id
            ):
                raise SchedulerAuditValidationError(
                    "scheduler Assignment artifact authority is inconsistent"
                )
            assignment_key = (
                parsed_assignment.assignment_id,
                str(document["attemptId"]),
            )
            if assignment_key in assignment_artifacts:
                raise SchedulerAuditValidationError(
                    "scheduler Assignment artifact is duplicated"
                )
            assignment_artifacts[assignment_key] = (
                str(document["agentRunId"]),
                str(document["stage"]),
            )
            assignments_by_attempt[assignment_key] = parsed_assignment
        if isinstance(document, dict) and document.get("resultType") == (
            "WorkflowRequestArtifact.v1"
        ):
            if contract_mode != "v2":
                raise SchedulerAuditValidationError(
                    "scheduler WorkflowRequest artifact requires v2 mode"
                )
            fields = {
                "requestArtifactVersion",
                "resultType",
                "workflowId",
                "taskId",
                "requestDigest",
                "objectiveDigest",
                "request",
            }
            request_document = document.get("request")
            objective_digest = document.get("objectiveDigest")
            try:
                parsed_request = parse_workflow_request(
                    request_document,
                    legacy_task_id=profile.task_id,
                )
                request_payload = json.dumps(
                    {
                        "objectiveDigest": objective_digest,
                        "request": parsed_request.to_dict(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                request_digest = (
                    "sha256:" + hashlib.sha256(request_payload).hexdigest()
                )
            except (ContractValidationError, TypeError, ValueError) as exc:
                raise SchedulerAuditValidationError(
                    "scheduler WorkflowRequest artifact is invalid"
                ) from exc
            if (
                set(document) != fields
                or document.get("requestArtifactVersion") != 1
                or document.get("workflowId") != workflow_id
                or document.get("taskId") != profile.task_id
                or _DIGEST_RE.fullmatch(str(objective_digest)) is None
                or parsed_request.structured is not True
                or parsed_request.to_dict() != request_document
                or document.get("requestDigest") != request_digest
            ):
                raise SchedulerAuditValidationError(
                    "scheduler WorkflowRequest artifact is invalid"
                )
            workflow_requests[artifact.artifact_id] = parsed_request
            workflow_objective_digests[artifact.artifact_id] = str(
                objective_digest
            )
        if isinstance(document, dict) and document.get("resultType") == (
            "SchedulerStageReceipt.v1"
        ):
            receipt_fields = {
                "receiptVersion",
                "resultType",
                "workflowId",
                "taskId",
                "assignmentId",
                "attemptId",
                "agentRunId",
                "decisionId",
                "parentDecisionId",
                "ruleId",
                "stage",
                "outcome",
                "errorCode",
            }
            if (
                set(document) != receipt_fields
                or document.get("receiptVersion") != 1
            ):
                raise SchedulerAuditValidationError(
                    "scheduler stage receipt is invalid"
                )
            receipt_artifacts[artifact.artifact_id] = document
        if isinstance(document, dict) and document.get("resultType") == (
            "HumanApprovalRequest.v1"
        ):
            fields = {
                "approvalVersion",
                "resultType",
                "approvalRequestId",
                "taskId",
                "requestDigest",
                "baseDigest",
                "analystArtifactId",
                "analystDigest",
                "risk",
            }
            approval_id = document.get("approvalRequestId")
            approval_identity = (
                f"{document.get('taskId')}\0{document.get('requestDigest')}\0"
                f"{document.get('baseDigest')}\0{document.get('analystDigest')}"
            ).encode("utf-8")
            expected_approval_id = (
                "approval_" + hashlib.sha256(approval_identity).hexdigest()[:32]
            )
            if (
                set(document) != fields
                or document.get("approvalVersion") != 1
                or not isinstance(approval_id, str)
                or approval_id != expected_approval_id
                or artifact.artifact_id
                != f"artifact_{approval_id}_request"
                or document.get("taskId") != profile.task_id
                or _DIGEST_RE.fullmatch(str(document.get("requestDigest")))
                is None
                or _DIGEST_RE.fullmatch(str(document.get("baseDigest"))) is None
                or not isinstance(document.get("analystArtifactId"), str)
                or _DIGEST_RE.fullmatch(str(document.get("analystDigest")))
                is None
                or document.get("risk") != profile.risk.value
            ):
                raise SchedulerAuditValidationError(
                    "scheduler human approval request is invalid"
                )
            approval_requests[approval_id] = document
        if isinstance(document, dict) and document.get("resultType") == (
            "HumanApproval.v1"
        ):
            fields = {
                "approvalVersion",
                "resultType",
                "approvalId",
                "taskId",
                "requestDigest",
                "baseDigest",
                "analystDigest",
                "decision",
                "approvedBy",
                "validatedAt",
                "expiresAt",
            }
            approval_id = document.get("approvalId")
            approved_by = document.get("approvedBy")
            validated_at = document.get("validatedAt")
            expires_at = document.get("expiresAt")
            try:
                parsed_validated_at = datetime.fromisoformat(
                    str(validated_at).replace("Z", "+00:00")
                )
                parsed_expires_at = datetime.fromisoformat(
                    str(expires_at).replace("Z", "+00:00")
                )
            except ValueError:
                parsed_validated_at = None
                parsed_expires_at = None
            if (
                set(document) != fields
                or document.get("approvalVersion") != 1
                or not isinstance(approval_id, str)
                or artifact.artifact_id != f"artifact_{approval_id}"
                or document.get("taskId") != profile.task_id
                or document.get("decision") != "approved"
                or _DIGEST_RE.fullmatch(str(document.get("requestDigest")))
                is None
                or _DIGEST_RE.fullmatch(str(document.get("baseDigest"))) is None
                or _DIGEST_RE.fullmatch(str(document.get("analystDigest")))
                is None
                or not isinstance(approved_by, str)
                or re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_.@-]{1,127}",
                    approved_by,
                )
                is None
                or not isinstance(validated_at, str)
                or not isinstance(expires_at, str)
                or len(validated_at) > 80
                or len(expires_at) > 80
                or parsed_validated_at is None
                or parsed_expires_at is None
                or parsed_validated_at.tzinfo is None
                or parsed_expires_at.tzinfo is None
                or parsed_validated_at >= parsed_expires_at
            ):
                raise SchedulerAuditValidationError(
                    "scheduler human approval is invalid"
                )
            approvals[approval_id] = document
        if isinstance(document, dict) and document.get("resultType") == (
            "AcceptanceEvidenceMatrix.v1"
        ):
            fields = {
                "matrixVersion",
                "resultType",
                "workflowId",
                "taskId",
                "patchDigest",
                "verificationArtifactRef",
                "frozenDiffRef",
                "criteria",
            }
            criteria = document.get("criteria")
            if (
                set(document) != fields
                or document.get("matrixVersion") != 1
                or document.get("workflowId") != workflow_id
                or document.get("taskId") != profile.task_id
                or _DIGEST_RE.fullmatch(str(document.get("patchDigest")))
                is None
                or not isinstance(document.get("verificationArtifactRef"), str)
                or not isinstance(document.get("frozenDiffRef"), str)
                or not isinstance(criteria, list)
                or not criteria
                or len(criteria) > 100
            ):
                raise SchedulerAuditValidationError(
                    "scheduler acceptance evidence matrix is invalid"
                )
            acceptance_matrices[artifact.artifact_id] = document
    if total_bytes > MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES:
        raise SchedulerAuditValidationError(
            "scheduler audit artifact bytes exceed the limit"
        )
    dangling_references = [
        str(transition["artifactId"])
        for transition in normalized_trace["transitions"]
        if transition["artifactId"] is not None
        and transition["artifactId"] not in artifact_ids
    ]
    if dangling_references:
        raise SchedulerAuditValidationError(
            "scheduler trace references an unsealed artifact"
        )
    human_preflight_blocked_attempts = {
        (str(item["assignmentId"]), str(item["attemptId"]))
        for item in normalized_trace["transitions"]
        if item["outcome"] == "waiting_human"
        and item["stage"] in {"executor", "reviewer"}
        and item["errorCode"] == "human_approval_expired"
        and isinstance(artifact_documents.get(str(item["artifactId"])), dict)
        and artifact_documents[str(item["artifactId"])].get("resultType")
        == "HumanApproval.v1"
    }
    dispatched_workers = {
        (str(item["assignmentId"]), str(item["attemptId"])): (
            str(item["agentRunId"]),
            str(item["stage"]),
        )
        for item in normalized_trace["transitions"]
        if item["outcome"] == "dispatched"
        and item["stage"]
        in {"analyst", "executor", "reviewer", "adjudicator"}
        and (str(item["assignmentId"]), str(item["attemptId"]))
        not in human_preflight_blocked_attempts
        and not (
            (str(item["assignmentId"]), str(item["attemptId"])) not in assignment_artifacts
            and any(
                terminal["stage"] == "executor"
                and terminal["outcome"] == "failed"
                and terminal["errorCode"] == "implementation_repair_unavailable"
                and terminal["artifactId"] in receipt_artifacts
                and terminal["assignmentId"] == item["assignmentId"]
                and terminal["attemptId"] == item["attemptId"]
                for terminal in normalized_trace["transitions"]
            )
        )
    }
    if (
        (contract_mode == "v2" or assignment_artifacts)
        and assignment_artifacts != dispatched_workers
    ):
        raise SchedulerAuditValidationError(
            "scheduler Assignment artifacts do not match dispatched Workers"
        )
    if contract_mode == "v2" and len(workflow_requests) != 1:
        raise SchedulerAuditValidationError(
            "scheduler WorkflowRequest artifact is missing"
        )
    if contract_mode == "v2":
        request_artifact_id, request = next(iter(workflow_requests.items()))
        repair_inputs = _validated_repair_inputs(
            normalized_trace, artifact_documents, request_artifact_id=request_artifact_id,
            requested_checks=request.verification_checks,
        )
        objective_digest = workflow_objective_digests[request_artifact_id]
        expected_profile = request.task_profile_for_policy(plan.policy_version)
        if plan.policy_version >= 5:
            from minicode.workflow_review_policy import (
                review_routing_artifact_id, validate_review_routing_decision,
            )

            request_document = artifact_documents[request_artifact_id]
            routing_id = review_routing_artifact_id(request_document["requestDigest"])
            routing = artifact_documents.get(routing_id)
            routing_documents = [document for document in artifact_documents.values()
                                 if isinstance(document, dict)
                                 and document.get("resultType") == "ReviewRoutingDecision.v1"]
            if len(routing_documents) != 1 or not isinstance(routing, dict):
                raise SchedulerAuditValidationError("scheduler review routing authority is missing")
            try:
                required = validate_review_routing_decision(
                    routing, request=request, profile=expected_profile,
                    workflow_id=workflow_id, request_digest=request_document["requestDigest"],
                    objective_digest=objective_digest, base_digest=routing.get("baseDigest"),
                )
            except (TypeError, ValueError) as exc:
                raise SchedulerAuditValidationError("scheduler review routing authority is invalid") from exc
            expected_profile = replace(expected_profile, independent_review_required=required)
            if profile.independent_review_required != required:
                raise SchedulerAuditValidationError("scheduler review routing does not match effective profile")
        stable_profile_fields = (
            "task_id",
            "kind",
            "risk",
            "complex",
            "parent_has_sufficient_context",
            "scope_clear",
            "acceptance_executable",
            "human_confirmation_required",
        )
        if any(
            getattr(profile, field) != getattr(expected_profile, field)
            for field in stable_profile_fields
        ):
            raise SchedulerAuditValidationError(
                "scheduler WorkflowRequest does not match effective routing"
            )
        expected_modes = {
            "analyst": (
                "investigate" if request.kind == "read_only" else "prepare_change"
            ),
            "executor": "implement",
            "reviewer": "change_review",
        }
        expected_result_types = {
            "analyst": "AnalystReport.v1",
            "executor": "ExecutionResult.v1",
            "reviewer": "ReviewVerdict.v1",
        }
        expected_constraint = (
            {
                "id": "c_scheduler_scope",
                "kind": "scope",
                "text": "Do not expand the scheduler-owned objective or paths.",
            },
        )
        for assignment in assignments_by_attempt.values():
            role = assignment.role.value
            if role not in expected_modes:
                continue
            assignment_constraints = expected_constraint
            if role == "reviewer" and any(
                item.constraint_id == REVIEW_COVERAGE_CONSTRAINT_ID for item in assignment.constraints
            ):
                assignment_constraints = (*expected_constraint, {
                    "id": REVIEW_COVERAGE_CONSTRAINT_ID, "kind": "policy",
                    "text": REVIEW_COVERAGE_CONSTRAINT_TEXT,
                })
            write_paths_are_valid = (
                assignment.scope.write_paths == request.scope.write_paths
                if role == "analyst"
                else all(
                    _path_is_within_requested_scope(
                        path,
                        request.scope.write_paths,
                    )
                    for path in assignment.scope.write_paths
                )
            )
            input_refs_are_valid = (
                assignment.input_refs == (request_artifact_id,)
                if role == "analyst"
                else request_artifact_id in assignment.input_refs
            )
            if (
                assignment.mode != expected_modes[role]
                or assignment.expected_result_type
                != expected_result_types[role]
                or assignment.risk.value != expected_profile.risk.value
                or "sha256:"
                + hashlib.sha256(assignment.objective.encode("utf-8")).hexdigest()
                != objective_digest
                or assignment.acceptance_criteria != request.criteria
                or assignment.scope.read_roots != request.scope.read_roots
                or assignment.scope.forbidden_paths
                != request.scope.forbidden_paths
                or not write_paths_are_valid
                or tuple(
                    constraint.to_dict()
                    for constraint in assignment.constraints
                )
                != assignment_constraints
                or not input_refs_are_valid
            ):
                raise SchedulerAuditValidationError(
                    "scheduler Assignment is not bound to WorkflowRequest"
                )
            if role == "executor":
                order_documents = [
                    (ref, artifact_documents[ref])
                    for ref in assignment.input_refs
                    if isinstance(artifact_documents.get(ref), dict)
                    and artifact_documents[ref].get("orderVersion") == 1
                ]
                if len(order_documents) != 1:
                    raise SchedulerAuditValidationError(
                        "scheduler Executor Assignment lacks one exact order"
                    )
                try:
                    order = parse_implementation_order(
                        assignment,
                        order_documents[0][1],
                    )
                except (ContractValidationError, TypeError, ValueError) as exc:
                    raise SchedulerAuditValidationError(
                        "scheduler Executor order is invalid"
                    ) from exc
                if (
                    order.order_id != order_documents[0][0]
                    or not _order_scope_within_assignment(order, assignment)
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler Executor order scope is inconsistent"
                    )
                repair_document = artifact_documents.get(
                    f"artifact_repair_{assignment.assignment_id}"
                )
                if assignment.assignment_id in repair_inputs:
                    previous_order = artifact_documents.get(
                        str(repair_document["previousOrderRef"])
                    )
                    if not isinstance(previous_order, dict) or any(
                        order_documents[0][1].get(field) != previous_order.get(field)
                        for field in (
                            "baseDigest", "allowedPaths", "steps",
                            "criterionIds", "verificationRequests",
                        )
                    ):
                        raise SchedulerAuditValidationError(
                            "scheduler repair changed its locked implementation order"
                        )
                analyst_handoffs = [
                    (artifact_id, document)
                    for artifact_id, document in artifact_documents.items()
                    if isinstance(document, dict)
                    and document.get("resultType") == "AnalystHandoff.v1"
                ]
                if len(analyst_handoffs) > 1:
                    raise SchedulerAuditValidationError(
                        "scheduler Executor lacks one Analyst handoff"
                    )
                if analyst_handoffs:
                    handoff_id, handoff = analyst_handoffs[0]
                    handoff_evidence_refs = handoff.get("evidenceSnapshotRefs")
                    if not isinstance(handoff_evidence_refs, list):
                        raise SchedulerAuditValidationError(
                            "scheduler Executor Analyst handoff is invalid"
                        )
                    expected_executor_refs = (
                        handoff_id,
                        request_artifact_id,
                        *(str(ref) for ref in handoff_evidence_refs),
                        *(
                            f"artifact_{approval_id}"
                            for approval_id in approvals
                        ),
                        order.order_id,
                    )
                else:
                    # Scoped route: no Analyst ran, so the executor binds
                    # directly to the frozen WorkflowRequest and its order.
                    expected_executor_refs = (
                        request_artifact_id,
                        *(
                            f"artifact_{approval_id}"
                            for approval_id in approvals
                        ),
                        order.order_id,
                    )
                expected_executor_refs = (
                    *expected_executor_refs[:-1],
                    *repair_inputs.get(assignment.assignment_id, ()),
                    expected_executor_refs[-1],
                )
                if assignment.input_refs != expected_executor_refs:
                    raise SchedulerAuditValidationError(
                        "scheduler Executor input topology is inconsistent"
                    )
    if contract_mode == "v2" and any(
        ref not in artifact_ids
        and ref
        not in {
            str(document.get("conflictId"))
            for document in artifact_documents.values()
            if isinstance(document, dict)
            and document.get("resultType") == "ConflictPackage.v1"
        }
        for assignment in assignments_by_attempt.values()
        for ref in assignment.input_refs
    ):
        raise SchedulerAuditValidationError(
            "scheduler Assignment has an unavailable input artifact"
        )

    completed_execution_document: dict[str, object] | None = None
    observed_candidate_ids: set[str] = set()
    execution_records: list[tuple[Mapping[str, object], Assignment, dict[str, object]]] = []
    completed_review_results: list[dict[str, object]] = []
    completed_review_assignments: list[Assignment] = []
    completed_review_records: list[
        tuple[Mapping[str, object], dict[str, object], Assignment]
    ] = []
    terminal_review_results: list[
        tuple[Mapping[str, object], dict[str, object], Assignment]
    ] = []
    if contract_mode == "v2":
        for transition in normalized_trace["transitions"]:
            stage = str(transition["stage"])
            if (
                transition["outcome"] in {"dispatched", "retrying", "repairing", "completed"}
                or stage not in {"analyst", "executor", "reviewer"}
                or transition["artifactId"] is None
                or transition["artifactId"] in receipt_artifacts
            ):
                continue
            document = artifact_documents.get(str(transition["artifactId"]))
            result_type = (
                document.get("resultType")
                if isinstance(document, dict)
                else None
            )
            if (
                transition["outcome"] == "waiting_human"
                and result_type
                in {"HumanApproval.v1", "HumanApprovalRequest.v1"}
            ):
                if (
                    stage not in {"executor", "reviewer"}
                    or result_type != "HumanApproval.v1"
                    or transition["errorCode"] != "human_approval_expired"
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler expired human authority gate is inconsistent"
                    )
                continue
            assignment = assignments_by_attempt.get(
                (
                    str(transition["assignmentId"]),
                    str(transition["attemptId"]),
                )
            )
            if assignment is None or assignment.role.value != stage:
                raise SchedulerAuditValidationError(
                    "scheduler terminal Worker lacks its exact Assignment"
                )
            if stage == "analyst":
                parsed_analyst = _validate_analyst_completion(
                    assignment,
                    attempt_id=str(transition["attemptId"]),
                    document=document,
                    artifact_documents=artifact_documents,
                    require_completed=False,
                )
                if (
                    transition["outcome"],
                    transition["errorCode"],
                ) != _expected_analyst_transition(
                    assignment,
                    parsed_analyst,
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler Analyst terminal cause is inconsistent"
                    )
                continue
            if stage == "executor":
                if not isinstance(document, dict):
                    raise SchedulerAuditValidationError(
                        "scheduler terminal ExecutionResult is invalid"
                    )
                order_id = document.get("orderId")
                order_document = artifact_documents.get(str(order_id))
                try:
                    order = parse_implementation_order(
                        assignment,
                        order_document,
                    )
                    parsed_execution = parse_role_result(
                        assignment,
                        _role_payload(document),
                        implementation_order=order,
                    )
                except (ContractValidationError, TypeError, ValueError) as exc:
                    raise SchedulerAuditValidationError(
                        "scheduler terminal ExecutionResult is invalid"
                    ) from exc
                if (
                    order_id not in assignment.input_refs
                    or not _order_scope_within_assignment(order, assignment)
                    or parsed_execution != document
                    or parsed_execution.get("status")
                    != transition["outcome"]
                    or transition["errorCode"]
                    != f"executor_{parsed_execution.get('status')}"
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler terminal ExecutionResult is not canonical"
                    )
                continue
            try:
                parsed_review = parse_role_result(
                    assignment,
                    _role_payload(document),
                )
            except (ContractValidationError, TypeError, ValueError) as exc:
                raise SchedulerAuditValidationError(
                    "scheduler terminal ReviewVerdict is invalid"
                ) from exc
            expected_review = (
                parsed_review
                if isinstance(document, dict) and "resultType" in document
                else _role_payload(parsed_review)
            )
            if expected_review != document:
                raise SchedulerAuditValidationError(
                    "scheduler terminal ReviewVerdict is not canonical"
                )
            terminal_review_results.append(
                (transition, parsed_review, assignment)
            )

        adjudicated_continuations = _adjudicated_continuation_attempts(
            normalized_trace, assignments_by_attempt, artifact_documents
        )
        for transition in normalized_trace["transitions"]:
            stage = str(transition["stage"])
            if (
                transition["outcome"] != "completed"
                or stage not in {"analyst", "executor", "reviewer"}
            ):
                continue
            assignment_key = (
                str(transition["assignmentId"]),
                str(transition["attemptId"]),
            )
            assignment = assignments_by_attempt.get(assignment_key)
            document = artifact_documents.get(
                str(transition["artifactId"])
            )
            if assignment is None or assignment.role.value != stage:
                raise SchedulerAuditValidationError(
                    "scheduler completed Worker lacks its exact Assignment"
                )
            if stage == "analyst":
                parsed_analyst = _validate_analyst_completion(
                    assignment,
                    attempt_id=str(transition["attemptId"]),
                    document=document,
                    artifact_documents=artifact_documents,
                )
                expected_analyst = _expected_analyst_transition(
                    assignment,
                    parsed_analyst,
                )
                if expected_analyst != ("completed", None) and not (
                    expected_analyst
                    == ("blocked", "analyst_risk_requires_reclassification")
                    and assignment_key in adjudicated_continuations
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler completed Analyst did not pass its gates"
                    )
                continue
            if stage == "executor":
                if not isinstance(document, dict):
                    raise SchedulerAuditValidationError(
                        "scheduler ExecutionResult is missing"
                    )
                order_id = document.get("orderId")
                order_document = artifact_documents.get(str(order_id))
                if (
                    not isinstance(order_id, str)
                    or order_id not in assignment.input_refs
                    or not isinstance(order_document, dict)
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler ImplementationOrder is missing"
                    )
                try:
                    order = parse_implementation_order(
                        assignment,
                        order_document,
                    )
                    is_candidate = document.get("resultType") == "ExecutionCandidate.v1"
                    if is_candidate:
                        from minicode.multi_agent_contracts import parse_execution_candidate

                        expected_id = (
                            f"artifact_{transition['assignmentId']}_"
                            f"{transition['attemptId']}_execution"
                        )
                        if (transition["artifactId"] != expected_id
                                or transition["artifactId"] not in candidate_artifact_ids):
                            raise ContractValidationError("checked candidate artifact authority is invalid")
                        parsed_execution = parse_execution_candidate(
                            assignment,
                            document,
                            implementation_order=order,
                            required_checks=next(iter(workflow_requests.values())).verification_checks,
                            receipt_documents=artifact_documents,
                        )
                        change = parsed_execution["changeSet"]
                        _validate_frozen_diff(
                            artifact_documents.get(
                                f"artifact_{transition['assignmentId']}_"
                                f"{transition['attemptId']}_frozen_diff"
                            ),
                            base_digest=str(change["baseDigest"]),
                            patch_digest=str(change["patchDigest"]),
                            require_complete=False,
                        )
                        observed_candidate_ids.add(str(transition["artifactId"]))
                    else:
                        parsed_execution = parse_role_result(
                            assignment,
                            _role_payload(document),
                            implementation_order=order,
                        )
                except (ContractValidationError, TypeError, ValueError) as exc:
                    raise SchedulerAuditValidationError(
                        "scheduler ExecutionResult is invalid"
                    ) from exc
                if (
                    order.order_id != order_id
                    or not _order_scope_within_assignment(order, assignment)
                    or parsed_execution != document
                    or parsed_execution.get("status") != (
                        "awaiting_verification" if is_candidate else "completed"
                    )
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler ExecutionResult is not canonical"
                    )
                completed_execution_document = parsed_execution
                execution_records.append((transition, assignment, parsed_execution))
                continue
            try:
                parsed_review = parse_role_result(
                    assignment,
                    _role_payload(document),
                )
            except (ContractValidationError, TypeError, ValueError) as exc:
                raise SchedulerAuditValidationError(
                    "scheduler ReviewVerdict is invalid"
                ) from exc
            expected_review = (
                parsed_review
                if isinstance(document, dict) and "resultType" in document
                else _role_payload(parsed_review)
            )
            if expected_review != document:
                raise SchedulerAuditValidationError(
                    "scheduler ReviewVerdict is not canonical"
                )
            completed_review_results.append(parsed_review)
            completed_review_assignments.append(assignment)
            completed_review_records.append(
                (transition, parsed_review, assignment)
            )

        role_artifact_ids = {
            item["artifactId"] for item in normalized_trace["transitions"]
            if item["stage"] in {"analyst", "executor", "reviewer", "adjudicator"}
            and item["artifactId"] is not None
        }
        for transition, review, assignment in (*completed_review_records, *terminal_review_results):
            prior_reviewer_ids = set()
            for entry in normalized_trace["transitions"]:
                if entry["stage"] == "reviewer":
                    prior_reviewer_ids.add(entry["assignmentId"])
                if entry is transition:
                    break
            validate_review_coverage(
                review=review, assignment=assignment, documents=artifact_documents,
                role_artifact_ids=role_artifact_ids, prior_reviewer_ids=prior_reviewer_ids,
                requested_checks=next(iter(workflow_requests.values())).verification_checks,
            )

        for _execution_transition, _execution_assignment, execution_document in execution_records:
            execution_base_digest = execution_document["changeSet"]["baseDigest"]
            if execution_base_digest != execution_records[0][2]["changeSet"]["baseDigest"]:
                raise SchedulerAuditValidationError("scheduler repair execution changed its original baseline")
            for transition in normalized_trace["transitions"]:
                if not (
                    transition["stage"] == "analyst"
                    and transition["outcome"] == "completed"
                ):
                    continue
                handoff = artifact_documents[str(transition["artifactId"])]
                if any(
                    artifact_documents[str(ref)].get("baseDigest")
                    != execution_base_digest
                    for ref in handoff["evidenceSnapshotRefs"]
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler Analyst evidence baseline is inconsistent"
                    )

    if observed_candidate_ids != candidate_artifact_ids:
        raise SchedulerAuditValidationError(
            "scheduler checked candidate is not bound to its Executor transition"
        )
    if (normalized_trace.get("committed") is True
            and _has_pending_executor_feedback(completed_execution_document)):
        raise SchedulerAuditValidationError(
            "pending Executor "
            + ("submission" if completed_execution_document.get("handoffReason")
               == "executor_submission_feedback_pending" else "write")
            + " feedback cannot authorize commit"
        )

    referenced_receipts: set[str] = set()
    for transition in normalized_trace["transitions"]:
        artifact_id = transition["artifactId"]
        if artifact_id not in receipt_artifacts:
            continue
        expected_receipt = {
            "receiptVersion": 1,
            "resultType": "SchedulerStageReceipt.v1",
            "workflowId": transition["workflowId"],
            "taskId": transition["taskId"],
            "assignmentId": transition["assignmentId"],
            "attemptId": transition["attemptId"],
            "agentRunId": transition["agentRunId"],
            "decisionId": transition["decisionId"],
            "parentDecisionId": transition["parentDecisionId"],
            "ruleId": plan.rule_id,
            "stage": transition["stage"],
            "outcome": transition["outcome"],
            "errorCode": transition["errorCode"],
        }
        if receipt_artifacts[artifact_id] != expected_receipt:
            raise SchedulerAuditValidationError(
                "scheduler stage receipt does not match its transition"
            )
        if artifact_id in referenced_receipts:
            raise SchedulerAuditValidationError(
                "scheduler stage receipt is referenced more than once"
            )
        referenced_receipts.add(str(artifact_id))
    if referenced_receipts != set(receipt_artifacts):
        raise SchedulerAuditValidationError(
            "scheduler stage receipt is not referenced by its transition"
        )
    if contract_mode == "v2":
        _validate_v2_transition_causes(
            normalized_trace,
            receipt_artifact_ids=frozenset(receipt_artifacts),
        )
    def execution_before(transition):
        return next((
            record for record in reversed(execution_records)
            if record[0]["sequence"] < transition["sequence"]
        ), None)

    validated_verification_documents: dict[str, dict[str, object]] = {}
    if contract_mode == "v2":
        request = next(iter(workflow_requests.values()))
        request_checks_by_id = {
            check.check_id: check for check in request.verification_checks
        }
        for transition in normalized_trace["transitions"]:
            if (
                transition["stage"] != "verifier"
                or transition["outcome"] in {"dispatched", "retrying", "repairing"}
                or transition["artifactId"] is None
            ):
                continue
            artifact_id = str(transition["artifactId"])
            execution_record = execution_before(transition)
            verification_execution_assignment = execution_record[1] if execution_record else None
            verification_execution_attempt_id = execution_record[0]["attemptId"] if execution_record else None
            execution_change_set = execution_record[2].get("changeSet") if execution_record else None
            document = artifact_documents.get(artifact_id)
            expected_artifact_id = (
                f"artifact_{transition['assignmentId']}_"
                f"{transition['attemptId']}_verification"
            )
            result_type = (
                document.get("resultType")
                if isinstance(document, dict)
                else None
            )
            if artifact_id in receipt_artifacts:
                continue
            if (
                transition["outcome"] == "waiting_human"
                and result_type
                in {"HumanApproval.v1", "HumanApprovalRequest.v1"}
            ):
                if (
                    result_type != "HumanApproval.v1"
                    or transition["errorCode"] != "human_approval_expired"
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler Verifier human authority gate is inconsistent"
                    )
                continue
            if (
                artifact_id != expected_artifact_id
                or result_type != "VerificationResult.v1"
                or verification_execution_assignment is None
                or not isinstance(execution_change_set, dict)
            ):
                raise SchedulerAuditValidationError(
                    "scheduler Verifier terminal artifact is invalid"
                )
            try:
                parsed_verification = parse_verification_result(
                    {
                        key: value
                        for key, value in document.items()
                        if key != "resultType"
                    },
                    workflow_id=workflow_id,
                    task_id=profile.task_id,
                    execution_assignment_id=(
                        verification_execution_assignment.assignment_id
                    ),
                    patch_digest=str(execution_change_set["patchDigest"]),
                    required_check_ids=tuple(request_checks_by_id),
                )
            except (ContractValidationError, TypeError, ValueError) as exc:
                raise SchedulerAuditValidationError(
                    "scheduler VerificationResult is invalid"
                ) from exc
            checks = parsed_verification.get("checks")
            checks_by_id = {
                str(check.get("checkId")): check
                for check in checks
                if isinstance(check, dict)
            } if isinstance(checks, list) else {}
            if (
                parsed_verification != document
                or set(checks_by_id) != set(request_checks_by_id)
            ):
                raise SchedulerAuditValidationError(
                    "scheduler VerificationResult is not canonical"
                )
            for check_id, check in checks_by_id.items():
                evidence_ref = check.get("artifactRef")
                if not isinstance(evidence_ref, str):
                    raise SchedulerAuditValidationError(
                        "scheduler verification evidence is missing"
                    )
                _validate_verification_evidence(
                    artifact_documents.get(evidence_ref),
                    artifact_id=evidence_ref,
                    workflow_id=workflow_id,
                    execution_assignment_id=(
                        verification_execution_assignment.assignment_id
                    ),
                    patch_digest=str(execution_change_set["patchDigest"]),
                    result_check=check,
                    request_check=request_checks_by_id[check_id],
                )
            expected_diff_id = (
                f"artifact_{verification_execution_assignment.assignment_id}_"
                f"{verification_execution_attempt_id}_frozen_diff"
            )
            frozen_diff = artifact_documents.get(expected_diff_id)
            if frozen_diff is not None:
                # Retain bounded previews of superseded repair candidates;
                # only the final revision earns commit authority. Every
                # revision still has to match its own baseline/patch digest.
                _validate_frozen_diff(
                    frozen_diff,
                    base_digest=str(execution_change_set["baseDigest"]),
                    patch_digest=str(execution_change_set["patchDigest"]),
                    require_complete=(
                        normalized_trace.get("committed") is True
                        and execution_record == execution_records[-1]
                    ),
                )
            criterion_evidence_incomplete = any(
                any(
                    checks_by_id[check.check_id].get("status") != "passed"
                    for check in request.verification_checks
                    if criterion.criterion_id in check.criterion_ids
                )
                for criterion in request.criteria
            )
            verification_status = str(parsed_verification["status"])
            if frozen_diff is None:
                expected_verifier_transition = (
                    "inconclusive",
                    "frozen_diff_evidence_missing",
                )
            elif (criterion_evidence_incomplete
                  or _has_pending_executor_feedback(execution_record[2])):
                expected_verifier_transition = (
                    "inconclusive",
                    "acceptance_evidence_incomplete",
                )
            elif verification_status == "passed":
                expected_verifier_transition = ("completed", None)
            else:
                expected_verifier_transition = (
                    verification_status,
                    f"verification_{verification_status}",
                )
            if (
                transition["outcome"],
                transition["errorCode"],
            ) != expected_verifier_transition:
                raise SchedulerAuditValidationError(
                    "scheduler Verifier terminal cause is inconsistent"
                )
            validated_verification_documents[artifact_id] = parsed_verification
    completed_verifier_transitions = [
        transition
        for transition in normalized_trace["transitions"]
        if transition["stage"] == "verifier"
        and transition["outcome"] == "completed"
    ]
    has_reviewer = False
    matrices_by_verification = {
        str(matrix["verificationArtifactRef"]): (matrix_id, matrix)
        for matrix_id, matrix in acceptance_matrices.items()
    }
    if contract_mode == "v2" and (
        len(matrices_by_verification) != len(acceptance_matrices)
        or set(matrices_by_verification) != {
            str(item["artifactId"]) for item in completed_verifier_transitions
        }
    ):
        raise SchedulerAuditValidationError(
            "scheduler acceptance matrices do not match completed verification revisions"
        )
    for verified_transition in (
        completed_verifier_transitions if contract_mode == "v2" else []
    ):
        matrix_id, matrix = matrices_by_verification[str(verified_transition["artifactId"])]
        matrix_execution_record = execution_before(verified_transition)
        if matrix_execution_record is None:
            raise SchedulerAuditValidationError("scheduler matrix lacks its execution revision")
        matrix_execution_assignment = matrix_execution_record[1]
        matrix_execution_document = matrix_execution_record[2]
        verification_document = artifact_documents.get(
            str(matrix["verificationArtifactRef"])
        )
        diff_document = artifact_documents.get(str(matrix["frozenDiffRef"]))
        review_assignments_for_evidence = [
            assignments_by_attempt[
                (
                    str(transition["assignmentId"]),
                    str(transition["attemptId"]),
                )
            ]
            for transition in normalized_trace["transitions"]
            if transition["stage"] == "reviewer"
            and execution_before(transition) == matrix_execution_record
            and transition["outcome"] == "dispatched"
            and (
                str(transition["assignmentId"]),
                str(transition["attemptId"]),
            )
            in assignments_by_attempt
        ]
        reviewer_preflight_blocked = any(
            transition["stage"] == "reviewer"
            and execution_before(transition) == matrix_execution_record
            and (
                str(transition["assignmentId"]),
                str(transition["attemptId"]),
            )
            in human_preflight_blocked_attempts
            for transition in normalized_trace["transitions"]
        )
        final_reviewer_input_refs = (
            set(review_assignments_for_evidence[-1].input_refs)
            if review_assignments_for_evidence
            else set()
        )
        execution_change_set = (
            matrix_execution_document.get("changeSet")
            if matrix_execution_document is not None
            else None
        )
        # The scoped route has no Reviewer to bind the matrix into; every
        # digest-chain condition below still applies unchanged.
        has_reviewer = bool(review_assignments_for_evidence)
        if (
            matrix["verificationArtifactRef"]
            != verified_transition["artifactId"]
            or matrix_execution_assignment is None
            or not isinstance(execution_change_set, dict)
            or (
                has_reviewer
                and not reviewer_preflight_blocked
                and matrix_id not in final_reviewer_input_refs
            )
            or (
                has_reviewer
                and not reviewer_preflight_blocked
                and matrix["frozenDiffRef"] not in final_reviewer_input_refs
            )
            or not isinstance(verification_document, dict)
            or verification_document.get("resultType")
            != "VerificationResult.v1"
            or verification_document.get("status") != "passed"
            or verification_document.get("patchDigest") != matrix["patchDigest"]
            or verification_document.get("executionAssignmentId")
            != matrix_execution_assignment.assignment_id
            or execution_change_set.get("patchDigest") != matrix["patchDigest"]
            or not isinstance(diff_document, dict)
            or diff_document.get("patchDigest") != matrix["patchDigest"]
        ):
            raise SchedulerAuditValidationError(
                "scheduler acceptance evidence matrix authority is inconsistent"
            )
        _validate_frozen_diff(
            diff_document,
            base_digest=str(execution_change_set["baseDigest"]),
            patch_digest=str(execution_change_set["patchDigest"]),
            require_complete=(
                normalized_trace.get("committed") is True
                and matrix_execution_record == execution_records[-1]
            ),
        )
        analyst_handoff_id = next(
            (
                str(transition["artifactId"])
                for transition in normalized_trace["transitions"]
                if transition["stage"] == "analyst"
                and transition["outcome"] == "completed"
                and transition["artifactId"] is not None
            ),
            None,
        )
        execution_artifact_id = str(matrix_execution_record[0]["artifactId"])
        if review_assignments_for_evidence:
            # Policy v4 scoped work skips preliminary analysis, not final
            # review. Require Analyst authority exactly when the sealed plan
            # contains that role; never invent an optional handoff.
            has_analyst = DispatchStage.ANALYST in plan.stages
            analyst_handoff = artifact_documents.get(str(analyst_handoff_id))
            if has_analyst and (
                not isinstance(analyst_handoff, dict)
                or not isinstance(analyst_handoff.get("evidenceSnapshotRefs"), list)
            ) or not has_analyst and analyst_handoff_id is not None:
                raise SchedulerAuditValidationError(
                    "scheduler Reviewer Analyst handoff is invalid"
                )
            expected_reviewer_refs = (
                *((str(analyst_handoff_id),) if has_analyst else ()),
                request_artifact_id,
                *(
                    str(ref)
                    for ref in (analyst_handoff["evidenceSnapshotRefs"] if has_analyst else ())
                ),
                str(matrix_execution_document["orderId"]),
                execution_artifact_id,
                str(matrix["frozenDiffRef"]),
                str(matrix["verificationArtifactRef"]),
                *(
                    str(check["artifactRef"])
                    for check in verification_document["checks"]
                ),
                matrix_id,
            )
            if any(
                assignment.input_refs != expected_reviewer_refs
                for assignment in review_assignments_for_evidence
            ):
                raise SchedulerAuditValidationError(
                    "scheduler Reviewer input topology is inconsistent"
                )
        checks = verification_document.get("checks")
        if not isinstance(checks, list):
            raise SchedulerAuditValidationError(
                "scheduler acceptance evidence matrix checks are invalid"
            )
        checks_by_id = {
            str(check.get("checkId")): check
            for check in checks
            if isinstance(check, dict)
        }
        request = next(iter(workflow_requests.values()))
        parsed_verification = validated_verification_documents.get(
            str(verified_transition["artifactId"])
        )
        if parsed_verification != verification_document:
            raise SchedulerAuditValidationError(
                "scheduler VerificationResult is not canonical"
            )
        request_checks_by_id = {
            check.check_id: check for check in request.verification_checks
        }
        if set(checks_by_id) != set(request_checks_by_id):
            raise SchedulerAuditValidationError(
                "scheduler VerificationResult check set is inconsistent"
            )
        for check_id, check in checks_by_id.items():
            evidence_ref = check.get("artifactRef")
            if (
                not isinstance(evidence_ref, str)
                or (
                    has_reviewer
                    and not reviewer_preflight_blocked
                    and evidence_ref not in final_reviewer_input_refs
                )
            ):
                raise SchedulerAuditValidationError(
                    "scheduler verification evidence is not reviewable"
                )
            _validate_verification_evidence(
                artifact_documents.get(evidence_ref),
                artifact_id=evidence_ref,
                workflow_id=workflow_id,
                execution_assignment_id=(
                    matrix_execution_assignment.assignment_id
                ),
                patch_digest=str(verification_document["patchDigest"]),
                result_check=check,
                request_check=request_checks_by_id[check_id],
            )
        expected_checks_by_criterion = {
            criterion.criterion_id: [
                check.check_id
                for check in request.verification_checks
                if criterion.criterion_id in check.criterion_ids
            ]
            for criterion in request.criteria
        }
        observed_criteria: set[str] = set()
        for index, entry in enumerate(matrix["criteria"]):
            if (
                not isinstance(entry, dict)
                or set(entry) != {"criterionId", "checkIds", "evidenceRefs"}
                or not isinstance(entry.get("criterionId"), str)
                or not isinstance(entry.get("checkIds"), list)
                or not entry["checkIds"]
                or not isinstance(entry.get("evidenceRefs"), list)
                or len(entry["checkIds"]) != len(entry["evidenceRefs"])
                or entry["criterionId"] in observed_criteria
            ):
                raise SchedulerAuditValidationError(
                    f"scheduler acceptance matrix criterion {index} is invalid"
                )
            observed_criteria.add(entry["criterionId"])
            if entry["checkIds"] != expected_checks_by_criterion.get(
                entry["criterionId"]
            ):
                raise SchedulerAuditValidationError(
                    "scheduler acceptance matrix request mapping is invalid"
                )
            for check_id, evidence_ref in zip(
                entry["checkIds"],
                entry["evidenceRefs"],
                strict=True,
            ):
                check = checks_by_id.get(str(check_id))
                if (
                    not isinstance(evidence_ref, str)
                    or not isinstance(check, dict)
                    or check.get("status") != "passed"
                    or check.get("artifactRef") != evidence_ref
                    or evidence_ref not in artifact_ids
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler acceptance matrix evidence is invalid"
                    )
        # Without a Reviewer (scoped route, or a preflight-blocked one) the
        # matrix must still cover the request's own acceptance criteria.
        final_reviewer_criteria = (
            {
                item.criterion_id
                for item in review_assignments_for_evidence[-1].acceptance_criteria
            }
            if review_assignments_for_evidence
            else {item.criterion_id for item in request.criteria}
        )
        if observed_criteria != final_reviewer_criteria:
            raise SchedulerAuditValidationError(
                "scheduler acceptance matrix criteria are incomplete"
            )
        matrix_by_criterion = {
            str(entry["criterionId"]): entry
            for entry in matrix["criteria"]
        }
        for transition, review, _assignment in (
            *completed_review_records,
            *terminal_review_results,
        ):
            if execution_before(transition) != matrix_execution_record:
                continue
            evidence_binding_invalid = False
            for criterion_result in review["criteria"]:
                if criterion_result["status"] != "satisfied":
                    continue
                matrix_entry = matrix_by_criterion.get(
                    str(criterion_result["criterionId"])
                )
                required_refs = (
                    {
                        matrix_id,
                        str(matrix["frozenDiffRef"]),
                        *(
                            str(ref)
                            for ref in matrix_entry["evidenceRefs"]
                        ),
                    }
                    if isinstance(matrix_entry, dict)
                    else set()
                )
                if not required_refs or not required_refs.issubset(
                    set(criterion_result["evidenceRefs"])
                ):
                    evidence_binding_invalid = True
                    break
            if evidence_binding_invalid:
                expected_review_transition = (
                    "failed",
                    "review_evidence_binding_invalid",
                )
            elif review["patchDigest"] != execution_change_set["patchDigest"]:
                expected_review_transition = (
                    "rejected",
                    "review_patch_digest_mismatch",
                )
            elif diff_document.get("complete") is not True:
                expected_review_transition = (
                    "rejected",
                    "review_evidence_truncated",
                )
            elif review["verdict"] != "approved":
                expected_review_transition = (
                    "rejected",
                    "review_not_approved",
                )
            else:
                expected_review_transition = ("completed", None)
            if (
                transition["outcome"],
                transition["errorCode"],
            ) != expected_review_transition:
                raise SchedulerAuditValidationError(
                    "scheduler Reviewer terminal cause is inconsistent"
                )
    if (
        contract_mode == "v2"
        and normalized_trace.get("committed") is True
        and has_reviewer
        and (
            len(completed_review_results) != 1
            or completed_review_results[0].get("verdict") != "approved"
        )
    ):
        raise SchedulerAuditValidationError(
            "committed scheduler audit lacks Reviewer approval"
        )
    if contract_mode == "v2" and plan.requires_human:
        human_transitions = [
            transition
            for transition in normalized_trace["transitions"]
            if transition["stage"] == "human"
        ]
        completed_human_artifact_ids = {
            str(transition["artifactId"])
            for transition in human_transitions
            if transition["outcome"] == "completed"
            and transition["artifactId"] is not None
        }
        waiting_human_artifact_ids = {
            str(transition["artifactId"])
            for transition in human_transitions
            if transition["outcome"] == "waiting_human"
            and transition["artifactId"] is not None
        }
        if not approval_requests:
            if (
                approvals
                or profile.human_confirmation_granted
                or completed_human_artifact_ids
                or waiting_human_artifact_ids
            ):
                raise SchedulerAuditValidationError(
                    "scheduler human approval authority is missing"
                )
            # A workflow may terminate at Analyst (or fail at the Human port
            # before publishing a request).  Stage-prefix validation already
            # proves that no later stage ran; absence of unused authority is
            # therefore the exact replayable state.
            return SchedulerAudit(
                profile=profile,
                plan=plan,
                trace=normalized_trace,
                artifacts=artifacts,
                contract_mode=contract_mode,
            )
        if len(approval_requests) != 1 or not any(
            transition["outcome"] == "dispatched"
            for transition in human_transitions
        ):
            raise SchedulerAuditValidationError(
                "scheduler human approval request is orphaned"
            )
        approval_id, request_document = next(iter(approval_requests.items()))
        for transition in human_transitions:
            artifact_id = transition["artifactId"]
            if artifact_id == f"artifact_{approval_id}_request" and (
                transition["outcome"],
                transition["errorCode"],
            ) != ("waiting_human", "human_approval_required"):
                raise SchedulerAuditValidationError(
                    "scheduler Human approval request cause is inconsistent"
                )
            if artifact_id == f"artifact_{approval_id}" and (
                transition["outcome"],
                transition["errorCode"],
            ) != ("completed", None):
                raise SchedulerAuditValidationError(
                    "scheduler Human approval completion is inconsistent"
                )
        analyst_artifact_id = str(request_document["analystArtifactId"])
        analyst_document = artifact_documents.get(analyst_artifact_id)
        analyst_digest = human_approval_analysis_digest(
            analyst_document,
            artifact_documents,
        )
        completed_analyst_artifact_ids = {
            str(transition["artifactId"])
            for transition in normalized_trace["transitions"]
            if transition["stage"] == "analyst"
            and transition["outcome"] == "completed"
            and transition["artifactId"] is not None
        }
        workflow_request_document = next(
            (
                document
                for document in artifact_documents.values()
                if isinstance(document, dict)
                and document.get("resultType")
                == "WorkflowRequestArtifact.v1"
            ),
            None,
        )
        if (
            completed_analyst_artifact_ids != {analyst_artifact_id}
            or not isinstance(workflow_request_document, dict)
            or request_document["requestDigest"]
            != workflow_request_document.get("requestDigest")
            or any(
                artifact_documents[str(ref)].get("baseDigest")
                != request_document["baseDigest"]
                for ref in analyst_document["evidenceSnapshotRefs"]
            )
            or (
                completed_execution_document is not None
                and request_document["baseDigest"]
                != completed_execution_document["changeSet"]["baseDigest"]
            )
            or analyst_digest != request_document["analystDigest"]
        ):
            raise SchedulerAuditValidationError(
                "scheduler human approval is not bound to sealed analysis"
            )
        if profile.human_confirmation_granted:
            approval_document = approvals.get(approval_id)
            if (
                profile.human_approval_id != approval_id
                or len(approvals) != 1
                or approval_document is None
                or completed_human_artifact_ids
                != {f"artifact_{approval_id}"}
                or approval_document["requestDigest"]
                != request_document["requestDigest"]
                or approval_document["baseDigest"]
                != request_document["baseDigest"]
                or approval_document["analystDigest"]
                != request_document["analystDigest"]
            ):
                raise SchedulerAuditValidationError(
                    "scheduler human approval does not match its request"
                )
        elif (
            approvals
            or completed_human_artifact_ids
            or (
                waiting_human_artifact_ids
                and waiting_human_artifact_ids
                != {f"artifact_{approval_id}_request"}
            )
        ):
            raise SchedulerAuditValidationError(
                "scheduler human approval is inconsistent with routing"
            )
    return SchedulerAudit(
        profile=profile,
        plan=plan,
        trace=normalized_trace,
        artifacts=artifacts,
        contract_mode=contract_mode,
    )


def _profile_dict(profile: TaskProfile, *, policy_version: int) -> dict[str, object]:
    result = {
        "taskId": profile.task_id,
        "kind": profile.kind.value,
        "risk": profile.risk.value,
        "complex": profile.complex,
        "parentHasSufficientContext": profile.parent_has_sufficient_context,
        "scopeClear": profile.scope_clear,
        "acceptanceExecutable": profile.acceptance_executable,
        "humanConfirmationRequired": profile.human_confirmation_required,
        "humanConfirmationGranted": profile.human_confirmation_granted,
        "humanApprovalId": profile.human_approval_id,
    }
    if policy_version >= 5:
        result["independentReviewRequired"] = profile.independent_review_required
    return result


def _plan_dict(plan: DispatchPlan) -> dict[str, object]:
    return {
        "taskId": plan.task_id,
        "policyVersion": plan.policy_version,
        "ruleId": plan.rule_id,
        "stages": [stage.value for stage in plan.stages],
        "requiresHuman": plan.requires_human,
    }


def _artifact_index(
    artifacts: tuple[SchedulerArtifact, ...],
) -> list[dict[str, object]]:
    index: list[dict[str, object]] = []
    for artifact in artifacts:
        encoded = _canonical_artifact_content(artifact)
        index.append(
            {
                "artifactId": artifact.artifact_id,
                "contentSha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
                "byteCount": len(encoded),
            }
        )
    return index


def _artifact_content_digest(artifact: SchedulerArtifact) -> str:
    return "sha256:" + hashlib.sha256(
        artifact.content.encode("utf-8")
    ).hexdigest()


def _artifact_set_digest(artifacts: tuple[SchedulerArtifact, ...]) -> str:
    index = [
        {
            "artifactId": artifact.artifact_id,
            "contentDigest": _artifact_content_digest(artifact),
        }
        for artifact in artifacts
    ]
    encoded = json.dumps(
        index,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_commit_ready_against_audit(
    ready: SchedulerCommitReady,
    audit: SchedulerAudit,
) -> None:
    """Rejoin the pre-commit seal to the exact final authority artifacts."""
    if (
        ready.workflow_id != audit.workflow_id
        or ready.task_id != audit.task_id
        or ready.rule_id != audit.plan.rule_id
        or ready.artifact_set_digest != _artifact_set_digest(audit.artifacts)
    ):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready does not match final audit identity"
        )
    approval_binding_required = (
        audit.plan.requires_human
        and audit.profile.human_confirmation_granted
    )
    if approval_binding_required != (ready.approval_digest is not None):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready human approval binding is inconsistent"
        )
    documents: list[tuple[SchedulerArtifact, object]] = [
        (artifact, _closed_json_document(artifact.content))
        for artifact in audit.artifacts
    ]
    request_documents = [
        document
        for _artifact, document in documents
        if isinstance(document, dict)
        and document.get("resultType") == "WorkflowRequestArtifact.v1"
    ]
    verification_binding = next(
        (
            (artifact, document)
            for artifact, document in documents
            if _artifact_content_digest(artifact) == ready.verification_digest
        ),
        None,
    )
    review_binding = next(
        (
            (artifact, document)
            for artifact, document in documents
            if _artifact_content_digest(artifact) == ready.review_digest
        ),
        None,
    )
    approval_binding = (
        next(
            (
                (artifact, document)
                for artifact, document in documents
                if _artifact_content_digest(artifact) == ready.approval_digest
            ),
            None,
        )
        if ready.approval_digest is not None
        else None
    )
    matrix_binding = next(
        (
            (artifact, document)
            for artifact, document in documents
            if isinstance(document, dict)
            and document.get("resultType") == "AcceptanceEvidenceMatrix.v1"
            and verification_binding is not None
            and document.get("verificationArtifactRef") == verification_binding[0].artifact_id
        ),
        None,
    )
    verification_artifact, verification = (
        verification_binding
        if verification_binding is not None
        else (None, None)
    )
    review_artifact, review = (
        review_binding if review_binding is not None else (None, None)
    )
    approval_artifact, approval = (
        approval_binding if approval_binding is not None else (None, None)
    )
    _matrix_artifact, matrix = (
        matrix_binding if matrix_binding is not None else (None, None)
    )
    diff_binding = (
        next(
            (
                (artifact, document)
                for artifact, document in documents
                if isinstance(matrix, dict)
                and artifact.artifact_id == matrix.get("frozenDiffRef")
            ),
            None,
        )
        if matrix is not None
        else None
    )
    diff_artifact, diff = (
        diff_binding if diff_binding is not None else (None, None)
    )
    last_repair_sequence = max((
        int(transition["sequence"]) for transition in audit.trace["transitions"]
        if transition["outcome"] == "repairing"
    ), default=0)
    completed_artifacts_by_stage = {
        stage: {
            str(transition["artifactId"])
            for transition in audit.trace["transitions"]
            if transition["stage"] == stage
            and transition["outcome"] == "completed"
            and transition["artifactId"] is not None
            and (stage == "human" or transition["sequence"] > last_repair_sequence)
        }
        for stage in ("verifier", "reviewer", "human")
    }
    # The audit has already been re-projected against CentralScheduler.plan,
    # including the request's effective risk/scope and execution evidence.
    # Only its exact scoped route omits Reviewer. Its pre-commit seal uses
    # the final Verifier artifact in both digest slots; it does not invent a
    # review verdict. All other routes must retain the completed Reviewer
    # binding, and both routes still bind the request, matrix, and frozen diff.
    scoped_without_reviewer = (
        audit.plan.rule_id in {"route.scoped_change", "route.verified_change"}
        and audit.plan.stages == (DispatchStage.EXECUTOR, DispatchStage.VERIFIER)
    )
    if audit.plan.policy_version >= 5:
        routing = [document for _artifact, document in documents
                   if isinstance(document, dict) and document.get("resultType") == "ReviewRoutingDecision.v1"]
        if (len(routing) != 1 or routing[0].get("baseDigest") != ready.base_digest
                or routing[0].get("requestDigest") != ready.request_digest):
            raise SchedulerAuditValidationError("scheduler commit-ready review routing binding is inconsistent")
    if scoped_without_reviewer:
        review_authority_valid = (
            ready.review_digest == ready.verification_digest
            and review_artifact is not None
            and verification_artifact is not None
            and review_artifact.artifact_id == verification_artifact.artifact_id
        )
    else:
        review_authority_valid = (
            isinstance(review, dict)
            and review.get("verdict") == "approved"
            and review.get("patchDigest") == ready.patch_digest
            and review_artifact is not None
            and review_artifact.artifact_id in completed_artifacts_by_stage["reviewer"]
        )
    if (
        len(request_documents) != 1
        or request_documents[0].get("workflowId") != audit.workflow_id
        or request_documents[0].get("taskId") != audit.task_id
        or request_documents[0].get("requestDigest") != ready.request_digest
        or not isinstance(verification, dict)
        or verification.get("resultType") != "VerificationResult.v1"
        or verification.get("status") != "passed"
        or verification.get("patchDigest") != ready.patch_digest
        or verification_artifact is None
        or verification_artifact.artifact_id
        not in completed_artifacts_by_stage["verifier"]
        or not isinstance(matrix, dict)
        or matrix.get("verificationArtifactRef")
        != verification_artifact.artifact_id
        or not review_authority_valid
        or not isinstance(diff, dict)
        or diff_artifact is None
        or matrix.get("frozenDiffRef") != diff_artifact.artifact_id
        or diff.get("patchDigest") != ready.patch_digest
        or diff.get("baseDigest") != ready.base_digest
    ):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready authority binding is inconsistent"
        )
    if ready.approval_digest is not None and (
        not isinstance(approval, dict)
        or approval.get("resultType") != "HumanApproval.v1"
        or approval.get("decision") != "approved"
        or approval_artifact is None
        or approval_artifact.artifact_id
        not in completed_artifacts_by_stage["human"]
    ):
        raise SchedulerAuditValidationError(
            "scheduler commit-ready approval binding is inconsistent"
        )


def _validate_available_commit_ready(
    ready: SchedulerCommitReady | None,
    audit: SchedulerAudit,
) -> None:
    """Join any durable pre-commit seal to success or failure audit state."""
    commit_requested = any(
        transition["stage"] == "commit"
        and transition["outcome"] == "commit_requested"
        for transition in audit.trace["transitions"]
    )
    if ready is None:
        if audit.trace.get("committed") is True:
            raise SchedulerAuditValidationError(
                "committed scheduler audit requires commit-ready"
            )
        return
    if not commit_requested:
        raise SchedulerAuditValidationError(
            "scheduler commit-ready is orphaned from final audit"
        )
    _validate_commit_ready_against_audit(ready, audit)


def _manifest(audit: SchedulerAudit) -> dict[str, object]:
    return {
        "auditVersion": audit.audit_version,
        "contractMode": audit.contract_mode,
        "workflowId": audit.workflow_id,
        "taskId": audit.task_id,
        "effectiveProfile": _profile_dict(audit.profile, policy_version=audit.plan.policy_version),
        "dispatchPlan": _plan_dict(audit.plan),
        "schedulerTrace": audit.trace,
        "artifacts": _artifact_index(audit.artifacts),
    }


def _canonical_document(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


class SchedulerAuditJournal:
    """Write and query bounded scheduler audits below one canonical Run."""

    def __init__(self, run_dir: Path) -> None:
        candidate = Path(run_dir)
        try:
            info = os.lstat(candidate)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise OSError("unsafe Run directory")
            self._run_dir = candidate.resolve(strict=True)
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit Run directory is unsafe"
            ) from exc
        self._root = self._run_dir / "scheduler-workflows"

    @staticmethod
    def _require_directory(path: Path) -> None:
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit directory is unavailable"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SchedulerAuditStorageError("scheduler audit directory is unsafe")

    @classmethod
    def _ensure_directory(cls, path: Path, *, parent: Path) -> None:
        cls._require_directory(parent)
        created = False
        try:
            path.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit directory is unavailable"
            ) from exc
        cls._require_directory(path)
        try:
            if path.resolve(strict=True).parent != parent.resolve(strict=True):
                raise SchedulerAuditStorageError(
                    "scheduler audit directory escapes its parent"
                )
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit directory is unsafe"
            ) from exc
        if created:
            cls._fsync_directory(parent)

    def _ensure_root(self) -> None:
        self._ensure_directory(self._root, parent=self._run_dir)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_file(path: Path, *, max_bytes: int) -> bytes:
        try:
            before = os.lstat(path)
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit file is unavailable"
            ) from exc
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            raise SchedulerAuditStorageError("scheduler audit file is unsafe")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                after = os.fstat(descriptor)
                if (
                    after.st_dev != before.st_dev
                    or after.st_ino != before.st_ino
                    or after.st_size != before.st_size
                    or not stat.S_ISREG(after.st_mode)
                ):
                    raise OSError("scheduler audit file changed during read")
                raw = os.read(descriptor, max_bytes + 1)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit file is unsafe"
            ) from exc
        if len(raw) != before.st_size:
            raise SchedulerAuditStorageError("scheduler audit file read was short")
        return raw

    @classmethod
    def _write_once(cls, path: Path, payload: bytes) -> bool:
        cls._require_directory(path.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".scheduler-audit-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        created = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                if os.name == "posix":
                    os.fchmod(handle.fileno(), 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path, follow_symlinks=False)
                created = True
            except FileExistsError:
                if cls._read_file(path, max_bytes=max(len(payload), 1)) != payload:
                    raise SchedulerAuditStorageError(
                        "scheduler audit write-once content changed"
                    )
            cls._fsync_directory(path.parent)
        except SchedulerAuditStorageError:
            raise
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit file could not be persisted"
            ) from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return created

    def _prepare_workflow_directory(self, workflow_id: str) -> Path:
        self._ensure_root()
        try:
            entries = list(self._root.iterdir())
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit root could not be scanned"
            ) from exc
        for entry in entries:
            if (
                _WORKFLOW_ID_RE.fullmatch(entry.name) is None
                or entry.is_symlink()
                or not entry.is_dir()
            ):
                raise SchedulerAuditStorageError(
                    "scheduler audit workflow directory is unsafe"
                )
        workflow_dir = self._root / workflow_id
        if not workflow_dir.exists() and len(entries) >= _MAX_WORKFLOWS_PER_RUN:
            raise SchedulerAuditStorageError(
                "scheduler audit workflow limit was reached"
            )
        self._ensure_directory(workflow_dir, parent=self._root)
        return workflow_dir

    def write(self, audit: SchedulerAudit) -> None:
        if not isinstance(audit, SchedulerAudit):
            raise SchedulerAuditValidationError("scheduler audit value is invalid")
        # Re-project to reject manually constructed, internally inconsistent
        # dataclass instances before any filesystem mutation.
        validated = project_scheduler_audit(
            profile=audit.profile,
            plan=audit.plan,
            trace=audit.trace,
            artifacts=audit.artifacts,
            contract_mode=audit.contract_mode,
        )
        if validated.contract_mode == "v2":
            _validate_available_commit_ready(
                self.get_commit_ready(validated.workflow_id),
                validated,
            )
        workflow_dir = self._prepare_workflow_directory(validated.workflow_id)
        artifacts_dir = workflow_dir / "artifacts"
        self._ensure_directory(artifacts_dir, parent=workflow_dir)
        for artifact in validated.artifacts:
            self._write_once(
                artifacts_dir / f"{artifact.artifact_id}.json",
                _canonical_artifact_content(artifact),
            )
        manifest_payload = _canonical_document(_manifest(validated))
        if len(manifest_payload) > _MAX_MANIFEST_BYTES:
            raise SchedulerAuditValidationError(
                "scheduler audit manifest exceeds the byte limit"
            )
        # The manifest is deliberately last: its presence seals the snapshot.
        self._write_once(workflow_dir / "manifest.json", manifest_payload)

    def write_commit_ready(self, ready: SchedulerCommitReady) -> bool:
        """Persist one pre-commit authorization, idempotently and write-once."""
        if not isinstance(ready, SchedulerCommitReady):
            raise SchedulerAuditValidationError(
                "scheduler commit-ready value is invalid"
            )
        # Round-trip through the public parser so manually constructed
        # dataclass instances cannot bypass any binding or ID invariant.
        validated = parse_scheduler_commit_ready(ready.to_dict())
        workflow_dir = self._prepare_workflow_directory(validated.workflow_id)
        payload = _canonical_document(validated.to_dict())
        if len(payload) > _MAX_COMMIT_READY_BYTES:
            raise SchedulerAuditValidationError(
                "scheduler commit-ready document exceeds the byte limit"
            )
        return self._write_once(
            workflow_dir / _COMMIT_READY_FILENAME,
            payload,
        )

    @staticmethod
    def _parse_document(raw: bytes, *, label: str) -> dict[str, object]:
        try:
            text = raw.decode("utf-8")
            parsed = _closed_json_document(text)
        except (UnicodeDecodeError, SchedulerAuditValidationError) as exc:
            raise SchedulerAuditStorageError(
                f"scheduler audit {label} is invalid"
            ) from exc
        if not isinstance(parsed, dict):
            raise SchedulerAuditStorageError(
                f"scheduler audit {label} is invalid"
            )
        return parsed

    def get_commit_ready(self, workflow_id: str) -> SchedulerCommitReady | None:
        """Read and revalidate one exact pre-commit authorization."""
        if (
            not isinstance(workflow_id, str)
            or _WORKFLOW_ID_RE.fullmatch(workflow_id) is None
        ):
            raise SchedulerAuditValidationError("scheduler workflow id is invalid")
        if not self._root.exists() and not self._root.is_symlink():
            return None
        self._require_directory(self._root)
        workflow_dir = self._root / workflow_id
        if not workflow_dir.exists() and not workflow_dir.is_symlink():
            return None
        self._require_directory(workflow_dir)
        path = workflow_dir / _COMMIT_READY_FILENAME
        if not path.exists() and not path.is_symlink():
            return None
        raw = self._read_file(path, max_bytes=_MAX_COMMIT_READY_BYTES)
        document = self._parse_document(raw, label="commit-ready")
        try:
            ready = parse_scheduler_commit_ready(document)
            if (
                ready.workflow_id != workflow_id
                or _canonical_document(ready.to_dict()) != raw
            ):
                raise SchedulerAuditValidationError(
                    "scheduler commit-ready document is inconsistent"
                )
        except SchedulerAuditValidationError as exc:
            raise SchedulerAuditStorageError(
                "scheduler commit-ready snapshot is invalid"
            ) from exc
        return ready

    def _load(self, workflow_id: str, workflow_dir: Path) -> SchedulerAudit | None:
        self._require_directory(workflow_dir)
        manifest_path = workflow_dir / "manifest.json"
        if not manifest_path.exists() and not manifest_path.is_symlink():
            return None
        raw_manifest = self._read_file(
            manifest_path,
            max_bytes=_MAX_MANIFEST_BYTES,
        )
        manifest = self._parse_document(raw_manifest, label="manifest")
        try:
            manifest = _closed_mapping(
                manifest,
                _MANIFEST_FIELDS,
                label="scheduler audit manifest",
            )
            if (
                type(manifest["auditVersion"]) is not int
                or manifest["auditVersion"] != SCHEDULER_AUDIT_VERSION
                or manifest["workflowId"] != workflow_id
            ):
                raise SchedulerAuditValidationError(
                    "scheduler audit identity is inconsistent"
                )
            profile_fields = _PROFILE_FIELDS
            if isinstance(manifest["effectiveProfile"], dict) and "independentReviewRequired" in manifest["effectiveProfile"]:
                profile_fields = profile_fields | {"independentReviewRequired"}
            profile_raw = _closed_mapping(
                manifest["effectiveProfile"], profile_fields, label="effective profile",
            )
            profile = TaskProfile(
                task_id=str(profile_raw["taskId"]),
                kind=TaskKind(profile_raw["kind"]),
                risk=TaskRisk(profile_raw["risk"]),
                complex=profile_raw["complex"],
                parent_has_sufficient_context=profile_raw[
                    "parentHasSufficientContext"
                ],
                scope_clear=profile_raw["scopeClear"],
                acceptance_executable=profile_raw["acceptanceExecutable"],
                human_confirmation_required=profile_raw[
                    "humanConfirmationRequired"
                ],
                human_confirmation_granted=profile_raw[
                    "humanConfirmationGranted"
                ],
                human_approval_id=profile_raw["humanApprovalId"],
                independent_review_required=profile_raw.get("independentReviewRequired", True),
            )
            plan_raw = _closed_mapping(
                manifest["dispatchPlan"],
                _PLAN_FIELDS,
                label="dispatch plan",
            )
            stages_raw = plan_raw["stages"]
            if not isinstance(stages_raw, list):
                raise SchedulerAuditValidationError("dispatch stages are invalid")
            plan = DispatchPlan(
                task_id=str(plan_raw["taskId"]),
                stages=tuple(DispatchStage(stage) for stage in stages_raw),
                rule_id=str(plan_raw["ruleId"]),
                policy_version=plan_raw["policyVersion"],
                requires_human=plan_raw["requiresHuman"],
            )
            artifact_index = manifest["artifacts"]
            if (
                not isinstance(artifact_index, list)
                or len(artifact_index)
                > MAX_SCHEDULER_ARTIFACTS_PER_WORKFLOW
            ):
                raise SchedulerAuditValidationError(
                    "scheduler audit artifact index is invalid"
                )
            artifacts_dir = workflow_dir / "artifacts"
            self._require_directory(artifacts_dir)
            artifacts: list[SchedulerArtifact] = []
            expected_files: set[str] = set()
            seen_ids: set[str] = set()
            total_bytes = 0
            for item in artifact_index:
                entry = _closed_mapping(
                    item,
                    _ARTIFACT_INDEX_FIELDS,
                    label="scheduler audit artifact index entry",
                )
                artifact_id = entry["artifactId"]
                digest = entry["contentSha256"]
                byte_count = entry["byteCount"]
                if (
                    not isinstance(artifact_id, str)
                    or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None
                    or artifact_id in seen_ids
                    or not isinstance(digest, str)
                    or _DIGEST_RE.fullmatch(digest) is None
                    or isinstance(byte_count, bool)
                    or not isinstance(byte_count, int)
                    or not 1 <= byte_count <= MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES - total_bytes
                ):
                    raise SchedulerAuditValidationError(
                        "scheduler audit artifact index entry is invalid"
                    )
                filename = f"{artifact_id}.json"
                content_bytes = self._read_file(
                    artifacts_dir / filename,
                    max_bytes=byte_count,
                )
                actual_digest = "sha256:" + hashlib.sha256(content_bytes).hexdigest()
                if len(content_bytes) != byte_count or actual_digest != digest:
                    raise SchedulerAuditValidationError(
                        "scheduler audit artifact digest is inconsistent"
                    )
                try:
                    content = content_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise SchedulerAuditValidationError(
                        "scheduler audit artifact encoding is invalid"
                    ) from exc
                artifact = SchedulerArtifact(artifact_id, content)
                _canonical_artifact_content(artifact)
                artifacts.append(artifact)
                expected_files.add(filename)
                seen_ids.add(artifact_id)
                total_bytes += byte_count
            if total_bytes > MAX_SCHEDULER_ARTIFACT_TOTAL_BYTES:
                raise SchedulerAuditValidationError(
                    "scheduler audit artifact bytes exceed the limit"
                )
            actual_files: set[str] = set()
            for entry in artifacts_dir.iterdir():
                if entry.is_symlink() or not entry.is_file():
                    raise SchedulerAuditValidationError(
                        "scheduler audit artifact directory is unsafe"
                    )
                actual_files.add(entry.name)
            if actual_files != expected_files:
                raise SchedulerAuditValidationError(
                    "scheduler audit artifact index is incomplete"
                )
            audit = project_scheduler_audit(
                profile=profile,
                plan=plan,
                trace=manifest["schedulerTrace"],
                artifacts=tuple(artifacts),
                contract_mode=str(manifest["contractMode"]),
            )
            if manifest["taskId"] != audit.task_id or _manifest(audit) != manifest:
                raise SchedulerAuditValidationError(
                    "scheduler audit manifest is inconsistent"
                )
            if _canonical_document(manifest) != raw_manifest:
                raise SchedulerAuditValidationError(
                    "scheduler audit manifest is not canonical"
                )
            if audit.contract_mode == "v2":
                _validate_available_commit_ready(
                    self.get_commit_ready(workflow_id),
                    audit,
                )
            return audit
        except (
            KeyError,
            TypeError,
            ValueError,
            SchedulerAuditValidationError,
        ) as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit snapshot is invalid"
            ) from exc

    def get(self, workflow_id: str) -> SchedulerAudit | None:
        if (
            not isinstance(workflow_id, str)
            or _WORKFLOW_ID_RE.fullmatch(workflow_id) is None
        ):
            raise SchedulerAuditValidationError("scheduler workflow id is invalid")
        if not self._root.exists() and not self._root.is_symlink():
            return None
        self._require_directory(self._root)
        workflow_dir = self._root / workflow_id
        if not workflow_dir.exists() and not workflow_dir.is_symlink():
            return None
        return self._load(workflow_id, workflow_dir)

    def list(self) -> tuple[SchedulerAuditSummary, ...]:
        if not self._root.exists() and not self._root.is_symlink():
            return ()
        self._require_directory(self._root)
        try:
            entries = sorted(self._root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise SchedulerAuditStorageError(
                "scheduler audit root could not be scanned"
            ) from exc
        if len(entries) > _MAX_WORKFLOWS_PER_RUN:
            raise SchedulerAuditStorageError(
                "scheduler audit workflow limit was exceeded"
            )
        summaries: list[SchedulerAuditSummary] = []
        for entry in entries:
            if (
                _WORKFLOW_ID_RE.fullmatch(entry.name) is None
                or entry.is_symlink()
                or not entry.is_dir()
            ):
                raise SchedulerAuditStorageError(
                    "scheduler audit workflow directory is unsafe"
                )
            audit = self._load(entry.name, entry)
            if audit is None:
                ready = self.get_commit_ready(entry.name)
                if ready is not None:
                    summaries.append(
                        SchedulerAuditSummary(
                            workflow_id=ready.workflow_id,
                            task_id=ready.task_id,
                            status="commit_state_unknown",
                            rule_id=ready.rule_id,
                            committed=None,
                            terminal_reason="final_audit_missing",
                            artifact_count=0,
                        )
                    )
                continue
            summaries.append(
                SchedulerAuditSummary(
                    workflow_id=audit.workflow_id,
                    task_id=audit.task_id,
                    status=str(audit.trace["status"]),
                    rule_id=audit.plan.rule_id,
                    committed=bool(audit.trace["committed"]),
                    terminal_reason=(
                        str(audit.trace["terminalReason"])
                        if audit.trace["terminalReason"] is not None
                        else None
                    ),
                    artifact_count=len(audit.artifacts),
                )
            )
        return tuple(summaries)


__all__ = [
    "SCHEDULER_AUDIT_VERSION",
    "SCHEDULER_COMMIT_READY_VERSION",
    "SchedulerAudit",
    "SchedulerAuditError",
    "SchedulerAuditJournal",
    "SchedulerAuditStorageError",
    "SchedulerAuditSummary",
    "SchedulerAuditValidationError",
    "SchedulerCommitReady",
    "human_approval_analysis_digest",
    "parse_scheduler_commit_ready",
    "project_scheduler_audit",
    "project_scheduler_commit_ready",
    "validate_review_coverage",
]
