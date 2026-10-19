"""Closed host-owned authority for bounded implementation repair requests."""

from __future__ import annotations

from collections.abc import Mapping
import math
import re


MAX_IMPLEMENTATION_REPAIRS = 2
REPAIR_REQUEST_TYPE = "ImplementationRepairRequest.v1"
REPAIR_TRANSITION_ERROR = "implementation_repair_requested"
CONTINUATION_TRANSITION_ERROR = "implementation_continuation_requested"
_FIELDS = frozenset({
    "repairVersion", "resultType", "workflowId", "taskId", "assignmentId",
    "repairIndex", "sourceStage", "sourceArtifactRef", "requestArtifactRef",
    "previousExecutionArtifactRef", "previousOrderRef", "previousDiffArtifactRef",
    "previousPatchDigest", "previousVerificationArtifactRef", "evidenceRefs",
})
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$")
_CONTINUATION_FIELDS = frozenset({
    "continuationVersion", "sessionId", "limitModelCalls", "limitTotalTokens",
    "usedModelCalls", "usedTotalTokens", "reservedTotalTokens",
    "preserveModelCalls", "preserveTotalTokens", "projectedInputTokens",
    "outputTokens", "deadlineMonotonic", "admittedAtMonotonic",
})


def validate_continuation_admission(value: object) -> dict[str, object]:
    """Validate a host request quote, never create or extend spending authority.

    The owner still reserves every actual request on its original ledger.
    Audit additionally checks identity and monotonic consumption across quotes.
    """
    if not isinstance(value, Mapping) or set(value) != _CONTINUATION_FIELDS:
        raise ValueError("implementation continuation fields are invalid")
    if type(value["continuationVersion"]) is not int or value["continuationVersion"] != 1:
        raise ValueError("implementation continuation version is invalid")
    if not isinstance(value["sessionId"], str) or not _IDENTIFIER.fullmatch(value["sessionId"]):
        raise ValueError("implementation continuation session is invalid")
    optional_limits = {"limitModelCalls", "limitTotalTokens"}
    positive = optional_limits | {"outputTokens"}
    for name in positive | {
        "usedModelCalls", "usedTotalTokens", "reservedTotalTokens",
        "preserveModelCalls", "preserveTotalTokens", "projectedInputTokens",
    }:
        if name in optional_limits and value[name] is None:
            continue
        if type(value[name]) is not int or value[name] < (1 if name in positive else 0):
            raise ValueError(f"implementation continuation {name} is invalid")
    for name in ("deadlineMonotonic", "admittedAtMonotonic"):
        if name == "deadlineMonotonic" and value[name] is None:
            continue
        try:
            valid = type(value[name]) in (int, float) and math.isfinite(value[name])
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"implementation continuation {name} is invalid")
    if (value["admittedAtMonotonic"] < 0
            or value["deadlineMonotonic"] is not None
            and value["admittedAtMonotonic"] >= value["deadlineMonotonic"]):
        raise ValueError("implementation continuation deadline is exhausted")
    if (value["limitModelCalls"] is not None
            and value["usedModelCalls"] + 1 + value["preserveModelCalls"] > value["limitModelCalls"]):
        raise ValueError("implementation continuation calls are exhausted")
    if (value["limitTotalTokens"] is not None
            and value["usedTotalTokens"] + value["reservedTotalTokens"]
            + value["projectedInputTokens"] + value["outputTokens"]
            + value["preserveTotalTokens"] > value["limitTotalTokens"]):
        raise ValueError("implementation continuation tokens are exhausted")
    return dict(value)


def parse_implementation_repair_request(value: object) -> dict[str, object]:
    """Validate shape; audit additionally binds every reference to its revision."""
    if not isinstance(value, Mapping) or set(value) not in (_FIELDS, _FIELDS | {"continuation"}):
        raise ValueError("implementation repair request fields are invalid")
    continuation = (validate_continuation_admission(value["continuation"])
                    if "continuation" in value else None)
    if value["repairVersion"] != 1 or type(value["repairVersion"]) is not int:
        raise ValueError("implementation repair request version is invalid")
    if value["resultType"] != REPAIR_REQUEST_TYPE:
        raise ValueError("implementation repair request type is invalid")
    if (type(value["repairIndex"]) is not int or value["repairIndex"] < 1
            or continuation is None and value["repairIndex"] > MAX_IMPLEMENTATION_REPAIRS):
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
    return {**value, "evidenceRefs": list(refs),
            **({"continuation": continuation} if continuation is not None else {})}


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
    result: object, evidence: list[object], *, pending_write_feedback: bool = False,
) -> bool:
    """Completed checks can support repair, never acknowledge pending feedback.

    By default only real failed commands request a revision. A host-bound
    pending write or submission failure may also use entirely passing receipts:
    the tests stay passed, while the Executor must still resolve its tool error.
    The existing keyword names a host-validated pending cause, never model text.
    """
    if not isinstance(result, Mapping):
        return False
    passing_pending = pending_write_feedback is True and result.get("status") == "passed"
    if result.get("status") != "failed" and not passing_pending:
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
        and (not passing_pending or (
            check["status"] == "passed" and check["exitCode"] == 0
            and type(document.get("exitCode")) is int and document["exitCode"] == 0
        ))
        for check, document in zip(checks, evidence)
    )


def review_supports_implementation_repair(result: object) -> bool:
    """A known defect can be repaired before the rest of the review is done.

    Unknowns never authorize promotion. They may accompany a concrete failed
    criterion and finding so a Reviewer can return useful negative feedback
    immediately, instead of spending the repair budget completing an approval.
    The revised patch still needs a new, complete verification and review.
    Requested checks may accompany that defect as feedback to the Executor;
    they never authorize new commands or expand its existing write scope.
    """
    return (
        isinstance(result, Mapping)
        and result.get("verdict") in {"changes_required", "inconclusive"}
        and isinstance(result.get("criteria"), list)
        and bool(result["criteria"])
        and all(
            isinstance(item, Mapping) and item.get("status") in {"failed", "satisfied", "unknown"}
            for item in result["criteria"]
        )
        and isinstance(result.get("blockingFindings"), list)
        and all(
            isinstance(item, Mapping) and item.get("severity") != "critical"
            for item in result["blockingFindings"]
        )
        and (
            (result.get("verdict") == "changes_required"
             and not result.get("requestedChecks")
             and all(item.get("status") != "unknown" for item in result["criteria"]))
            or (
                any(item.get("status") == "failed" for item in result["criteria"])
                and any(isinstance(item.get("statement"), str) and item["statement"].strip()
                        for item in result["blockingFindings"])
            )
        )
    )
