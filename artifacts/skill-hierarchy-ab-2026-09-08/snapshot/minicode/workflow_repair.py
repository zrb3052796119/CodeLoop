"""Closed host-owned authority for bounded implementation repair requests."""

from __future__ import annotations

from collections.abc import Mapping
import re


MAX_IMPLEMENTATION_REPAIRS = 2
REPAIR_REQUEST_TYPE = "ImplementationRepairRequest.v1"
REPAIR_TRANSITION_ERROR = "implementation_repair_requested"
_FIELDS = frozenset({
    "repairVersion", "resultType", "workflowId", "taskId", "assignmentId",
    "repairIndex", "sourceStage", "sourceArtifactRef", "requestArtifactRef",
    "previousExecutionArtifactRef", "previousOrderRef", "previousDiffArtifactRef",
    "previousPatchDigest", "previousVerificationArtifactRef", "evidenceRefs",
})
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$")


def parse_implementation_repair_request(value: object) -> dict[str, object]:
    """Validate shape; audit additionally binds every reference to its revision."""
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ValueError("implementation repair request fields are invalid")
    if value["repairVersion"] != 1 or type(value["repairVersion"]) is not int:
        raise ValueError("implementation repair request version is invalid")
    if value["resultType"] != REPAIR_REQUEST_TYPE:
        raise ValueError("implementation repair request type is invalid")
    if type(value["repairIndex"]) is not int or not (
        1 <= value["repairIndex"] <= MAX_IMPLEMENTATION_REPAIRS
    ):
        raise ValueError("implementation repair request index is invalid")
    if value["sourceStage"] not in {"verifier", "reviewer"}:
        raise ValueError("implementation repair source stage is invalid")
    for name in _FIELDS - {
        "repairVersion", "resultType", "repairIndex", "sourceStage",
        "previousPatchDigest", "evidenceRefs",
    }:
        if not isinstance(value[name], str) or not _IDENTIFIER.fullmatch(value[name]):
            raise ValueError(f"implementation repair {name} is invalid")
    if not isinstance(value["previousPatchDigest"], str) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", value["previousPatchDigest"]
    ) is None:
        raise ValueError("implementation repair patch digest is invalid")
    refs = value["evidenceRefs"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 40 or any(
        not isinstance(ref, str) or not _IDENTIFIER.fullmatch(ref) for ref in refs
    ) or len(set(refs)) != len(refs):
        raise ValueError("implementation repair evidence references are invalid")
    return {**value, "evidenceRefs": list(refs)}


def implementation_repair_input_refs(
    artifact_id: str, request: Mapping[str, object]
) -> tuple[str, ...]:
    """Exact additional Executor reads, placed before the new executable order."""
    return tuple(dict.fromkeys((
        artifact_id,
        str(request["sourceArtifactRef"]),
        str(request["previousExecutionArtifactRef"]),
        str(request["previousDiffArtifactRef"]),
        str(request["previousVerificationArtifactRef"]),
        *(str(ref) for ref in request["evidenceRefs"]),
    )))


def verification_supports_implementation_repair(
    result: object, evidence: list[object]
) -> bool:
    """Only completed failing commands can request an implementation revision."""
    if not isinstance(result, Mapping) or result.get("status") != "failed":
        return False
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) != len(evidence):
        return False
    return all(
        isinstance(check, Mapping)
        and check.get("kind") in {"tests", "build", "lint", "typecheck"}
        and check.get("status") in {"passed", "failed"}
        and type(check.get("exitCode")) is int
        and check["exitCode"] >= 0
        and isinstance(document, Mapping)
        and document.get("failureReason") is None
        and document.get("status") == check["status"]
        and document.get("exitCode") == check["exitCode"]
        for check, document in zip(checks, evidence)
    )


def review_supports_implementation_repair(result: object) -> bool:
    """Unknown criteria, new checks, and critical blockers require reassessment."""
    return (
        isinstance(result, Mapping)
        and result.get("verdict") == "changes_required"
        and not result.get("requestedChecks")
        and isinstance(result.get("criteria"), list)
        and bool(result["criteria"])
        and all(
            isinstance(item, Mapping) and item.get("status") in {"failed", "satisfied"}
            for item in result["criteria"]
        )
        and isinstance(result.get("blockingFindings"), list)
        and all(
            isinstance(item, Mapping) and item.get("severity") != "critical"
            for item in result["blockingFindings"]
        )
    )
