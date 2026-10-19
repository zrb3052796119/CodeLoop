"""Strict role and hand-off contracts for centrally scheduled Workers.

Identity, scope, budgets, and result authority are owned by the parent runtime.
Models may fill role-specific payloads, but extra fields and capability
violations fail closed instead of being silently ignored.
"""

from __future__ import annotations

import json
import re
import base64
import binascii
import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import TypeVar

from minicode.scheduler_path_policy import (
    normalized_forbidden_paths_overlap,
    normalized_path_has_windows_ambiguous_component,
    normalized_path_identity,
    normalized_path_within,
)
from minicode.review_probe_source import validate_review_probe_source
from minicode.review_probe_correction import (
    correction_artifact_id, validate_probe_correction_execution,
    validate_probe_correction_sources, validate_pytest_case_report,
)

ASSIGNMENT_VERSION = 1
MAX_ROLE_CONTRACT_BYTES = 18_000
MAX_ANALYST_REPORT_BYTES = 16_000
MAX_WORKFLOW_CRITERIA = 40
MAX_WORKFLOW_CRITERION_CHECK_LINKS = 80
# Leaves room for the 40-check verifier, two bounded Analyst/Reviewer attempts,
# human authority, and all fixed workflow artifacts below the Store's 128-slot
# audit ceiling.  One validated Analyst evidence item becomes one snapshot.
MAX_ANALYST_EVIDENCE_SNAPSHOTS = 64
_ContractValue = TypeVar("_ContractValue")

_ASSIGNMENT_FIELDS = frozenset(
    {
        "assignmentVersion",
        "workflowId",
        "taskId",
        "assignmentId",
        "role",
        "mode",
        "objective",
        "scope",
        "constraints",
        "acceptanceCriteria",
        "inputRefs",
        "risk",
        "budget",
        "expectedResultType",
    }
)
_SCOPE_FIELDS = frozenset({"readRoots", "writePaths", "forbiddenPaths"})
_CONSTRAINT_FIELDS = frozenset({"id", "kind", "text"})
_CRITERION_FIELDS = frozenset({"id", "kind", "text"})
_BUDGET_FIELDS = frozenset(
    {"deadlineAt", "maxModelCalls", "maxInputTokens", "maxOutputTokens"}
)
_ID_RE = re.compile(r"^(?:wf|task|asg)_[A-Za-z0-9][A-Za-z0-9_.-]{3,127}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_CRITERION_KINDS = frozenset({"fact", "behavior", "quality", "safety"})
_MAX_TEXT = 4_000
_MAX_ITEMS = 100
_MAX_ASSIGNMENT_INPUT_REFS = 128
_ANALYST_BASE_FIELDS = frozenset(
    {
        "resultVersion",
        "assignmentId",
        "status",
        "findings",
        "unknowns",
        "risks",
        "ballot",
    }
)
_ANALYST_STANDARD_FIELDS = _ANALYST_BASE_FIELDS | {
    "implementationOrderCandidate"
}
_FINDING_FIELDS = frozenset(
    {"claimId", "statement", "evidence", "confidence"}
)
_EVIDENCE_FIELDS = frozenset({"path", "lineStart", "lineEnd"})
_RISK_FIELDS = frozenset({"severity", "statement", "evidenceRefs"})
_ORDER_CANDIDATE_FIELDS = frozenset(
    {"required", "steps", "verificationRequests"}
)
_ORDER_STEP_FIELDS = frozenset({"id", "action", "allowedPaths"})
_BALLOT_FIELDS = frozenset(
    {"questionId", "choice", "evidenceRefs", "confidence"}
)
_CONFIDENCE = frozenset({"high", "medium", "low"})
_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERIFICATION_FIELDS = frozenset(
    {
        "verificationVersion",
        "verificationId",
        "workflowId",
        "taskId",
        "executionAssignmentId",
        "patchDigest",
        "status",
        "checks",
    }
)
_VERIFICATION_CHECK_FIELDS = frozenset(
    {
        "checkId",
        "kind",
        "status",
        "exitCode",
        "durationMs",
        "artifactRef",
    }
)
_VERIFICATION_KINDS = frozenset(
    {"tests", "build", "lint", "typecheck", "oracle"}
)
_REVIEW_FIELDS = frozenset(
    {
        "resultVersion",
        "assignmentId",
        "patchDigest",
        "verdict",
        "criteria",
        "blockingFindings",
        "warnings",
        "requestedChecks",
        "confidence",
    }
)
REVIEW_COVERAGE_CONSTRAINT_ID = "c_scheduler_review_coverage_v1"
REVIEW_COVERAGE_CONSTRAINT_TEXT = "Approval requires host-bound independent review coverage."
_REVIEW_CRITERION_FIELDS = frozenset(
    {"criterionId", "status", "evidenceRefs"}
)
_REVIEW_FINDING_FIELDS = frozenset(
    {"id", "severity", "statement", "evidenceRefs"}
)
_CONFLICT_FIELDS = frozenset(
    {
        "conflictVersion",
        "conflictId",
        "workflowId",
        "taskId",
        "kind",
        "risk",
        "question",
        "claims",
        "evidenceDigest",
        "voteEligible",
    }
)
_CONFLICT_QUESTION_FIELDS = frozenset(
    {"questionId", "prompt", "choices"}
)
_CONFLICT_CLAIM_FIELDS = frozenset(
    {"claimId", "choice", "statement", "evidenceRefs"}
)
_CONFLICT_KINDS = frozenset(
    {"fact", "plan", "review", "risk", "requirement", "verification"}
)
_ADJUDICATION_FIELDS = frozenset(
    {
        "resultVersion",
        "assignmentId",
        "conflictId",
        "decision",
        "resolvedClaims",
        "conditions",
        "confidence",
    }
)
_RESOLVED_CLAIM_FIELDS = frozenset(
    {"claimId", "resolution", "evidenceRefs"}
)
_ORDER_FIELDS = frozenset(
    {
        "orderVersion",
        "orderId",
        "assignmentId",
        "baseDigest",
        "allowedPaths",
        "steps",
        "criterionIds",
        "verificationRequests",
    }
)
_IMPLEMENTATION_STEP_FIELDS = frozenset(
    {"id", "action", "allowedPaths", "criterionIds"}
)
_EXECUTION_FIELDS = frozenset(
    {
        "resultVersion",
        "assignmentId",
        "orderId",
        "status",
        "changeSet",
        "stepResults",
        "deviations",
        "selfChecks",
        "blockers",
    }
)
_EXECUTOR_DECLARATION_FIELDS = _EXECUTION_FIELDS - {"changeSet"}
_CHANGE_SET_FIELDS = frozenset(
    {"workspaceRef", "baseDigest", "patchDigest", "files"}
)
_CHANGED_FILE_FIELDS = frozenset({"path", "action"})
_STEP_RESULT_FIELDS = frozenset(
    {"stepId", "status", "artifactRefs"}
)
_DEVIATION_FIELDS = frozenset({"stepId", "reason", "impact"})
_WORKFLOW_REQUEST_FIELDS = frozenset(
    {"requestVersion", "profile", "scope", "criteria", "verificationChecks"}
)
_WORKFLOW_PROFILE_FIELDS = frozenset(
    {"taskId", "kind", "risk", "complex", "parentHasSufficientContext"}
)
_WORKFLOW_CHECK_FIELDS = frozenset(
    {"checkId", "kind", "argv", "criterionIds"}
)
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_SENSITIVE_PATH_TERMS = frozenset(
    {
        "auth",
        "authorization",
        "billing",
        "concurrency",
        "config",
        "credential",
        "database",
        "deploy",
        "infra",
        "lock",
        "migration",
        "payment",
        "permission",
        "schema",
        "secret",
        "security",
        "settings",
        "workflow",
    }
)
_LOW_RISK_CHANGE_SUFFIXES = frozenset({".md", ".rst", ".txt"})


class ContractValidationError(ValueError):
    """A versioned Assignment or role result violated its closed contract."""


class WorkerRole(str, Enum):
    ANALYST = "analyst"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    ADJUDICATOR = "adjudicator"


class AssignmentRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_ROLE_MODES = {
    WorkerRole.ANALYST: frozenset({"investigate", "prepare_change", "assess_claim"}),
    WorkerRole.EXECUTOR: frozenset({"implement"}),
    WorkerRole.REVIEWER: frozenset({"change_review", "risk_review"}),
    WorkerRole.ADJUDICATOR: frozenset({"resolve_conflict"}),
}
_ROLE_RESULT_TYPES = {
    WorkerRole.ANALYST: "AnalystReport.v1",
    WorkerRole.EXECUTOR: "ExecutionResult.v1",
    WorkerRole.REVIEWER: "ReviewVerdict.v1",
    WorkerRole.ADJUDICATOR: "AdjudicationVerdict.v1",
}
def _closed_mapping(
    value: object,
    fields: frozenset[str],
    *,
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ContractValidationError(f"{label} fields do not match the contract")
    return value


def _bounded_contract_document(
    value: _ContractValue,
    *,
    label: str,
    maximum_bytes: int = MAX_ROLE_CONTRACT_BYTES,
) -> _ContractValue:
    """Keep every normalized hand-off below the Store's transport ceiling."""
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"{label} is not canonical JSON") from exc
    if len(payload) > maximum_bytes:
        raise ContractValidationError(
            f"{label} exceeds the {maximum_bytes}-byte contract limit"
        )
    return value


def _text(value: object, *, label: str, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ContractValidationError(f"{label} is empty or oversized")
    return normalized


def _identifier(value: object, *, label: str, prefix: str) -> str:
    normalized = _text(value, label=label, maximum=132)
    if _ID_RE.fullmatch(normalized) is None or not normalized.startswith(f"{prefix}_"):
        raise ContractValidationError(f"{label} is invalid")
    return normalized


def _string_list(
    value: object,
    *,
    label: str,
    maximum_items: int = _MAX_ITEMS,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ContractValidationError(f"{label} must be a bounded list")
    normalized: list[str] = []
    for index, item in enumerate(value):
        text = _text(item, label=f"{label}[{index}]", maximum=500)
        if text in normalized:
            raise ContractValidationError(f"{label} contains a duplicate")
        normalized.append(text)
    return tuple(normalized)


def _generic_id(value: object, *, label: str) -> str:
    normalized = _text(value, label=label, maximum=128)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,127}", normalized) is None:
        raise ContractValidationError(f"{label} is invalid")
    return normalized


def _prefixed_id(value: object, *, label: str, prefix: str) -> str:
    normalized = _generic_id(value, label=label)
    if not normalized.startswith(prefix + "_"):
        raise ContractValidationError(f"{label} is invalid")
    return normalized


def _digest(value: object, *, label: str) -> str:
    normalized = _text(value, label=label, maximum=80)
    if _DIGEST_RE.fullmatch(normalized) is None:
        raise ContractValidationError(f"{label} must be a sha256 digest")
    return normalized


def _non_negative_int(
    value: object,
    *,
    label: str,
    allow_none: bool = False,
) -> int | None:
    if allow_none and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"{label} must be a non-negative integer")
    return value


def _normalized_path(value: object, *, label: str) -> str:
    if isinstance(value, str):
        portable_path = value.replace("\\", "/")
        if (
            portable_path
            and len(value.strip()) <= 500
            and normalized_path_has_windows_ambiguous_component(portable_path)
        ):
            raise ContractValidationError(
                f"{label} contains a Windows-ambiguous path component"
            )
    path = _text(value, label=label, maximum=500).replace("\\", "/")
    parts = [part for part in path.split("/") if part not in {"", "."}]
    if ".." in parts:
        raise ContractValidationError(f"{label} cannot contain parent traversal")
    prefix = "/" if path.startswith("/") else ""
    normalized = prefix + "/".join(parts)
    if not normalized:
        return "."
    return normalized


def _normalized_path_list(
    value: object,
    *,
    label: str,
    maximum_items: int = _MAX_ITEMS,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ContractValidationError(f"{label} must be a bounded list")
    normalized: list[str] = []
    for index, item in enumerate(value):
        path = _normalized_path(item, label=f"{label}[{index}]")
        if path in normalized:
            raise ContractValidationError(f"{label} contains a duplicate")
        normalized.append(path)
    return tuple(normalized)


def _path_within(path: str, roots: tuple[str, ...]) -> bool:
    candidate = _normalized_path(path, label="path")
    for raw_root in roots:
        root = _normalized_path(raw_root, label="scope root")
        if normalized_path_within(candidate, root, case_sensitive=True):
            return True
    return False


def _paths_overlap(left: str, right: str) -> bool:
    normalized_left = _normalized_path(left, label="path")
    normalized_right = _normalized_path(right, label="path")
    return normalized_forbidden_paths_overlap(
        normalized_left,
        normalized_right,
    )


@dataclass(frozen=True, slots=True)
class AssignmentScope:
    read_roots: tuple[str, ...]
    write_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "readRoots": list(self.read_roots),
            "writePaths": list(self.write_paths),
            "forbiddenPaths": list(self.forbidden_paths),
        }


@dataclass(frozen=True, slots=True)
class AssignmentConstraint:
    constraint_id: str
    kind: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.constraint_id, "kind": self.kind, "text": self.text}


@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    criterion_id: str
    kind: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.criterion_id, "kind": self.kind, "text": self.text}


@dataclass(frozen=True, slots=True)
class AssignmentBudget:
    deadline_at: str | None
    max_model_calls: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "deadlineAt": self.deadline_at,
            "maxModelCalls": self.max_model_calls,
            "maxInputTokens": self.max_input_tokens,
            "maxOutputTokens": self.max_output_tokens,
        }


@dataclass(frozen=True, slots=True)
class ImplementationStep:
    step_id: str
    action: str
    allowed_paths: tuple[str, ...]
    criterion_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.step_id,
            "action": self.action,
            "allowedPaths": list(self.allowed_paths),
            "criterionIds": list(self.criterion_ids),
        }


@dataclass(frozen=True, slots=True)
class ImplementationOrder:
    order_id: str
    assignment_id: str
    base_digest: str
    allowed_paths: tuple[str, ...]
    steps: tuple[ImplementationStep, ...]
    criterion_ids: tuple[str, ...]
    verification_requests: tuple[str, ...]
    order_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "orderVersion": self.order_version,
            "orderId": self.order_id,
            "assignmentId": self.assignment_id,
            "baseDigest": self.base_digest,
            "allowedPaths": list(self.allowed_paths),
            "steps": [step.to_dict() for step in self.steps],
            "criterionIds": list(self.criterion_ids),
            "verificationRequests": list(self.verification_requests),
        }


@dataclass(frozen=True, slots=True)
class WorkflowVerificationCheck:
    check_id: str
    kind: str
    argv: tuple[str, ...]
    criterion_ids: tuple[str, ...]

    def to_execution_check(self):
        """Use the same host execution policy in live checks and audit replay."""
        from minicode.deterministic_verifier import VerificationCheck

        return VerificationCheck(self.check_id, self.kind, self.argv, timeout_seconds=120)

    def to_dict(self) -> dict[str, object]:
        return {
            "checkId": self.check_id,
            "kind": self.kind,
            "argv": list(self.argv),
            "criterionIds": list(self.criterion_ids),
        }


@dataclass(frozen=True, slots=True)
class WorkflowRequest:
    task_id: str
    kind: str
    risk: str
    complex: bool
    parent_has_sufficient_context: bool
    scope: AssignmentScope
    criteria: tuple[AcceptanceCriterion, ...]
    verification_checks: tuple[WorkflowVerificationCheck, ...]
    structured: bool
    request_version: int = 1

    @property
    def task_profile(self):
        from minicode.central_scheduler import SCHEDULER_POLICY_VERSION

        return self.task_profile_for_policy(SCHEDULER_POLICY_VERSION)

    def task_profile_for_policy(self, policy_version: int):
        """Reproduce the recorded policy's derived facts for sealed audit replay."""
        from minicode.central_scheduler import (
            SUPPORTED_SCHEDULER_POLICY_VERSIONS,
            TaskKind,
            TaskProfile,
            TaskRisk,
        )

        if (
            type(policy_version) is not int
            or policy_version not in SUPPORTED_SCHEDULER_POLICY_VERSIONS
        ):
            raise ValueError("scheduler policy version is unsupported")
        covered_criteria = {
            criterion_id
            for check in self.verification_checks
            for criterion_id in check.criterion_ids
        }
        expected_criteria = {item.criterion_id for item in self.criteria}
        scope_clear = self.kind == "change" and bool(self.scope.write_paths)
        declared_coverage_complete = bool(self.verification_checks) and (
            covered_criteria == expected_criteria and bool(expected_criteria)
        )
        inferred_risk = "low"
        inferred_complex = False
        if self.kind == "change":
            lowered_paths = tuple(path.casefold() for path in self.scope.write_paths)
            path_terms = {
                term
                for path in lowered_paths
                for term in re.split(r"[^a-z0-9]+", path)
                if term
            }
            broad_scope = any(path in {".", "/"} for path in lowered_paths)
            sensitive = bool(path_terms & _SENSITIVE_PATH_TERMS) or any(
                criterion.kind == "safety" for criterion in self.criteria
            )
            # A narrow, non-sensitive scope with at most two executable
            # checks has bounded authority even when implementation is
            # complex. Broader scopes or more checks keep the full reviewed
            # chain; complexity alone does not require extra role handoffs.
            narrow_scope = (
                not broad_scope
                and len(self.scope.write_paths) <= 2
                and len(self.verification_checks) <= 2
                and not sensitive
            )
            inferred_complex = (
                broad_scope
                or len(self.scope.write_paths) > 3
                or len(self.verification_checks) > 2
            )
            if broad_scope or sensitive:
                inferred_risk = "high"
            elif narrow_scope:
                # Bounded authority can skip preliminary analysis. Semantic
                # completion review is a separate gate from risk assessment.
                inferred_risk = "low"
            elif self.scope.write_paths:
                inferred_risk = "medium"
            else:
                inferred_risk = "high"
        else:
            narrow_scope = False
        effective_risk = max(
            (self.risk, inferred_risk),
            key=_RISK_ORDER.__getitem__,
        )
        profile = TaskProfile(
            task_id=self.task_id,
            kind=TaskKind(self.kind),
            risk=TaskRisk(effective_risk),
            complex=self.complex or inferred_complex,
            # Public Worker input cannot certify the Parent's private context.
            # Parent-only work is selected before invoking the task tool.
            parent_has_sufficient_context=False,
            scope_clear=scope_clear,
            # This certifies executable associations only. Public model input
            # cannot prove semantic check/criterion equivalence. Policy v4
            # keeps a completion review even on the direct scoped route;
            # historical policies replay their original routing below.
            acceptance_executable=(
                declared_coverage_complete
                and (
                    not self.structured
                    or (
                        narrow_scope
                        and effective_risk == "low"
                        and scope_clear
                        and (
                            policy_version != 2
                            or not (self.complex or inferred_complex)
                        )
                    )
                )
            ),
            human_confirmation_required=(
                self.structured
                and effective_risk in {"high", "critical"}
                and (self.kind == "change" or effective_risk == "critical")
            ),
        )
        # Public criterion/check declarations never waive independent review.
        # The runtime may narrow v5 routing using original host user evidence.
        return profile

    def to_dict(self) -> dict[str, object]:
        return {
            "requestVersion": self.request_version,
            "profile": {
                "taskId": self.task_id,
                "kind": self.kind,
                "risk": self.risk,
                "complex": self.complex,
                "parentHasSufficientContext": self.parent_has_sufficient_context,
            },
            "scope": self.scope.to_dict(),
            "criteria": [item.to_dict() for item in self.criteria],
            "verificationChecks": [
                item.to_dict() for item in self.verification_checks
            ],
        }


@dataclass(frozen=True, slots=True)
class Assignment:
    workflow_id: str
    task_id: str
    assignment_id: str
    role: WorkerRole
    mode: str
    objective: str
    scope: AssignmentScope
    constraints: tuple[AssignmentConstraint, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    input_refs: tuple[str, ...]
    risk: AssignmentRisk
    budget: AssignmentBudget
    expected_result_type: str
    assignment_version: int = ASSIGNMENT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "assignmentVersion": self.assignment_version,
            "workflowId": self.workflow_id,
            "taskId": self.task_id,
            "assignmentId": self.assignment_id,
            "role": self.role.value,
            "mode": self.mode,
            "objective": self.objective,
            "scope": self.scope.to_dict(),
            "constraints": [item.to_dict() for item in self.constraints],
            "acceptanceCriteria": [
                item.to_dict() for item in self.acceptance_criteria
            ],
            "inputRefs": list(self.input_refs),
            "risk": self.risk.value,
            "budget": self.budget.to_dict(),
            "expectedResultType": self.expected_result_type,
        }


def _parse_scope(value: object, *, role: WorkerRole) -> AssignmentScope:
    raw = _closed_mapping(value, _SCOPE_FIELDS, label="scope")
    read_roots = _normalized_path_list(
        raw["readRoots"],
        label="scope.readRoots",
    )
    write_paths = _normalized_path_list(
        raw["writePaths"],
        label="scope.writePaths",
    )
    forbidden_paths = _normalized_path_list(
        raw["forbiddenPaths"],
        label="scope.forbiddenPaths",
    )
    if (
        len(set(read_roots)) != len(read_roots)
        or len(set(write_paths)) != len(write_paths)
        or len(
            {
                normalized_path_identity(path, case_sensitive=False)
                for path in forbidden_paths
            }
        )
        != len(forbidden_paths)
    ):
        raise ContractValidationError("scope contains normalized duplicate paths")
    scope = AssignmentScope(
        read_roots=read_roots,
        write_paths=write_paths,
        forbidden_paths=forbidden_paths,
    )
    del role  # Capabilities, not Assignment target scope, enforce read-only access.
    if any(
        _paths_overlap(write_path, forbidden_path)
        for write_path in scope.write_paths
        for forbidden_path in scope.forbidden_paths
    ):
        raise ContractValidationError("write scope intersects forbidden paths")
    return scope


def _parse_constraints(value: object) -> tuple[AssignmentConstraint, ...]:
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise ContractValidationError("constraints must be a bounded list")
    results: list[AssignmentConstraint] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        raw = _closed_mapping(item, _CONSTRAINT_FIELDS, label=f"constraint[{index}]")
        constraint_id = _text(raw["id"], label="constraint.id", maximum=80)
        if constraint_id in seen:
            raise ContractValidationError("constraint id is duplicated")
        seen.add(constraint_id)
        results.append(
            AssignmentConstraint(
                constraint_id=constraint_id,
                kind=_text(raw["kind"], label="constraint.kind", maximum=80),
                text=_text(raw["text"], label="constraint.text"),
            )
        )
    return tuple(results)


def _parse_criteria(value: object) -> tuple[AcceptanceCriterion, ...]:
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise ContractValidationError("acceptanceCriteria must be a bounded list")
    results: list[AcceptanceCriterion] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        raw = _closed_mapping(item, _CRITERION_FIELDS, label=f"criterion[{index}]")
        criterion_id = _text(raw["id"], label="criterion.id", maximum=80)
        kind = _text(raw["kind"], label="criterion.kind", maximum=20)
        if criterion_id in seen:
            raise ContractValidationError("criterion id is duplicated")
        if kind not in _CRITERION_KINDS:
            raise ContractValidationError("criterion kind is invalid")
        seen.add(criterion_id)
        results.append(
            AcceptanceCriterion(
                criterion_id=criterion_id,
                kind=kind,
                text=_text(raw["text"], label="criterion.text"),
            )
        )
    return tuple(results)


def _parse_budget(value: object) -> AssignmentBudget:
    raw = _closed_mapping(value, _BUDGET_FIELDS, label="budget")
    # Null is explicit absence of a host limit, never a missing field or a
    # sentinel numeric grant. Present limits keep the existing strict contract.
    deadline = None
    if raw["deadlineAt"] is not None:
        deadline = _text(raw["deadlineAt"], label="budget.deadlineAt", maximum=80)
        if _RFC3339_RE.fullmatch(deadline) is None:
            raise ContractValidationError(
                "budget.deadlineAt must use timezone-aware strict RFC3339 syntax"
            )
        try:
            parsed_deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractValidationError("budget.deadlineAt is invalid") from exc
        if "T" not in deadline or parsed_deadline.tzinfo is None:
            raise ContractValidationError("budget.deadlineAt must be timezone-aware")
    counts: list[int | None] = []
    for field in ("maxModelCalls", "maxInputTokens", "maxOutputTokens"):
        item = raw[field]
        if item is not None and (isinstance(item, bool) or not isinstance(item, int) or item < 0):
            raise ContractValidationError(f"budget.{field} must be non-negative")
        counts.append(item)
    return AssignmentBudget(deadline, *counts)


def parse_assignment(value: object) -> Assignment:
    """Validate and normalize an immutable Worker Assignment."""

    raw = _closed_mapping(value, _ASSIGNMENT_FIELDS, label="assignment")
    if (
        isinstance(raw["assignmentVersion"], bool)
        or raw["assignmentVersion"] != ASSIGNMENT_VERSION
    ):
        raise ContractValidationError("assignment version is unsupported")
    try:
        role = WorkerRole(str(raw["role"]))
    except ValueError as exc:
        raise ContractValidationError("assignment role is invalid") from exc
    mode = _text(raw["mode"], label="assignment.mode", maximum=80)
    if mode not in _ROLE_MODES[role]:
        raise ContractValidationError("assignment mode is invalid for role")
    expected_result_type = _text(
        raw["expectedResultType"],
        label="assignment.expectedResultType",
        maximum=80,
    )
    if expected_result_type != _ROLE_RESULT_TYPES[role]:
        raise ContractValidationError("expected result type is invalid for role")
    try:
        risk = AssignmentRisk(str(raw["risk"]))
    except ValueError as exc:
        raise ContractValidationError("assignment risk is invalid") from exc

    assignment = Assignment(
        workflow_id=_identifier(raw["workflowId"], label="workflowId", prefix="wf"),
        task_id=_identifier(raw["taskId"], label="taskId", prefix="task"),
        assignment_id=_identifier(
            raw["assignmentId"], label="assignmentId", prefix="asg"
        ),
        role=role,
        mode=mode,
        objective=_text(raw["objective"], label="assignment.objective"),
        scope=_parse_scope(raw["scope"], role=role),
        constraints=_parse_constraints(raw["constraints"]),
        acceptance_criteria=_parse_criteria(raw["acceptanceCriteria"]),
        input_refs=_string_list(
            raw["inputRefs"],
            label="inputRefs",
            maximum_items=_MAX_ASSIGNMENT_INPUT_REFS,
        ),
        risk=risk,
        budget=_parse_budget(raw["budget"]),
        expected_result_type=expected_result_type,
    )
    _bounded_contract_document(assignment.to_dict(), label="Assignment")
    return assignment


def parse_workflow_request(
    value: object | None,
    *,
    legacy_task_id: str,
) -> WorkflowRequest:
    """Normalize optional task-tool facts without trusting derived route flags."""

    fallback_task_id = _identifier(
        legacy_task_id,
        label="legacy taskId",
        prefix="task",
    )
    if value is None:
        request = WorkflowRequest(
            task_id=fallback_task_id,
            kind="change",
            risk="high",
            complex=True,
            parent_has_sufficient_context=False,
            scope=AssignmentScope(
                read_roots=(".",),
                write_paths=(),
                forbidden_paths=(".git", ".env"),
            ),
            criteria=(),
            verification_checks=(),
            structured=False,
        )
        _bounded_contract_document(request.to_dict(), label="WorkflowRequest")
        return request

    raw = _closed_mapping(
        value,
        _WORKFLOW_REQUEST_FIELDS,
        label="WorkflowRequest",
    )
    if isinstance(raw["requestVersion"], bool) or raw["requestVersion"] != 1:
        raise ContractValidationError("WorkflowRequest version is unsupported")
    profile = _closed_mapping(
        raw["profile"],
        _WORKFLOW_PROFILE_FIELDS,
        label="WorkflowRequest profile",
    )
    task_id = _identifier(profile["taskId"], label="profile.taskId", prefix="task")
    kind = profile["kind"]
    risk = profile["risk"]
    if kind not in {"read_only", "change"}:
        raise ContractValidationError("WorkflowRequest kind is invalid")
    if risk not in {item.value for item in AssignmentRisk}:
        raise ContractValidationError("WorkflowRequest risk is invalid")
    complex_flag = profile["complex"]
    context_flag = profile["parentHasSufficientContext"]
    if type(complex_flag) is not bool or type(context_flag) is not bool:
        raise ContractValidationError(
            "WorkflowRequest routing flags must be booleans"
        )
    scope = _parse_scope(raw["scope"], role=WorkerRole.EXECUTOR)
    from minicode.scheduler_permissions import (
        MANDATORY_SCHEDULER_FORBIDDEN_PATHS,
        is_mandatory_scheduler_forbidden_path,
        mandatory_scheduler_forbidden_paths,
        scheduler_paths_overlap,
    )

    if any(
        is_mandatory_scheduler_forbidden_path(path)
        for path in scope.read_roots
        if path != "."
    ):
        raise ContractValidationError(
            "WorkflowRequest readRoots enter scheduler-forbidden paths"
        )
    if any(
        is_mandatory_scheduler_forbidden_path(path)
        or any(
            scheduler_paths_overlap(path, forbidden)
            for forbidden in MANDATORY_SCHEDULER_FORBIDDEN_PATHS
        )
        for path in scope.write_paths
    ):
        raise ContractValidationError(
            "WorkflowRequest writePaths enter scheduler-forbidden paths"
        )
    scope = AssignmentScope(
        read_roots=scope.read_roots,
        write_paths=scope.write_paths,
        forbidden_paths=mandatory_scheduler_forbidden_paths(
            scope.forbidden_paths
        ),
    )
    if not scope.read_roots:
        raise ContractValidationError("WorkflowRequest readRoots must be non-empty")
    if kind == "read_only" and scope.write_paths:
        raise ContractValidationError(
            "read-only WorkflowRequest cannot declare write paths"
        )
    if kind == "change" and not scope.write_paths:
        raise ContractValidationError(
            "change WorkflowRequest must declare bounded write paths"
        )
    criteria = _parse_criteria(raw["criteria"])
    if not criteria:
        raise ContractValidationError(
            "WorkflowRequest acceptance criteria must be non-empty"
        )
    if len(criteria) > MAX_WORKFLOW_CRITERIA:
        raise ContractValidationError(
            "WorkflowRequest acceptance criteria exceed the derived-artifact budget"
        )
    criterion_ids = {item.criterion_id for item in criteria}

    checks_raw = raw["verificationChecks"]
    if not isinstance(checks_raw, list) or len(checks_raw) > 40:
        raise ContractValidationError(
            "WorkflowRequest verificationChecks must be bounded"
        )
    from minicode.verification_observation import project_command_verification
    from minicode.verification_policy import verification_argv_is_safe

    checks: list[WorkflowVerificationCheck] = []
    check_ids: set[str] = set()
    check_argvs: set[tuple[str, ...]] = set()
    for index, item in enumerate(checks_raw):
        check = _closed_mapping(
            item,
            _WORKFLOW_CHECK_FIELDS,
            label=f"WorkflowRequest verificationCheck[{index}]",
        )
        check_id = _generic_id(
            check["checkId"],
            label="WorkflowRequest verificationCheck.checkId",
        )
        if check_id in check_ids:
            raise ContractValidationError(
                "WorkflowRequest verification check id is duplicated"
            )
        check_kind = check["kind"]
        argv_raw = check["argv"]
        if (
            check_kind not in {"tests", "build", "lint", "typecheck"}
            or not isinstance(argv_raw, list)
            or not verification_argv_is_safe(argv_raw)
        ):
            raise ContractValidationError(
                "WorkflowRequest verification argv or kind is invalid"
            )
        projected = project_command_verification(
            {"command": argv_raw[0], "args": argv_raw[1:]},
            passed=True,
        )
        if projected is None or projected.get("kind") != check_kind:
            raise ContractValidationError(
                "WorkflowRequest verification command is not policy-recognized"
            )
        normalized_argv = tuple(str(item) for item in argv_raw)
        if normalized_argv in check_argvs:
            raise ContractValidationError(
                "WorkflowRequest verification argv is duplicated"
            )
        covered = _string_list(
            check["criterionIds"],
            label="WorkflowRequest verificationCheck.criterionIds",
        )
        if not covered or any(item not in criterion_ids for item in covered):
            raise ContractValidationError(
                "WorkflowRequest verification check has invalid criterionIds"
            )
        check_ids.add(check_id)
        check_argvs.add(normalized_argv)
        checks.append(
            WorkflowVerificationCheck(
                check_id=check_id,
                kind=str(check_kind),
                argv=normalized_argv,
                criterion_ids=covered,
            )
        )
    covered_criteria = {
        criterion_id
        for check in checks
        for criterion_id in check.criterion_ids
    }
    if (
        sum(len(check.criterion_ids) for check in checks)
        > MAX_WORKFLOW_CRITERION_CHECK_LINKS
    ):
        raise ContractValidationError(
            "WorkflowRequest criterion-check links exceed the derived-artifact budget"
        )
    if kind == "change" and (
        not checks or covered_criteria != criterion_ids
    ):
        raise ContractValidationError(
            "change WorkflowRequest requires executable coverage for every "
            "acceptance criterion"
        )
    request = WorkflowRequest(
        task_id=task_id,
        kind=str(kind),
        risk=str(risk),
        complex=complex_flag,
        parent_has_sufficient_context=context_flag,
        scope=scope,
        criteria=criteria,
        verification_checks=tuple(checks),
        structured=True,
    )
    _bounded_contract_document(request.to_dict(), label="WorkflowRequest")
    return request


def parse_implementation_order(
    assignment: Assignment,
    value: object,
) -> ImplementationOrder:
    """Lock an Executor to one baseline, path set, and criterion-complete plan."""

    if not isinstance(assignment, Assignment):
        raise TypeError("assignment must be an Assignment")
    if assignment.role is not WorkerRole.EXECUTOR:
        raise ContractValidationError(
            "ImplementationOrder requires an Executor assignment"
        )
    raw = _closed_mapping(value, _ORDER_FIELDS, label="ImplementationOrder")
    if isinstance(raw["orderVersion"], bool) or raw["orderVersion"] != 1:
        raise ContractValidationError("ImplementationOrder version is unsupported")
    if raw["assignmentId"] != assignment.assignment_id:
        raise ContractValidationError(
            "ImplementationOrder assignmentId does not match"
        )
    order_id = _prefixed_id(
        raw["orderId"],
        label="ImplementationOrder orderId",
        prefix="order",
    )
    base_digest = _digest(
        raw["baseDigest"],
        label="ImplementationOrder baseDigest",
    )
    allowed_paths = _normalized_path_list(
        raw["allowedPaths"],
        label="ImplementationOrder allowedPaths",
    )
    if not allowed_paths:
        raise ContractValidationError(
            "ImplementationOrder allowedPaths must be non-empty"
        )
    if any(
        not _path_within(path, assignment.scope.write_paths)
        for path in allowed_paths
    ):
        raise ContractValidationError(
            "ImplementationOrder exceeds Assignment write scope"
        )
    if any(
        _paths_overlap(path, forbidden)
        for path in allowed_paths
        for forbidden in assignment.scope.forbidden_paths
    ):
        raise ContractValidationError(
            "ImplementationOrder intersects forbidden paths"
        )

    criterion_ids = _string_list(
        raw["criterionIds"],
        label="ImplementationOrder criterionIds",
    )
    expected_criteria = {
        item.criterion_id for item in assignment.acceptance_criteria
    }
    if set(criterion_ids) != expected_criteria:
        raise ContractValidationError(
            "ImplementationOrder must bind all Assignment criteria"
        )

    steps_raw = raw["steps"]
    if (
        not isinstance(steps_raw, list)
        or not steps_raw
        or len(steps_raw) > 40
    ):
        raise ContractValidationError("ImplementationOrder steps must be bounded")
    steps: list[ImplementationStep] = []
    step_ids: set[str] = set()
    covered_paths: set[str] = set()
    covered_criteria: set[str] = set()
    for index, item in enumerate(steps_raw):
        step = _closed_mapping(
            item,
            _IMPLEMENTATION_STEP_FIELDS,
            label=f"ImplementationOrder step[{index}]",
        )
        step_id = _generic_id(step["id"], label="ImplementationOrder step.id")
        if step_id in step_ids:
            raise ContractValidationError("ImplementationOrder step id is duplicated")
        step_paths = _normalized_path_list(
            step["allowedPaths"],
            label="ImplementationOrder step.allowedPaths",
        )
        if not step_paths or any(path not in allowed_paths for path in step_paths):
            raise ContractValidationError(
                "ImplementationOrder step exceeds locked allowed paths"
            )
        step_criteria = _string_list(
            step["criterionIds"],
            label="ImplementationOrder step.criterionIds",
        )
        if not step_criteria or any(
            criterion not in criterion_ids for criterion in step_criteria
        ):
            raise ContractValidationError(
                "ImplementationOrder step has invalid criteria"
            )
        step_ids.add(step_id)
        covered_paths.update(step_paths)
        covered_criteria.update(step_criteria)
        steps.append(
            ImplementationStep(
                step_id=step_id,
                action=_text(
                    step["action"],
                    label="ImplementationOrder step.action",
                ),
                allowed_paths=step_paths,
                criterion_ids=step_criteria,
            )
        )
    if covered_paths != set(allowed_paths):
        raise ContractValidationError(
            "ImplementationOrder steps do not account for allowed paths"
        )
    if covered_criteria != set(criterion_ids):
        raise ContractValidationError(
            "ImplementationOrder steps do not account for criteria"
        )
    verification_requests = _string_list(
        raw["verificationRequests"],
        label="ImplementationOrder verificationRequests",
    )
    if not verification_requests:
        raise ContractValidationError(
            "ImplementationOrder requires deterministic verification requests"
        )
    order = ImplementationOrder(
        order_id=order_id,
        assignment_id=assignment.assignment_id,
        base_digest=base_digest,
        allowed_paths=allowed_paths,
        steps=tuple(steps),
        criterion_ids=criterion_ids,
        verification_requests=verification_requests,
    )
    _bounded_contract_document(order.to_dict(), label="ImplementationOrder")
    return order


def _parse_analyst_result(
    assignment: Assignment,
    value: object,
) -> dict[str, object]:
    expected_fields = (
        _ANALYST_BASE_FIELDS
        if assignment.mode == "assess_claim"
        else _ANALYST_STANDARD_FIELDS
    )
    raw = _closed_mapping(value, expected_fields, label="AnalystReport")
    if isinstance(raw["resultVersion"], bool) or raw["resultVersion"] != 1:
        raise ContractValidationError("AnalystReport version is unsupported")
    if raw["assignmentId"] != assignment.assignment_id:
        raise ContractValidationError("AnalystReport assignmentId does not match")
    status = raw["status"]
    if status not in {"completed", "inconclusive", "failed"}:
        raise ContractValidationError("AnalystReport status is invalid")

    findings_raw = raw["findings"]
    if not isinstance(findings_raw, list) or len(findings_raw) > _MAX_ITEMS:
        raise ContractValidationError("AnalystReport findings must be bounded")
    findings: list[dict[str, object]] = []
    claim_ids: set[str] = set()
    evidence_snapshot_count = 0
    for index, item in enumerate(findings_raw):
        finding = _closed_mapping(
            item, _FINDING_FIELDS, label=f"AnalystReport finding[{index}]"
        )
        claim_id = _generic_id(finding["claimId"], label="finding.claimId")
        if claim_id in claim_ids:
            raise ContractValidationError("finding claimId is duplicated")
        claim_ids.add(claim_id)
        evidence_raw = finding["evidence"]
        if not isinstance(evidence_raw, list) or not evidence_raw or len(evidence_raw) > 20:
            raise ContractValidationError("finding evidence is required and bounded")
        evidence_snapshot_count += len(evidence_raw)
        if evidence_snapshot_count > MAX_ANALYST_EVIDENCE_SNAPSHOTS:
            raise ContractValidationError(
                "AnalystReport evidence exceeds the workflow snapshot budget"
            )
        evidence: list[dict[str, object]] = []
        for evidence_index, evidence_item in enumerate(evidence_raw):
            source = _closed_mapping(
                evidence_item,
                _EVIDENCE_FIELDS,
                label=f"finding evidence[{evidence_index}]",
            )
            line_start = source["lineStart"]
            line_end = source["lineEnd"]
            if (
                isinstance(line_start, bool)
                or not isinstance(line_start, int)
                or isinstance(line_end, bool)
                or not isinstance(line_end, int)
                or line_start < 1
                or line_end < line_start
            ):
                raise ContractValidationError("finding evidence line range is invalid")
            evidence.append(
                {
                    "path": _normalized_path(source["path"], label="evidence.path"),
                    "lineStart": line_start,
                    "lineEnd": line_end,
                }
            )
        for source in evidence:
            evidence_path = str(source["path"])
            if not _path_within(evidence_path, assignment.scope.read_roots):
                raise ContractValidationError("finding evidence is outside read scope")
            if any(
                _paths_overlap(evidence_path, forbidden)
                for forbidden in assignment.scope.forbidden_paths
            ):
                raise ContractValidationError("finding evidence intersects forbidden scope")
        confidence = finding["confidence"]
        if confidence not in _CONFIDENCE:
            raise ContractValidationError("finding confidence is invalid")
        findings.append(
            {
                "claimId": claim_id,
                "statement": _text(finding["statement"], label="finding.statement"),
                "evidence": evidence,
                "confidence": confidence,
            }
        )
    if status == "completed" and not findings:
        raise ContractValidationError("completed AnalystReport requires findings")

    risks_raw = raw["risks"]
    if not isinstance(risks_raw, list) or len(risks_raw) > 20:
        raise ContractValidationError("AnalystReport risks must be bounded")
    risks: list[dict[str, object]] = []
    for index, item in enumerate(risks_raw):
        risk = _closed_mapping(item, _RISK_FIELDS, label=f"risk[{index}]")
        severity = risk["severity"]
        if severity not in _SEVERITIES:
            raise ContractValidationError("risk severity is invalid")
        evidence_refs = _string_list(risk["evidenceRefs"], label="risk.evidenceRefs")
        if any(ref not in claim_ids for ref in evidence_refs):
            raise ContractValidationError("risk references an unknown claim")
        risks.append(
            {
                "severity": severity,
                "statement": _text(risk["statement"], label="risk.statement"),
                "evidenceRefs": list(evidence_refs),
            }
        )

    candidate: dict[str, object] | None = None
    if assignment.mode != "assess_claim":
        candidate_raw = _closed_mapping(
            raw["implementationOrderCandidate"],
            _ORDER_CANDIDATE_FIELDS,
            label="implementationOrderCandidate",
        )
        required = candidate_raw["required"]
        if not isinstance(required, bool):
            raise ContractValidationError("implementation order required must be boolean")
        steps_raw = candidate_raw["steps"]
        if not isinstance(steps_raw, list) or len(steps_raw) > 40:
            raise ContractValidationError("implementation order steps must be bounded")
        steps: list[dict[str, object]] = []
        step_ids: set[str] = set()
        for index, item in enumerate(steps_raw):
            step = _closed_mapping(item, _ORDER_STEP_FIELDS, label=f"order step[{index}]")
            step_id = _generic_id(step["id"], label="order step.id")
            if step_id in step_ids:
                raise ContractValidationError("implementation step id is duplicated")
            step_ids.add(step_id)
            allowed_paths = _normalized_path_list(
                step["allowedPaths"],
                label="order step.allowedPaths",
            )
            if not allowed_paths:
                raise ContractValidationError(
                    "implementation step requires target write paths"
                )
            if any(
                not _path_within(path, assignment.scope.write_paths)
                for path in allowed_paths
            ):
                raise ContractValidationError(
                    "implementation step exceeds target write scope"
                )
            if any(
                _paths_overlap(path, forbidden)
                for path in allowed_paths
                for forbidden in assignment.scope.forbidden_paths
            ):
                raise ContractValidationError(
                    "implementation step intersects forbidden paths"
                )
            steps.append(
                {
                    "id": step_id,
                    "action": _text(step["action"], label="order step.action"),
                    "allowedPaths": list(allowed_paths),
                }
            )
        if required != bool(steps):
            raise ContractValidationError(
                "implementation order required must match presence of steps"
            )
        candidate = {
            "required": required,
            "steps": steps,
            "verificationRequests": list(
                _string_list(
                    candidate_raw["verificationRequests"],
                    label="implementationOrderCandidate.verificationRequests",
                )
            ),
        }

    ballot_raw = raw["ballot"]
    ballot: dict[str, object] | None = None
    if ballot_raw is not None:
        source = _closed_mapping(ballot_raw, _BALLOT_FIELDS, label="ballot")
        choice = source["choice"]
        confidence = source["confidence"]
        if choice not in {"A", "B", "ABSTAIN"}:
            raise ContractValidationError("ballot choice is invalid")
        if confidence not in _CONFIDENCE:
            raise ContractValidationError("ballot confidence is invalid")
        evidence_refs = _string_list(source["evidenceRefs"], label="ballot.evidenceRefs")
        if any(ref not in claim_ids for ref in evidence_refs):
            raise ContractValidationError("ballot references an unknown claim")
        ballot = {
            "questionId": _generic_id(source["questionId"], label="ballot.questionId"),
            "choice": choice,
            "evidenceRefs": list(evidence_refs),
            "confidence": confidence,
        }
    if assignment.mode == "assess_claim":
        if ballot is None:
            raise ContractValidationError("assess_claim requires only a ballot")
    elif ballot is not None:
        raise ContractValidationError("ballot is only valid for assess_claim")
    if (
        assignment.mode == "prepare_change"
        and candidate is not None
        and not candidate["required"]
    ):
        raise ContractValidationError("prepare_change requires implementation steps")

    return {
        "resultVersion": 1,
        "resultType": "AnalystReport.v1",
        "assignmentId": assignment.assignment_id,
        "status": status,
        "findings": findings,
        "unknowns": list(_string_list(raw["unknowns"], label="unknowns")),
        "risks": risks,
        "implementationOrderCandidate": candidate,
        "ballot": ballot,
    }


def validate_executor_declaration(
    assignment: Assignment,
    value: object,
    *,
    implementation_order: ImplementationOrder,
) -> dict[str, object]:
    """Validate worker-owned semantics without manufacturing observed bytes."""
    if not isinstance(assignment, Assignment) or assignment.role is not WorkerRole.EXECUTOR:
        raise ContractValidationError("ExecutorDeclaration requires an Executor Assignment")
    if not isinstance(implementation_order, ImplementationOrder):
        raise ContractValidationError(
            "ExecutionResult requires a locked ImplementationOrder"
        )
    if implementation_order.assignment_id != assignment.assignment_id:
        raise ContractValidationError(
            "ImplementationOrder does not match Executor assignment"
        )
    raw = _closed_mapping(
        value, _EXECUTOR_DECLARATION_FIELDS, label="ExecutorDeclaration",
    )
    if isinstance(raw["resultVersion"], bool) or raw["resultVersion"] != 1:
        raise ContractValidationError("ExecutionResult version is unsupported")
    if raw["assignmentId"] != assignment.assignment_id:
        raise ContractValidationError("ExecutionResult assignmentId does not match")
    if raw["orderId"] != implementation_order.order_id:
        raise ContractValidationError("ExecutionResult orderId does not match")
    status = raw["status"]
    if not isinstance(status, str) or status not in {"completed", "blocked", "failed"}:
        raise ContractValidationError("ExecutionResult status is invalid")

    known_step_ids = {step.step_id for step in implementation_order.steps}
    step_results_raw = raw["stepResults"]
    if not isinstance(step_results_raw, list) or len(step_results_raw) > 40:
        raise ContractValidationError("ExecutionResult stepResults must be bounded")
    step_results: list[dict[str, object]] = []
    observed_steps: set[str] = set()
    for index, item in enumerate(step_results_raw):
        step_result = _closed_mapping(
            item,
            _STEP_RESULT_FIELDS,
            label=f"ExecutionResult stepResult[{index}]",
        )
        step_id = _generic_id(
            step_result["stepId"],
            label="ExecutionResult stepResult.stepId",
        )
        step_status = step_result["status"]
        if step_id not in known_step_ids or step_id in observed_steps:
            raise ContractValidationError(
                "ExecutionResult stepResult is unknown or duplicated"
            )
        if not isinstance(step_status, str) or step_status not in {"completed", "skipped", "failed"}:
            raise ContractValidationError(
                "ExecutionResult stepResult status is invalid"
            )
        artifact_refs = _string_list(
            step_result["artifactRefs"],
            label="ExecutionResult stepResult.artifactRefs",
        )
        if artifact_refs:
            raise ContractValidationError(
                "Executor step artifactRefs must be empty; Scheduler owns artifacts"
            )
        observed_steps.add(step_id)
        step_results.append(
            {
                "stepId": step_id,
                "status": step_status,
                "artifactRefs": [],
            }
        )
    if observed_steps != known_step_ids:
        raise ContractValidationError(
            "ExecutionResult must account for every implementation step"
        )
    if status == "completed" and any(
        item["status"] != "completed" for item in step_results
    ):
        raise ContractValidationError(
            "completed ExecutionResult requires every step to complete"
        )

    deviations_raw = raw["deviations"]
    if not isinstance(deviations_raw, list) or len(deviations_raw) > 40:
        raise ContractValidationError("ExecutionResult deviations must be bounded")
    deviations: list[dict[str, str]] = []
    deviated_steps: set[str] = set()
    for index, item in enumerate(deviations_raw):
        deviation = _closed_mapping(
            item,
            _DEVIATION_FIELDS,
            label=f"ExecutionResult deviation[{index}]",
        )
        step_id = _generic_id(
            deviation["stepId"],
            label="ExecutionResult deviation.stepId",
        )
        impact = deviation["impact"]
        if step_id not in known_step_ids or step_id in deviated_steps:
            raise ContractValidationError(
                "ExecutionResult deviation step is unknown or duplicated"
            )
        if not isinstance(impact, str) or impact not in {"low", "medium", "high"}:
            raise ContractValidationError("ExecutionResult deviation impact is invalid")
        deviated_steps.add(step_id)
        deviations.append(
            {
                "stepId": step_id,
                "reason": _text(
                    deviation["reason"],
                    label="ExecutionResult deviation.reason",
                ),
                "impact": str(impact),
            }
        )

    self_checks_raw = raw["selfChecks"]
    if not isinstance(self_checks_raw, list) or self_checks_raw:
        raise ContractValidationError(
            "Executor selfChecks must be empty; Verifier owns command evidence"
        )
    blockers = _string_list(raw["blockers"], label="ExecutionResult blockers")
    if status == "completed" and (
        blockers or any(item["impact"] == "high" for item in deviations)
    ):
        raise ContractValidationError(
            "completed ExecutionResult has an unresolved deviation or blocker"
        )
    if status != "completed" and not blockers:
        raise ContractValidationError(
            "non-completed ExecutionResult requires a blocker"
        )
    return _bounded_contract_document({
        "resultVersion": 1,
        "assignmentId": assignment.assignment_id,
        "orderId": implementation_order.order_id,
        "status": status,
        "stepResults": step_results,
        "deviations": deviations,
        "selfChecks": [],
        "blockers": list(blockers),
    }, label="ExecutorDeclaration")


def _parse_execution_result(
    assignment: Assignment,
    value: object,
    *,
    implementation_order: ImplementationOrder | None,
) -> dict[str, object]:
    raw = _closed_mapping(value, _EXECUTION_FIELDS, label="ExecutionResult")
    declaration = validate_executor_declaration(
        assignment,
        {key: raw[key] for key in _EXECUTOR_DECLARATION_FIELDS},
        implementation_order=implementation_order,
    )
    status = declaration["status"]
    change_set: dict[str, object] | None = None
    change_set_raw = raw["changeSet"]
    if status == "completed":
        change = _closed_mapping(
            change_set_raw,
            _CHANGE_SET_FIELDS,
            label="ExecutionResult changeSet",
        )
        workspace_ref = _text(
            change["workspaceRef"],
            label="ExecutionResult workspaceRef",
            maximum=500,
        )
        if (
            not workspace_ref.startswith("isolated://")
            or ".." in workspace_ref.split("/")
        ):
            raise ContractValidationError(
                "ExecutionResult workspaceRef must be isolated"
            )
        base_digest = _digest(
            change["baseDigest"],
            label="ExecutionResult baseDigest",
        )
        if base_digest != implementation_order.base_digest:
            raise ContractValidationError(
                "ExecutionResult baseDigest does not match order"
            )
        files_raw = change["files"]
        if (
            not isinstance(files_raw, list)
            or not files_raw
            or len(files_raw) > 100
        ):
            raise ContractValidationError(
                "ExecutionResult changed files must be bounded"
            )
        files: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        for index, item in enumerate(files_raw):
            changed_file = _closed_mapping(
                item,
                _CHANGED_FILE_FIELDS,
                label=f"ExecutionResult file[{index}]",
            )
            path = _normalized_path(
                changed_file["path"],
                label="ExecutionResult file.path",
            )
            action = changed_file["action"]
            if path in seen_paths:
                raise ContractValidationError(
                    "ExecutionResult changed file is duplicated"
                )
            if not _path_within(path, implementation_order.allowed_paths):
                raise ContractValidationError(
                    "ExecutionResult changed path exceeds ImplementationOrder"
                )
            if action not in {"created", "modified", "deleted"}:
                raise ContractValidationError(
                    "ExecutionResult changed file action is invalid"
                )
            seen_paths.add(path)
            files.append({"path": path, "action": str(action)})
        change_set = {
            "workspaceRef": workspace_ref,
            "baseDigest": base_digest,
            "patchDigest": _digest(
                change["patchDigest"],
                label="ExecutionResult patchDigest",
            ),
            "files": files,
        }
    elif change_set_raw is not None:
        raise ContractValidationError(
            "non-completed ExecutionResult cannot expose a promotable changeSet"
        )

    return {
        **declaration,
        "resultType": "ExecutionResult.v1",
        "changeSet": change_set,
    }


def parse_executor_declaration(
    assignment: Assignment,
    value: object,
    *,
    implementation_order: ImplementationOrder,
    scheduler_change_set: Mapping[str, object] | None,
) -> dict[str, object]:
    """Bind an Executor's semantic declaration to runtime-observed bytes.

    The Worker owns step status, deviations, and blockers. Self-checks and step
    artifact references must remain empty; only the Scheduler may supply the
    isolated ChangeSet, its patch digest, and verification evidence.
    """

    raw = _closed_mapping(
        value,
        _EXECUTOR_DECLARATION_FIELDS,
        label="ExecutorDeclaration",
    )
    if raw["status"] == "completed":
        if not isinstance(scheduler_change_set, Mapping):
            raise ContractValidationError(
                "completed ExecutorDeclaration requires a scheduler ChangeSet"
            )
        change_set: object = dict(scheduler_change_set)
    else:
        if scheduler_change_set is not None:
            raise ContractValidationError(
                "blocked ExecutorDeclaration cannot receive a promotable ChangeSet"
            )
        change_set = None
    result = _parse_execution_result(
        assignment,
        {**raw, "changeSet": change_set},
        implementation_order=implementation_order,
    )
    return _bounded_contract_document(
        result,
        label="ExecutionResult",
    )


def implementation_receipt_ref(assignment_id: str, order_id: str, patch_digest: str, check_id: str) -> str:
    """A host feedback namespace distinct from formal Verifier evidence."""
    material = "\0".join((assignment_id, order_id, patch_digest, check_id))
    return "artifact_implementation_" + hashlib.sha256(material.encode()).hexdigest()[:32]


def _candidate_stream_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "byteCount", "digest", "preview", "previewBase64", "truncated",
    }:
        return False
    count, preview, encoded = value["byteCount"], value["preview"], value["previewBase64"]
    if (type(count) is not int or count < 0 or not isinstance(preview, str)
            or not isinstance(encoded, str) or len(preview) > 5000 or len(encoded) > 8000
            or type(value["truncated"]) is not bool or value["truncated"] != (count > 4000)
            or _DIGEST_RE.fullmatch(str(value["digest"])) is None):
        return False
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return False
    if preview != data.decode("utf-8", errors="replace"):
        return False
    return ("\n...<truncated>...\n" in preview if value["truncated"] else
            len(data) == count and "sha256:" + hashlib.sha256(data).hexdigest() == value["digest"])


def parse_execution_candidate(
    assignment: Assignment,
    value: object,
    *,
    implementation_order: ImplementationOrder,
    required_checks,
    receipt_documents: Mapping[str, object],
) -> dict[str, object]:
    """Validate a scheduler-only pending candidate, never worker semantics.

    Deliberately separate from parse_role_result/ExecutorDeclaration: no model
    can claim this status, and no step or criterion becomes completed here.
    """
    order = implementation_order
    if (not isinstance(assignment, Assignment) or assignment.role is not WorkerRole.EXECUTOR
            or not isinstance(order, ImplementationOrder) or order.assignment_id != assignment.assignment_id):
        raise ContractValidationError("ExecutionCandidate requires its exact Executor order")
    raw = _closed_mapping(value, frozenset({
        "candidateVersion", "resultType", "assignmentId", "orderId", "status",
        "handoffReason", "unassessedStepIds", "changeSet", "implementationReceiptRefs",
    }), label="ExecutionCandidate")
    if (type(raw["candidateVersion"]) is not int or raw["candidateVersion"] != 1
            or raw["resultType"] != "ExecutionCandidate.v1"
            or raw["status"] != "awaiting_verification"
            or raw["handoffReason"] not in (
                "executor_budget_exceeded", "executor_write_feedback_pending",
                "executor_submission_feedback_pending",
            )
            or raw["assignmentId"] != assignment.assignment_id or raw["orderId"] != order.order_id
            or raw["unassessedStepIds"] != [step.step_id for step in order.steps]):
        raise ContractValidationError("ExecutionCandidate identity or pending semantics are invalid")
    change = _closed_mapping(raw["changeSet"], _CHANGE_SET_FIELDS, label="ExecutionCandidate changeSet")
    workspace = _text(change["workspaceRef"], label="ExecutionCandidate workspaceRef", maximum=500)
    if not workspace.startswith("isolated://") or ".." in workspace.split("/"):
        raise ContractValidationError("ExecutionCandidate workspace must be isolated")
    if _digest(change["baseDigest"], label="ExecutionCandidate baseDigest") != order.base_digest:
        raise ContractValidationError("ExecutionCandidate base does not match its order")
    digest = _digest(change["patchDigest"], label="ExecutionCandidate patchDigest")
    files_raw = change["files"]
    if not isinstance(files_raw, list) or not 1 <= len(files_raw) <= 100:
        raise ContractValidationError("ExecutionCandidate must contain actual changed files")
    files, seen = [], set()
    for item in files_raw:
        file = _closed_mapping(item, _CHANGED_FILE_FIELDS, label="ExecutionCandidate changed file")
        path = _normalized_path(file["path"], label="ExecutionCandidate changed path")
        if (path in seen or not _path_within(path, order.allowed_paths)
                or not _path_within(path, assignment.scope.write_paths)
                or any(_paths_overlap(path, forbidden) for forbidden in assignment.scope.forbidden_paths)
                or file["action"] not in {"created", "modified", "deleted"}):
            raise ContractValidationError("ExecutionCandidate changed path or action is invalid")
        seen.add(path)
        files.append({"path": path, "action": file["action"]})
    checks = tuple(required_checks)
    refs = raw["implementationReceiptRefs"]
    if (not checks or len(checks) > 40 or not isinstance(refs, list) or len(refs) != len(checks)
            or len({check.check_id for check in checks}) != len(checks)
            or tuple(" ".join(check.argv) for check in checks) != order.verification_requests):
        raise ContractValidationError("ExecutionCandidate requires every approved implementation check")
    for check, ref in zip(checks, refs, strict=True):
        if (check.kind not in {"tests", "build", "lint", "typecheck"} or not isinstance(ref, str)
                or ref != implementation_receipt_ref(assignment.assignment_id, order.order_id, digest, check.check_id)):
            raise ContractValidationError("ExecutionCandidate implementation receipt reference is invalid")
        document = _closed_mapping(receipt_documents.get(ref), frozenset({
            "receiptVersion", "resultType", "workflowId", "taskId", "assignmentId", "orderId", "receipt",
        }), label="ImplementationCheckReceipt")
        if (type(document["receiptVersion"]) is not int or document["receiptVersion"] != 1
                or document["resultType"] != "ImplementationCheckReceipt.v1"
                or document["workflowId"] != assignment.workflow_id or document["taskId"] != assignment.task_id
                or document["assignmentId"] != assignment.assignment_id or document["orderId"] != order.order_id):
            raise ContractValidationError("ImplementationCheckReceipt owner is invalid")
        receipt = _closed_mapping(document["receipt"], frozenset({
            "checkId", "argv", "patchDigest", "status", "evidence", "verification", "writeback", "observationSequence",
        }), label="ImplementationCheckReceipt receipt")
        argv = list(check.argv)
        if (receipt["checkId"] != check.check_id or receipt["argv"] != argv
                or receipt["patchDigest"] != digest or receipt["status"] not in {"passed", "failed"}
                or receipt["verification"] != "implementation_feedback" or receipt["writeback"] != "pending"
                or type(receipt["observationSequence"]) is not int or receipt["observationSequence"] < 1):
            raise ContractValidationError("ImplementationCheckReceipt is not a completed check on this candidate")
        evidence = _closed_mapping(receipt["evidence"], frozenset({
            "evidenceVersion", "checkId", "kind", "argv", "resolvedExecutable", "commandDigest",
            "status", "exitCode", "durationMs", "failureReason", "stdout", "stderr",
        }), label="ImplementationCheckReceipt evidence")
        if (type(evidence["evidenceVersion"]) is not int or evidence["evidenceVersion"] != 1
                or evidence["checkId"] != check.check_id or evidence["kind"] != check.kind
                or evidence["argv"] != argv or evidence["status"] != receipt["status"]
                or type(evidence["exitCode"]) is not int or evidence["exitCode"] < 0
                or (evidence["exitCode"] == 0) != (evidence["status"] == "passed")
                or type(evidence["durationMs"]) is not int or evidence["durationMs"] < 0
                or evidence["failureReason"] is not None
                or not isinstance(evidence["resolvedExecutable"], str)
                or not (evidence["resolvedExecutable"].startswith(("/", "\\\\"))
                        or re.match(r"^[A-Za-z]:[\\/]", evidence["resolvedExecutable"]))
                or evidence["commandDigest"] != "sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest()
                or not _candidate_stream_is_valid(evidence["stdout"])
                or not _candidate_stream_is_valid(evidence["stderr"])):
            raise ContractValidationError("ImplementationCheckReceipt execution evidence is invalid")
    return _bounded_contract_document({**raw, "changeSet": {
        "workspaceRef": workspace, "baseDigest": order.base_digest, "patchDigest": digest, "files": files,
    }}, label="ExecutionCandidate")


def parse_verification_result(
    value: object,
    *,
    workflow_id: str,
    task_id: str,
    execution_assignment_id: str,
    patch_digest: str,
    required_check_ids: tuple[str, ...],
) -> dict[str, object]:
    """Validate deterministic check evidence against one frozen patch."""

    expected_workflow_id = _identifier(
        workflow_id, label="workflowId", prefix="wf"
    )
    expected_task_id = _identifier(task_id, label="taskId", prefix="task")
    expected_assignment_id = _identifier(
        execution_assignment_id,
        label="executionAssignmentId",
        prefix="asg",
    )
    expected_patch_digest = _digest(patch_digest, label="patchDigest")
    if not isinstance(required_check_ids, tuple):
        raise TypeError("required_check_ids must be a tuple")
    normalized_required = tuple(
        _generic_id(item, label="required check id")
        for item in required_check_ids
    )
    if not normalized_required or len(set(normalized_required)) != len(
        normalized_required
    ):
        raise ContractValidationError(
            "required check ids must be non-empty and unique"
        )

    raw = _closed_mapping(
        value,
        _VERIFICATION_FIELDS,
        label="VerificationResult",
    )
    if (
        isinstance(raw["verificationVersion"], bool)
        or raw["verificationVersion"] != 1
    ):
        raise ContractValidationError("VerificationResult version is unsupported")
    if raw["workflowId"] != expected_workflow_id:
        raise ContractValidationError("VerificationResult workflowId does not match")
    if raw["taskId"] != expected_task_id:
        raise ContractValidationError("VerificationResult taskId does not match")
    if raw["executionAssignmentId"] != expected_assignment_id:
        raise ContractValidationError(
            "VerificationResult executionAssignmentId does not match"
        )
    if raw["patchDigest"] != expected_patch_digest:
        raise ContractValidationError("VerificationResult patchDigest does not match")

    checks_raw = raw["checks"]
    if (
        not isinstance(checks_raw, list)
        or not checks_raw
        or len(checks_raw) > 40
    ):
        raise ContractValidationError("VerificationResult checks must be bounded")
    checks: list[dict[str, object]] = []
    statuses: dict[str, str] = {}
    for index, item in enumerate(checks_raw):
        check = _closed_mapping(
            item,
            _VERIFICATION_CHECK_FIELDS,
            label=f"verification check[{index}]",
        )
        check_id = _generic_id(check["checkId"], label="check.checkId")
        if check_id in statuses:
            raise ContractValidationError("verification check id is duplicated")
        kind = check["kind"]
        status = check["status"]
        if kind not in _VERIFICATION_KINDS:
            raise ContractValidationError("verification check kind is invalid")
        if status not in {"passed", "failed", "inconclusive"}:
            raise ContractValidationError("verification check status is invalid")
        exit_code = _non_negative_int(
            check["exitCode"],
            label="verification check exitCode",
            allow_none=True,
        )
        if (
            (status == "passed" and exit_code != 0)
            or (status == "failed" and (exit_code is None or exit_code == 0))
            or (status == "inconclusive" and exit_code is not None)
        ):
            raise ContractValidationError(
                "verification check exitCode contradicts status"
            )
        statuses[check_id] = str(status)
        checks.append(
            {
                "checkId": check_id,
                "kind": kind,
                "status": status,
                "exitCode": exit_code,
                "durationMs": _non_negative_int(
                    check["durationMs"],
                    label="verification check durationMs",
                ),
                "artifactRef": _generic_id(
                    check["artifactRef"],
                    label="verification check artifactRef",
                ),
            }
        )

    missing = [item for item in normalized_required if item not in statuses]
    if missing:
        raise ContractValidationError(
            "VerificationResult is missing required checks"
        )
    required_statuses = [statuses[item] for item in normalized_required]
    aggregate = (
        "failed"
        if "failed" in required_statuses
        else "inconclusive"
        if "inconclusive" in required_statuses
        else "passed"
    )
    if raw["status"] != aggregate:
        raise ContractValidationError(
            "VerificationResult aggregate status contradicts required checks"
        )
    result = {
        "verificationVersion": 1,
        "resultType": "VerificationResult.v1",
        "verificationId": _prefixed_id(
            raw["verificationId"],
            label="verificationId",
            prefix="verification",
        ),
        "workflowId": expected_workflow_id,
        "taskId": expected_task_id,
        "executionAssignmentId": expected_assignment_id,
        "patchDigest": expected_patch_digest,
        "status": aggregate,
        "checks": checks,
    }
    return _bounded_contract_document(
        result,
        label="VerificationResult",
    )


def _parse_review_result(
    assignment: Assignment,
    value: object,
) -> dict[str, object]:
    fields = (_REVIEW_FIELDS | {"coverageArtifactRef"}
              if isinstance(value, Mapping) and "coverageArtifactRef" in value else _REVIEW_FIELDS)
    raw = _closed_mapping(value, fields, label="ReviewVerdict")
    if isinstance(raw["resultVersion"], bool) or raw["resultVersion"] != 1:
        raise ContractValidationError("ReviewVerdict version is unsupported")
    if raw["assignmentId"] != assignment.assignment_id:
        raise ContractValidationError("ReviewVerdict assignmentId does not match")
    patch_digest = _digest(raw["patchDigest"], label="ReviewVerdict patchDigest")
    verdict = raw["verdict"]
    confidence = raw["confidence"]
    if verdict not in {"approved", "changes_required", "inconclusive"}:
        raise ContractValidationError("ReviewVerdict verdict is invalid")
    if confidence not in _CONFIDENCE:
        raise ContractValidationError("ReviewVerdict confidence is invalid")
    coverage_ref = None
    if "coverageArtifactRef" in raw:
        coverage_ref = _generic_id(raw["coverageArtifactRef"], label="ReviewVerdict coverage artifact")
        if coverage_ref != f"artifact_review_coverage_{assignment.assignment_id}":
            raise ContractValidationError("ReviewVerdict coverage artifact owner is invalid")
    if (verdict == "approved" and coverage_ref is None
            and any(item.constraint_id == REVIEW_COVERAGE_CONSTRAINT_ID for item in assignment.constraints)):
        raise ContractValidationError("approved ReviewVerdict requires host coverage evidence")
    readable_artifacts = set(assignment.input_refs)

    criteria_raw = raw["criteria"]
    if not isinstance(criteria_raw, list) or len(criteria_raw) > _MAX_ITEMS:
        raise ContractValidationError("ReviewVerdict criteria must be bounded")
    criteria: list[dict[str, object]] = []
    criterion_statuses: dict[str, str] = {}
    for index, item in enumerate(criteria_raw):
        criterion = _closed_mapping(
            item,
            _REVIEW_CRITERION_FIELDS,
            label=f"review criterion[{index}]",
        )
        criterion_id = _text(
            criterion["criterionId"],
            label="review criterion.criterionId",
            maximum=80,
        )
        if criterion_id in criterion_statuses:
            raise ContractValidationError("review criterion id is duplicated")
        status = criterion["status"]
        if status not in {"satisfied", "failed", "unknown"}:
            raise ContractValidationError("review criterion status is invalid")
        evidence_refs = _string_list(
            criterion["evidenceRefs"],
            label="review criterion.evidenceRefs",
        )
        if status != "unknown" and not evidence_refs:
            raise ContractValidationError(
                "review criterion decision requires evidence"
            )
        if any(ref not in readable_artifacts for ref in evidence_refs):
            raise ContractValidationError(
                "review criterion references an unreadable artifact"
            )
        criterion_statuses[criterion_id] = str(status)
        criteria.append(
            {
                "criterionId": criterion_id,
                "status": status,
                "evidenceRefs": list(evidence_refs),
            }
        )
    expected_criteria = {
        item.criterion_id for item in assignment.acceptance_criteria
    }
    if set(criterion_statuses) != expected_criteria:
        raise ContractValidationError(
            "ReviewVerdict must account for all acceptance criteria"
        )

    def parse_findings(
        values: object,
        *,
        label: str,
        severities: frozenset[str],
    ) -> list[dict[str, object]]:
        if not isinstance(values, list) or len(values) > 40:
            raise ContractValidationError(f"{label} must be bounded")
        parsed: list[dict[str, object]] = []
        seen: set[str] = set()
        for index, item in enumerate(values):
            finding = _closed_mapping(
                item,
                _REVIEW_FINDING_FIELDS,
                label=f"{label}[{index}]",
            )
            finding_id = _generic_id(finding["id"], label=f"{label}.id")
            if finding_id in seen:
                raise ContractValidationError(f"{label} id is duplicated")
            if finding["severity"] not in severities:
                raise ContractValidationError(f"{label} severity is invalid")
            evidence_refs = _string_list(
                finding["evidenceRefs"],
                label=f"{label}.evidenceRefs",
            )
            if not evidence_refs:
                raise ContractValidationError(f"{label} requires evidence")
            if any(ref not in readable_artifacts for ref in evidence_refs):
                raise ContractValidationError(
                    f"{label} references an unreadable artifact"
                )
            seen.add(finding_id)
            parsed.append(
                {
                    "id": finding_id,
                    "severity": finding["severity"],
                    "statement": _text(
                        finding["statement"],
                        label=f"{label}.statement",
                    ),
                    "evidenceRefs": list(evidence_refs),
                }
            )
        return parsed

    blocking = parse_findings(
        raw["blockingFindings"],
        label="blocking findings",
        severities=frozenset({"high", "critical"}),
    )
    warnings = parse_findings(
        raw["warnings"],
        label="warnings",
        severities=frozenset({"low", "medium"}),
    )
    requested_checks = _string_list(
        raw["requestedChecks"],
        label="ReviewVerdict requestedChecks",
    )
    all_satisfied = all(
        status == "satisfied" for status in criterion_statuses.values()
    )
    if verdict == "approved" and (
        not all_satisfied or blocking or requested_checks
    ):
        raise ContractValidationError(
            "approved verdict requires satisfied criteria and no blockers"
        )
    if verdict == "changes_required" and not (
        blocking or "failed" in criterion_statuses.values()
    ):
        raise ContractValidationError(
            "changes_required verdict requires a blocking reason"
        )
    return {
        "resultVersion": 1,
        "resultType": "ReviewVerdict.v1",
        "assignmentId": assignment.assignment_id,
        "patchDigest": patch_digest,
        "verdict": verdict,
        "criteria": criteria,
        "blockingFindings": blocking,
        "warnings": warnings,
        "requestedChecks": list(requested_checks),
        "confidence": confidence,
        **({"coverageArtifactRef": coverage_ref} if coverage_ref is not None else {}),
    }


def parse_review_coverage_evidence(
    assignment: Assignment,
    value: object,
    *,
    review: Mapping[str, object],
    artifact_documents: Mapping[str, object],
) -> dict[str, object]:
    """Validate durable host observations, never infer semantic test coverage.

    The live host captures source line ranges and bytes when they are read.
    Replay validates their owner/scope/digest bindings, not a mutable checkout.
    Probe source and execution documents remain separate immutable artifacts.
    """
    if not isinstance(assignment, Assignment) or assignment.role is not WorkerRole.REVIEWER:
        raise ContractValidationError("review coverage requires a Reviewer Assignment")
    raw = _closed_mapping(value, frozenset({
        "coverageVersion", "resultType", "workflowId", "taskId", "assignmentId",
        "patchDigest", "criteria", "probes",
    }), label="ReviewCoverageEvidence")
    digest = _digest(raw["patchDigest"], label="review coverage patchDigest")
    if (type(raw["coverageVersion"]) is not int or raw["coverageVersion"] != 1
            or raw["resultType"] != "ReviewCoverageEvidence.v1"
            or raw["workflowId"] != assignment.workflow_id or raw["taskId"] != assignment.task_id
            or raw["assignmentId"] != assignment.assignment_id
            or review.get("assignmentId") != assignment.assignment_id
            or review.get("patchDigest") != digest
            or review.get("verdict") not in {"approved", "changes_required", "inconclusive"}):
        raise ContractValidationError("review coverage identity or patch is invalid")
    expected = {criterion.criterion_id for criterion in assignment.acceptance_criteria}
    decisions = {item["criterionId"]: item["status"] for item in review.get("criteria", [])
                 if isinstance(item, Mapping) and "criterionId" in item and "status" in item}
    if set(decisions) != expected:
        raise ContractValidationError("review coverage requires the exact review criteria")
    approved = review["verdict"] == "approved"
    probes_raw = raw["probes"]
    if not isinstance(probes_raw, list) or len(probes_raw) > _MAX_ITEMS or (approved and not probes_raw):
        raise ContractValidationError("approved review coverage requires an independent probe")
    probes, probe_criteria, receipt_refs = [], {}, set()
    for entry in probes_raw:
        probe = _closed_mapping(entry, frozenset({"probeId", "sourceRef", "receiptRef"}), label="review coverage probe")
        probe_id = _generic_id(probe["probeId"], label="review probe id")
        source_ref = _generic_id(probe["sourceRef"], label="review probe source ref")
        receipt_ref = _generic_id(probe["receiptRef"], label="review probe receipt ref")
        if source_ref != "artifact_review_probe_source_" + hashlib.sha256(probe_id.encode()).hexdigest()[:32]:
            raise ContractValidationError("review probe source namespace is invalid")
        if probe_id in probe_criteria or receipt_ref in receipt_refs:
            raise ContractValidationError("review coverage probe is duplicated")
        source = _closed_mapping(artifact_documents.get(source_ref), frozenset({
            "probeVersion", "resultType", "probeId", "criterionIds", "scenario", "code", "sourceDigest",
        }), label="ReviewProbeSource")
        if not validate_review_probe_source(source):
            raise ContractValidationError("review probe immutable source identity is invalid")
        source_ids = _string_list(source["criterionIds"], label="review probe criterionIds")
        code = source["code"]
        if not isinstance(code, str) or not code.strip():
            raise ContractValidationError("review probe source code is missing")
        try:
            source_digest = "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()
        except UnicodeError as exc:
            raise ContractValidationError("review probe source is not UTF-8") from exc
        if (type(source["probeVersion"]) is not int or source["probeVersion"] != 1
                or source["resultType"] != "ReviewProbeSource.v1" or source["probeId"] != probe_id
                or not source_ids or not set(source_ids).issubset(expected)
                or source["sourceDigest"] != source_digest):
            raise ContractValidationError("review probe immutable source is invalid")
        _text(source["scenario"], label="review probe scenario", maximum=1200)
        execution_raw = artifact_documents.get(receipt_ref)
        execution_fields = frozenset({
            "probeVersion", "resultType", "workflowId", "taskId", "assignmentId", "patchDigest",
            "probeId", "sourceRef", "sequence", "status", "reason", "testReport", "evidence",
        })
        if isinstance(execution_raw, Mapping) and "executionBinding" in execution_raw:
            execution_fields |= {"executionBinding"}
        execution = _closed_mapping(execution_raw, execution_fields, label="ReviewProbeExecution")
        if (type(execution["probeVersion"]) is not int or execution["probeVersion"] != 1
                or execution["resultType"] != "ReviewProbeExecution.v1"
                or execution["workflowId"] != assignment.workflow_id or execution["taskId"] != assignment.task_id
                or execution["assignmentId"] != assignment.assignment_id or execution["patchDigest"] != digest
                or execution["probeId"] != probe_id or execution["sourceRef"] != source_ref
                or type(execution["sequence"]) is not int or execution["sequence"] < 1
                or execution["status"] not in {"passed", "failed", "inconclusive"}):
            raise ContractValidationError("review probe execution owner or patch is invalid")
        receipt_material = "\0".join((assignment.workflow_id, assignment.assignment_id, probe_id,
                                      str(execution["sequence"])))
        if receipt_ref != "artifact_review_probe_run_" + hashlib.sha256(receipt_material.encode()).hexdigest()[:32]:
            raise ContractValidationError("review probe receipt namespace is invalid")
        if "executionBinding" in execution:
            from minicode.review_probe_execution import validate_review_probe_execution_binding

            try:
                validate_review_probe_execution_binding(dict(execution), probe_source_digest=source_digest)
            except (TypeError, ValueError) as exc:
                raise ContractValidationError("review probe execution binding is invalid") from exc
        evidence = _closed_mapping(execution["evidence"], frozenset({
            "evidenceVersion", "checkId", "kind", "argv", "resolvedExecutable", "commandDigest",
            "status", "exitCode", "durationMs", "failureReason", "stdout", "stderr",
        }), label="review probe execution evidence")
        argv = evidence["argv"]
        status, exit_code = evidence["status"], evidence["exitCode"]
        conclusive = status in {"passed", "failed"}
        if (type(evidence["evidenceVersion"]) is not int or evidence["evidenceVersion"] != 1
                or not isinstance(argv, list) or not argv
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)
                or evidence["kind"] not in {"tests", "build", "lint", "typecheck"}
                or not isinstance(evidence["checkId"], str) or not evidence["checkId"]
                or status not in {"passed", "failed", "inconclusive"}
                or type(evidence["durationMs"]) is not int or evidence["durationMs"] < 0
                or evidence["commandDigest"] != "sha256:" + hashlib.sha256("\0".join(argv).encode()).hexdigest()
                or not _candidate_stream_is_valid(evidence["stdout"])
                or not _candidate_stream_is_valid(evidence["stderr"])):
            raise ContractValidationError("review probe execution evidence is invalid")
        executable = evidence["resolvedExecutable"]
        absolute_executable = (isinstance(executable, str)
                               and (executable.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", executable)))
        if (conclusive and (type(exit_code) is not int or exit_code < 0
                            or (exit_code == 0) != (status == "passed")
                            or evidence["failureReason"] is not None or not absolute_executable)):
            raise ContractValidationError("review probe has no conclusive execution evidence")
        if not conclusive and (exit_code is not None or not isinstance(evidence["failureReason"], str)
                               or not evidence["failureReason"]):
            raise ContractValidationError("inconclusive review probe failure is invalid")
        report = execution["testReport"]
        if report is not None:
            report_fields = frozenset({
                "collected", "passed", "failed", "errors", "skipped", "reportDigest",
            })
            if isinstance(report, Mapping) and "caseReport" in report:
                report_fields |= {"caseReport"}
                # Execution observations must retain their classified JUnit
                # cases; validate_pytest_case_report checks both bindings below.
                if "executionReport" in report:
                    report_fields |= {"executionReport"}
            report = _closed_mapping(report, report_fields, label="review probe test report")
            if (any(type(report[key]) is not int or report[key] < 0
                    for key in ("collected", "passed", "failed", "errors", "skipped"))
                    or report["collected"] != sum(report[key] for key in ("passed", "failed", "errors", "skipped"))):
                raise ContractValidationError("review probe test report counts are invalid")
            _digest(report["reportDigest"], label="review probe test report digest")
            if "caseReport" in report and not validate_pytest_case_report(report):
                raise ContractValidationError("review probe case classification is invalid")
        if execution["status"] == "inconclusive":
            _text(execution["reason"], label="inconclusive review probe reason", maximum=1200)
        elif (execution["reason"] is not None or status != execution["status"]
              or exit_code != (0 if execution["status"] == "passed" else 1)
              or report is None or report["errors"] or report["skipped"]
              or (execution["status"] == "passed" and (report["passed"] < 1 or report["failed"]))
              or (execution["status"] == "failed" and report["failed"] < 1)):
            raise ContractValidationError("review probe host conclusion does not match its execution")
        if approved and execution["status"] != "passed":
            raise ContractValidationError("approved review coverage contains an unpassed probe")
        probe_criteria[probe_id] = set(source_ids)
        receipt_refs.add(receipt_ref)
        probes.append(dict(probe))
    criteria_raw = raw["criteria"]
    if not isinstance(criteria_raw, list) or len(criteria_raw) != len(expected):
        raise ContractValidationError("review coverage criteria are incomplete")
    criteria, seen = [], set()
    for entry in criteria_raw:
        row = _closed_mapping(entry, frozenset({
            "criterionId", "rationale", "probeIds", "sourceEvidence",
        }), label="review coverage criterion")
        criterion_id = row["criterionId"]
        if not isinstance(criterion_id, str) or criterion_id not in expected or criterion_id in seen:
            raise ContractValidationError("review coverage criterion is unknown or duplicated")
        rationale = _text(row["rationale"], label="review coverage rationale", maximum=1200)
        probe_ids = _string_list(row["probeIds"], label="review coverage probeIds")
        if any(criterion_id not in probe_criteria.get(probe_id, set()) for probe_id in probe_ids):
            raise ContractValidationError("review coverage probe does not cover this criterion")
        source_rows = row["sourceEvidence"]
        if not isinstance(source_rows, list) or len(source_rows) > _MAX_ITEMS:
            raise ContractValidationError("review source anchors must be bounded")
        anchors, seen_anchors = [], set()
        for entry in source_rows:
            anchor = _closed_mapping(entry, frozenset({
                "path", "startLine", "endLine", "sourceDigest",
            }), label="review source anchor")
            path = _normalized_path(anchor["path"], label="review source path")
            start, end = anchor["startLine"], anchor["endLine"]
            source_digest = _digest(anchor["sourceDigest"], label="review source digest")
            if (not _path_within(path, assignment.scope.read_roots)
                    or any(_paths_overlap(path, forbidden) for forbidden in assignment.scope.forbidden_paths)
                    or type(start) is not int or type(end) is not int or start < 1 or end < start):
                raise ContractValidationError("review source anchor is outside the authorized scope or lines")
            key = (path, start, end, source_digest)
            if key in seen_anchors:
                raise ContractValidationError("review source anchor is duplicated")
            seen_anchors.add(key)
            anchors.append({"path": path, "startLine": start, "endLine": end, "sourceDigest": source_digest})
        if decisions[criterion_id] == "satisfied" and not (probe_ids or anchors):
            raise ContractValidationError("satisfied review criterion requires observed coverage evidence")
        seen.add(criterion_id)
        criteria.append({"criterionId": criterion_id, "rationale": rationale,
                         "probeIds": list(probe_ids), "sourceEvidence": anchors})
    return _bounded_contract_document({**raw, "criteria": criteria, "probes": probes}, label="ReviewCoverageEvidence")


def review_probe_correction_artifact_id(document: Mapping[str, object]) -> str:
    """The immutable host namespace for one explicit correction decision."""
    return correction_artifact_id(document)


def parse_review_probe_correction(
    assignment: Assignment,
    value: object,
    *,
    artifact_documents: Mapping[str, object],
    required_checks,
) -> dict[str, object]:
    """Validate an explicit retirement, preserving every historical attempt.

    The host captures source anchors; replay verifies scope and immutable
    bindings rather than rereading a later checkout. No model verdict is
    synthesized by the inconclusive envelopes used to validate raw receipts.
    """
    if not isinstance(assignment, Assignment) or assignment.role is not WorkerRole.REVIEWER:
        raise ContractValidationError("probe correction requires a Reviewer Assignment")
    raw = _closed_mapping(value, frozenset({
        "correctionVersion", "resultType", "workflowId", "taskId", "assignmentId", "patchDigest",
        "oldProbeId", "newProbeId", "oldSourceRef", "newSourceRef", "oldReceiptRefs", "newReceiptRef",
        "sourceEvidence", "reason",
    }), label="ReviewProbeCorrection")
    digest = _digest(raw["patchDigest"], label="probe correction patchDigest")
    if (type(raw["correctionVersion"]) is not int or raw["correctionVersion"] != 1
            or raw["resultType"] != "ReviewProbeCorrection.v1"
            or raw["workflowId"] != assignment.workflow_id or raw["taskId"] != assignment.task_id
            or raw["assignmentId"] != assignment.assignment_id):
        raise ContractValidationError("probe correction owner is invalid")
    for key in ("oldProbeId", "newProbeId", "oldSourceRef", "newSourceRef", "newReceiptRef"):
        if _generic_id(raw[key], label=f"probe correction {key}") != raw[key]:
            raise ContractValidationError("probe correction identity is not canonical")
    old_refs = _string_list(raw["oldReceiptRefs"], label="probe correction oldReceiptRefs")
    historical = {
        ref: document for ref, document in artifact_documents.items()
        if isinstance(document, Mapping) and document.get("resultType") == "ReviewProbeExecution.v1"
        and document.get("workflowId") == assignment.workflow_id
        and document.get("taskId") == assignment.task_id and document.get("probeId") == raw["oldProbeId"]
    }
    if not old_refs or set(old_refs) != set(historical):
        raise ContractValidationError("probe correction must retain the complete old execution history")
    new_receipt = artifact_documents.get(raw["newReceiptRef"])
    if (not isinstance(new_receipt, Mapping) or new_receipt.get("assignmentId") != assignment.assignment_id
            or new_receipt.get("patchDigest") != digest):
        raise ContractValidationError("replacement probe execution is not owned by the current Reviewer")
    anchors = raw["sourceEvidence"]
    if not isinstance(anchors, list) or not anchors:
        raise ContractValidationError("probe correction requires observed source anchors")
    reason = _text(raw["reason"], label="probe correction reason", maximum=1200)
    approved_commands = {(check.check_id, check.kind, tuple(check.argv)) for check in required_checks}
    receipt_items = [(ref, historical[ref], raw["oldProbeId"], raw["oldSourceRef"]) for ref in old_refs]
    receipt_items.append((raw["newReceiptRef"], new_receipt, raw["newProbeId"], raw["newSourceRef"]))
    sequences = set()
    for ref, receipt, probe_id, source_ref in receipt_items:
        owner_id = receipt.get("assignmentId")
        if owner_id != assignment.assignment_id:
            owners = [document.get("assignment") for document in artifact_documents.values()
                      if isinstance(document, Mapping) and document.get("resultType") == "AssignmentArtifact.v1"
                      and isinstance(document.get("assignment"), Mapping)
                      and document["assignment"].get("assignmentId") == owner_id]
            if not owners:
                raise ContractValidationError("probe correction historical owner is missing")
            for owner_raw in owners:
                owner = parse_assignment(owner_raw)
                if (owner.workflow_id != assignment.workflow_id or owner.task_id != assignment.task_id
                        or owner.role not in {WorkerRole.EXECUTOR, WorkerRole.REVIEWER}):
                    raise ContractValidationError("probe correction historical owner is invalid")
        # Borrow only the immutable owner identity to reuse the closed receipt
        # validator; the current Reviewer remains the sole correction issuer.
        envelope_owner = replace(assignment, assignment_id=owner_id)
        rows = [{"criterionId": criterion.criterion_id, "rationale": reason,
                 "probeIds": [], "sourceEvidence": anchors} for criterion in assignment.acceptance_criteria]
        parse_review_coverage_evidence(envelope_owner, {
            "coverageVersion": 1, "resultType": "ReviewCoverageEvidence.v1",
            "workflowId": assignment.workflow_id, "taskId": assignment.task_id,
            "assignmentId": owner_id, "patchDigest": receipt.get("patchDigest"),
            "criteria": rows, "probes": [{"probeId": probe_id, "sourceRef": source_ref, "receiptRef": ref}],
        }, review={"assignmentId": owner_id, "patchDigest": receipt.get("patchDigest"), "verdict": "inconclusive",
                   "criteria": [{"criterionId": row["criterionId"], "status": "unknown"} for row in rows]},
           artifact_documents=artifact_documents)
        evidence = receipt["evidence"]
        if "executionBinding" in receipt:
            from minicode.review_probe_execution import validate_review_probe_execution_binding

            try:
                checks = tuple(check.to_execution_check() if isinstance(check, WorkflowVerificationCheck)
                               else check for check in required_checks)
                validate_review_probe_execution_binding(dict(receipt), checks,
                    probe_source_digest=artifact_documents[source_ref]["sourceDigest"])
            except (TypeError, ValueError) as exc:
                raise ContractValidationError("probe correction command exceeds the approved checks") from exc
        elif (evidence["checkId"], evidence["kind"], tuple(evidence["argv"])) not in approved_commands:
            raise ContractValidationError("probe correction command exceeds the approved checks")
        if receipt["sequence"] in sequences:
            raise ContractValidationError("probe correction execution sequence is duplicated")
        sequences.add(receipt["sequence"])
    if any(document["sequence"] >= new_receipt["sequence"] for document in historical.values()):
        raise ContractValidationError("replacement execution must follow every old attempt")
    try:
        validate_probe_correction_sources(artifact_documents[raw["oldSourceRef"]],
                                          artifact_documents[raw["newSourceRef"]])
        validate_probe_correction_execution(list(historical.values()), new_receipt, digest)
    except (TypeError, ValueError, KeyError) as exc:
        raise ContractValidationError(str(exc)) from exc
    return _bounded_contract_document(dict(raw), label="ReviewProbeCorrection")


def parse_conflict_package(
    value: object,
    *,
    workflow_id: str,
    task_id: str,
) -> dict[str, object]:
    """Validate one frozen, scheduler-owned material conflict."""

    expected_workflow_id = _identifier(
        workflow_id, label="workflowId", prefix="wf"
    )
    expected_task_id = _identifier(task_id, label="taskId", prefix="task")
    raw = _closed_mapping(value, _CONFLICT_FIELDS, label="ConflictPackage")
    if isinstance(raw["conflictVersion"], bool) or raw["conflictVersion"] != 1:
        raise ContractValidationError("ConflictPackage version is unsupported")
    if raw["workflowId"] != expected_workflow_id:
        raise ContractValidationError("ConflictPackage workflowId does not match")
    if raw["taskId"] != expected_task_id:
        raise ContractValidationError("ConflictPackage taskId does not match")
    kind = raw["kind"]
    risk = raw["risk"]
    if kind not in _CONFLICT_KINDS:
        raise ContractValidationError("ConflictPackage kind is invalid")
    if risk not in {item.value for item in AssignmentRisk}:
        raise ContractValidationError("ConflictPackage risk is invalid")

    question_raw = _closed_mapping(
        raw["question"],
        _CONFLICT_QUESTION_FIELDS,
        label="conflict question",
    )
    choices = _string_list(
        question_raw["choices"],
        label="conflict question.choices",
    )
    if not 2 <= len(choices) <= 5:
        raise ContractValidationError(
            "conflict question must have two to five choices"
        )
    question = {
        "questionId": _prefixed_id(
            question_raw["questionId"],
            label="conflict questionId",
            prefix="question",
        ),
        "prompt": _text(question_raw["prompt"], label="conflict question.prompt"),
        "choices": list(choices),
    }

    claims_raw = raw["claims"]
    if (
        not isinstance(claims_raw, list)
        or not 2 <= len(claims_raw) <= 20
    ):
        raise ContractValidationError("ConflictPackage claims must be bounded")
    claims: list[dict[str, object]] = []
    claim_ids: set[str] = set()
    claimed_choices: set[str] = set()
    for index, item in enumerate(claims_raw):
        claim = _closed_mapping(
            item,
            _CONFLICT_CLAIM_FIELDS,
            label=f"conflict claim[{index}]",
        )
        claim_id = _prefixed_id(
            claim["claimId"],
            label="conflict claimId",
            prefix="claim",
        )
        if claim_id in claim_ids:
            raise ContractValidationError("conflict claim id is duplicated")
        choice = claim["choice"]
        if choice not in choices:
            raise ContractValidationError("conflict claim choice is invalid")
        evidence_refs = _string_list(
            claim["evidenceRefs"],
            label="conflict claim.evidenceRefs",
        )
        if not evidence_refs:
            raise ContractValidationError("conflict claim requires evidence")
        claim_ids.add(claim_id)
        claimed_choices.add(str(choice))
        claims.append(
            {
                "claimId": claim_id,
                "choice": choice,
                "statement": _text(
                    claim["statement"],
                    label="conflict claim.statement",
                ),
                "evidenceRefs": list(evidence_refs),
            }
        )
    if len(claimed_choices) < 2:
        raise ContractValidationError("ConflictPackage does not contain a conflict")

    vote_eligible = raw["voteEligible"]
    if not isinstance(vote_eligible, bool):
        raise ContractValidationError("ConflictPackage voteEligible must be boolean")
    if vote_eligible and (
        risk != "low" or kind in {"requirement", "verification"}
    ):
        raise ContractValidationError(
            "ConflictPackage voting is not eligible for this risk or kind"
        )
    result = {
        "conflictVersion": 1,
        "resultType": "ConflictPackage.v1",
        "conflictId": _prefixed_id(
            raw["conflictId"],
            label="conflictId",
            prefix="conflict",
        ),
        "workflowId": expected_workflow_id,
        "taskId": expected_task_id,
        "kind": kind,
        "risk": risk,
        "question": question,
        "claims": claims,
        "evidenceDigest": _digest(
            raw["evidenceDigest"],
            label="ConflictPackage evidenceDigest",
        ),
        "voteEligible": vote_eligible,
    }
    return _bounded_contract_document(
        result,
        label="ConflictPackage",
    )


def _parse_adjudication_result(
    assignment: Assignment,
    value: object,
    *,
    conflict_package: Mapping[str, object] | None,
) -> dict[str, object]:
    if conflict_package is None:
        raise ContractValidationError(
            "AdjudicationVerdict requires a frozen ConflictPackage"
        )
    conflict_id = conflict_package.get("conflictId")
    if (
        conflict_package.get("resultType") != "ConflictPackage.v1"
        or not isinstance(conflict_id, str)
        or conflict_id not in assignment.input_refs
    ):
        raise ContractValidationError(
            "Adjudication assignment is not bound to ConflictPackage"
        )
    if (
        conflict_package.get("workflowId") != assignment.workflow_id
        or conflict_package.get("taskId") != assignment.task_id
    ):
        raise ContractValidationError(
            "ConflictPackage identity does not match adjudication assignment"
        )
    raw = _closed_mapping(
        value,
        _ADJUDICATION_FIELDS,
        label="AdjudicationVerdict",
    )
    if isinstance(raw["resultVersion"], bool) or raw["resultVersion"] != 1:
        raise ContractValidationError("AdjudicationVerdict version is unsupported")
    if raw["assignmentId"] != assignment.assignment_id:
        raise ContractValidationError(
            "AdjudicationVerdict assignmentId does not match"
        )
    if raw["conflictId"] != conflict_id:
        raise ContractValidationError(
            "AdjudicationVerdict conflictId does not match"
        )
    decision = raw["decision"]
    confidence = raw["confidence"]
    if decision not in {
        "select_a",
        "select_b",
        "synthesize",
        "insufficient",
        "human_required",
    }:
        raise ContractValidationError("AdjudicationVerdict decision is invalid")
    if confidence not in _CONFIDENCE:
        raise ContractValidationError("AdjudicationVerdict confidence is invalid")

    claims = conflict_package.get("claims")
    if not isinstance(claims, list):
        raise ContractValidationError("ConflictPackage claims are invalid")
    package_claims = {
        str(item.get("claimId")): item
        for item in claims
        if isinstance(item, Mapping)
    }
    package_evidence = {
        str(ref)
        for item in claims
        if isinstance(item, Mapping)
        for ref in item.get("evidenceRefs", [])
    }
    resolved_raw = raw["resolvedClaims"]
    if not isinstance(resolved_raw, list) or len(resolved_raw) > 20:
        raise ContractValidationError("resolved claims must be bounded")
    resolved: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, item in enumerate(resolved_raw):
        resolution = _closed_mapping(
            item,
            _RESOLVED_CLAIM_FIELDS,
            label=f"resolved claim[{index}]",
        )
        claim_id = _prefixed_id(
            resolution["claimId"],
            label="resolved claimId",
            prefix="claim",
        )
        if claim_id in seen or claim_id not in package_claims:
            raise ContractValidationError("resolved claim is unknown or duplicated")
        evidence_refs = _string_list(
            resolution["evidenceRefs"],
            label="resolved claim.evidenceRefs",
        )
        if not evidence_refs or any(
            ref not in package_evidence for ref in evidence_refs
        ):
            raise ContractValidationError(
                "resolved claim evidence is outside ConflictPackage"
            )
        seen.add(claim_id)
        resolved.append(
            {
                "claimId": claim_id,
                "resolution": _text(
                    resolution["resolution"],
                    label="resolved claim.resolution",
                ),
                "evidenceRefs": list(evidence_refs),
            }
        )
    if decision in {"select_a", "select_b", "synthesize"} and not resolved:
        raise ContractValidationError(
            "decisive adjudication requires resolved claims"
        )
    return {
        "resultVersion": 1,
        "resultType": "AdjudicationVerdict.v1",
        "assignmentId": assignment.assignment_id,
        "conflictId": conflict_id,
        "decision": decision,
        "resolvedClaims": resolved,
        "conditions": list(
            _string_list(raw["conditions"], label="adjudication conditions")
        ),
        "confidence": confidence,
    }


MAX_ROLE_PAYLOAD_REPAIR_BYTES = max(MAX_ANALYST_REPORT_BYTES, MAX_ROLE_CONTRACT_BYTES)
_REPAIR_INVISIBLE_CHARS = ("\ufeff", "\u200b", "\u200c", "\u200d", "\u2060")
_REPAIR_MAX_DEPTH = 64
_REPAIR_MAX_CANDIDATES = 64


def _balanced_json_object_spans(text: str) -> Iterator[str]:
    """Yield the first bounded set of string-aware balanced ``{...}`` spans."""
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    found = 0
    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start = index
                depth = 1
                in_string = False
                escaped = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
            if depth > _REPAIR_MAX_DEPTH:
                # Pathological nesting: abandon this candidate, keep scanning.
                start = None
                depth = 0
        elif char == "}":
            depth -= 1
            if depth == 0:
                yield text[start : index + 1]
                found += 1
                if found >= _REPAIR_MAX_CANDIDATES:
                    return
                start = None


def repair_role_payload(text: object) -> str | None:
    """Repair only format noise around one role result JSON document.

    Strips a BOM/zero-width characters and, when the whole text still is not
    a JSON object, extracts the first balanced ``{...}`` span (skipping
    markdown fences and surrounding prose) that parses as a JSON object.
    Returns the repaired candidate, or ``None`` when no bounded repair
    applies; oversized or non-string inputs are refused unchanged.  The
    caller must still run the unchanged strict ``parse_role_result``
    validation, so semantic contract failures stay fail-closed.
    """

    if not isinstance(text, str):
        return None
    if len(text.encode("utf-8", errors="ignore")) > MAX_ROLE_PAYLOAD_REPAIR_BYTES:
        return None
    stripped = text
    for char in _REPAIR_INVISIBLE_CHARS:
        if char in stripped:
            stripped = stripped.replace(char, "")
    candidates = [stripped.strip()]
    candidates.extend(_balanced_json_object_spans(stripped))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            if isinstance(json.loads(candidate), dict):
                return candidate
        except ValueError:
            continue
    return None


def parse_role_result(
    assignment: Assignment,
    value: object,
    *,
    implementation_order: object | None = None,
    conflict_package: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Validate one role-specific payload against its immutable Assignment."""

    if not isinstance(assignment, Assignment):
        raise TypeError("assignment must be an Assignment")
    if assignment.role is WorkerRole.ANALYST:
        result = _parse_analyst_result(assignment, value)
    elif assignment.role is WorkerRole.EXECUTOR:
        result = _parse_execution_result(
            assignment,
            value,
            implementation_order=(
                implementation_order
                if isinstance(implementation_order, ImplementationOrder)
                else None
            ),
        )
    elif assignment.role is WorkerRole.REVIEWER:
        result = _parse_review_result(assignment, value)
    elif assignment.role is WorkerRole.ADJUDICATOR:
        result = _parse_adjudication_result(
            assignment,
            value,
            conflict_package=conflict_package,
        )
    else:
        raise ContractValidationError("role result contract is not implemented")
    return _bounded_contract_document(
        result,
        label=assignment.expected_result_type.removesuffix(".v1"),
        maximum_bytes=(
            MAX_ANALYST_REPORT_BYTES
            if assignment.role is WorkerRole.ANALYST
            else MAX_ROLE_CONTRACT_BYTES
        ),
    )


def analyst_report_requires_reclassification(
    assignment: Assignment,
    report: Mapping[str, object],
) -> bool:
    """Whether validated Analyst risks exceed the locked Assignment risk."""
    if not isinstance(assignment, Assignment) or assignment.role is not WorkerRole.ANALYST:
        raise TypeError("assignment must be an Analyst Assignment")
    if not isinstance(report, Mapping) or not isinstance(report.get("risks"), list):
        raise TypeError("report must be a validated AnalystReport")
    assigned_rank = _RISK_ORDER[assignment.risk.value]
    return any(
        isinstance(risk, Mapping)
        and isinstance(risk.get("severity"), str)
        and _RISK_ORDER.get(str(risk["severity"]), -1) > assigned_rank
        for risk in report["risks"]
    )


__all__ = [
    "ASSIGNMENT_VERSION",
    "AcceptanceCriterion",
    "Assignment",
    "AssignmentBudget",
    "AssignmentConstraint",
    "AssignmentRisk",
    "AssignmentScope",
    "ContractValidationError",
    "ImplementationOrder",
    "ImplementationStep",
    "MAX_ANALYST_EVIDENCE_SNAPSHOTS",
    "MAX_ANALYST_REPORT_BYTES",
    "MAX_ROLE_CONTRACT_BYTES",
    "MAX_ROLE_PAYLOAD_REPAIR_BYTES",
    "MAX_WORKFLOW_CRITERIA",
    "MAX_WORKFLOW_CRITERION_CHECK_LINKS",
    "REVIEW_COVERAGE_CONSTRAINT_ID",
    "REVIEW_COVERAGE_CONSTRAINT_TEXT",
    "WorkflowRequest",
    "WorkflowVerificationCheck",
    "WorkerRole",
    "analyst_report_requires_reclassification",
    "parse_assignment",
    "parse_executor_declaration",
    "parse_execution_candidate",
    "implementation_receipt_ref",
    "validate_executor_declaration",
    "parse_conflict_package",
    "parse_implementation_order",
    "parse_role_result",
    "parse_review_coverage_evidence",
    "parse_review_probe_correction",
    "review_probe_correction_artifact_id",
    "parse_verification_result",
    "parse_workflow_request",
    "repair_role_payload",
]
