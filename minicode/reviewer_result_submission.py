"""One assignment-bound review decision, without model-owned transport fields.

Evidence references identify the frozen material supporting a model judgment;
binding them never asserts that the model read it or that a passing check proves
semantic coverage. The scheduler still owns repair routing and final promotion.

The public native interface separates blocking findings from observations.
Legacy ``findings`` is only a blocking alias; its text is never classified or
silently moved to a nonblocking field. Explicit observations are retained as
labelled low-severity warnings in the unchanged ReviewVerdict.v1 contract.

The adapter supplies an already parsed object. This capability does not accept
or repair JSON text; duplicate raw argument keys must be rejected by the adapter
before parsing, since their original occurrences are unavailable here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from weakref import WeakKeyDictionary

from minicode.capability_contracts import (
    CapabilityDomain, CapabilityEffects, CapabilityProfile, CapabilityScope,
)
from minicode.multi_agent_contracts import (
    Assignment, MAX_ROLE_CONTRACT_BYTES, WorkerRole, parse_role_result, repair_role_payload,
)
from minicode.scheduler_artifacts import AssignmentArtifactChannel, SchedulerArtifactError
from minicode.tooling import ToolDefinition, ToolResult


_SUBMISSION_CHANNELS = WeakKeyDictionary()
_SUBMISSION_BINDINGS = WeakKeyDictionary()


def _channel_live(channel) -> bool:
    """Inspect the existing write reservation; never create or renew authority."""
    store = getattr(channel, "_store", None)
    if store is None:
        return False
    with store._lock:
        authority = getattr(channel, "_write_authority", None)
        return (
            not getattr(channel, "_written", True)
            and authority is not None
            and store._assignment_writes.get(authority) == channel.write_key
        )


def reviewer_submission_rejected(tool: ToolDefinition, result: ToolResult) -> bool:
    """Only a live host-bound rejection can reopen the same native request view."""
    channel = _SUBMISSION_CHANNELS.get(tool.run) if tool.name == "review_submit" else None
    return (
        channel is not None and not result.ok
        and result._review_submission_rejected is True
        and _channel_live(channel)
    )


def reviewer_submission_needs_evidence(tool: ToolDefinition, result: ToolResult) -> bool:
    """A live host pending receipt resumes inspection under unchanged authority."""
    channel = (_SUBMISSION_CHANNELS.get(tool.run)
               if tool.name in {"review_submit", "subagent_note_write"} else None)
    return (
        channel is not None and _channel_live(channel)
        and result._review_evidence_incomplete is True
        and not result._implementation_submitted
        and (result.ok or reviewer_submission_rejected(tool, result))
    )


def reviewer_submission_same_channel(candidate: ToolDefinition, native: ToolDefinition) -> bool:
    """Recognize only host-bound tools sharing the current live result channel."""
    channel = _SUBMISSION_CHANNELS.get(native.run) if native.name == "review_submit" else None
    return (
        channel is not None and _channel_live(channel)
        and candidate.name in {"review_submit", "subagent_note_write"}
        and _SUBMISSION_CHANNELS.get(candidate.run) is channel
    )


def _pending_result(declaration: dict, context, channel, guard) -> ToolResult | None:
    if guard is None:
        return None
    pending = guard(declaration, context)
    # The callback never creates authority. In particular, revocation during
    # inspection cannot produce a receipt that reopens the tool surface.
    if getattr(context, "_subagent_mailbox", None) is not channel:
        return ToolResult(ok=False, output="error[review_submit_owner]: capability belongs to another Assignment")
    if not _channel_live(channel):
        return ToolResult(ok=False, output="error[review_submit_closed]: result capability is unavailable")
    if pending is None:
        return None
    if not isinstance(pending, dict):
        raise ValueError("Reviewer continuation guard must return an object or None")
    return ToolResult(ok=True, output=_encoded(pending), _review_evidence_incomplete=True)


def bind_reviewer_legacy_submission_tool(
    native: ToolDefinition, original: ToolDefinition,
) -> ToolDefinition:
    """Apply the same pending decision before legacy notes consume the result key.

    This does not approve a legacy verdict: the existing strict result and
    pre-commit coverage gates still inspect every published final declaration.
    Other note keys and invalid legacy payloads retain their original behavior.
    """
    binding = _SUBMISSION_BINDINGS.get(native.run) if native.name == "review_submit" else None
    if binding is None or original.name != "subagent_note_write":
        raise ValueError("legacy review submission requires a bound native tool and note writer")
    assignment, channel, guard = binding

    def run(value, context):
        if getattr(context, "_subagent_mailbox", None) is not channel:
            return ToolResult(ok=False, output="error[review_submit_owner]: capability belongs to another Assignment")
        if not _channel_live(channel):
            return ToolResult(ok=False, output="error[review_submit_closed]: result capability is unavailable")
        if guard is not None and value.get("key") == channel.write_key:
            content = value.get("content")
            try:
                declaration = parse_role_result(assignment, json.loads(content))
            except (ValueError, TypeError):
                repaired = repair_role_payload(content)
                try:
                    declaration = parse_role_result(assignment, json.loads(repaired))
                except (ValueError, TypeError):
                    return original.run(value, context)
            try:
                pending = _pending_result(declaration, context, channel, guard)
            except (ValueError, TypeError, SchedulerArtifactError) as error:
                return ToolResult(ok=False, output=f"error[review_submit_invalid]: {error}")
            if pending is not None:
                return pending
        return original.run(value, context)

    tool = replace(original, run=run)
    _SUBMISSION_CHANNELS[run] = channel
    return tool


def _closed(value: object, fields: tuple[str, ...], label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    missing = [field for field in fields if field not in value]
    unknown = [str(field) for field in value if field not in fields]
    errors = []
    if missing:
        errors.append("missing required fields: " + ", ".join(missing))
    if unknown:
        errors.append("unknown fields: " + ", ".join(sorted(unknown)))
    if errors:
        raise ValueError(label + " " + "; ".join(errors))
    return value


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be a nonempty string without NUL")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds the {maximum}-character limit")
    return value


def _items(value: object, label: str, maximum: int = 40) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a list of at most {maximum} items")
    return value


def _strings(value: object, label: str, maximum: int = 500) -> list[str]:
    result = [_text(item, label, maximum) for item in _items(value, label)]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contains a duplicate")
    return result


def _encoded(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_ROLE_CONTRACT_BYTES:
        raise ValueError("Reviewer submission exceeds the byte limit")
    return encoded


def create_reviewer_submit_tool(
    assignment: Assignment,
    channel: AssignmentArtifactChannel,
    *,
    patch_digest: str,
    matrix: dict,
    matrix_ref: str,
    diff_ref: str,
    coverage_ref: str | None = None,
    coverage_guard: Callable[[dict, dict, object], None] | None = None,
    continuation_guard: Callable[[dict, object], dict | None] | None = None,
) -> ToolDefinition:
    """Bind one strict native submission to immutable scheduler evidence."""
    if not isinstance(assignment, Assignment) or assignment.role is not WorkerRole.REVIEWER:
        raise ValueError("submission requires a Reviewer Assignment")
    if not isinstance(patch_digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", patch_digest) is None:
        raise ValueError("Reviewer patch digest is invalid")
    readable = set(assignment.input_refs) & set(channel.readable_keys)

    def read_document(ref: str) -> dict:
        if not isinstance(ref, str) or ref not in readable:
            raise ValueError("Reviewer evidence is not readable by this Assignment")
        try:
            artifact = channel.read(ref)
            document = json.loads(artifact.content) if artifact is not None else None
        except (ValueError, TypeError, SchedulerArtifactError) as error:
            raise ValueError("Reviewer evidence is unavailable") from error
        if not isinstance(document, dict):
            raise ValueError("Reviewer evidence must be a published object")
        return document

    frozen_matrix = read_document(matrix_ref)
    if frozen_matrix != matrix:
        raise ValueError("Reviewer matrix does not match the frozen artifact")
    if (
        type(frozen_matrix.get("matrixVersion")) is not int
        or frozen_matrix.get("matrixVersion") != 1
        or frozen_matrix.get("resultType") != "AcceptanceEvidenceMatrix.v1"
        or frozen_matrix.get("workflowId") != assignment.workflow_id
        or frozen_matrix.get("taskId") != assignment.task_id
        or frozen_matrix.get("patchDigest") != patch_digest
        or frozen_matrix.get("frozenDiffRef") != diff_ref
        or matrix_ref == diff_ref
    ):
        raise ValueError("Reviewer matrix identity or frozen diff binding is invalid")
    if read_document(diff_ref).get("patchDigest") != patch_digest:
        raise ValueError("Reviewer frozen diff digest does not match")
    read_document(frozen_matrix.get("verificationArtifactRef"))
    criterion_ids = tuple(item.criterion_id for item in assignment.acceptance_criteria)
    bound_refs: dict[str, tuple[str, ...]] = {}
    for item in _items(frozen_matrix.get("criteria"), "matrix criteria"):
        row = _closed(item, ("criterionId", "checkIds", "evidenceRefs"), "matrix criterion")
        cid = _text(row["criterionId"], "matrix criterionId", 80)
        if cid not in criterion_ids or cid in bound_refs:
            raise ValueError("Reviewer matrix criterion is unknown or duplicated")
        checks = _strings(row["checkIds"], "matrix checkIds")
        refs = _strings(row["evidenceRefs"], "matrix evidenceRefs")
        if len(checks) != len(refs):
            raise ValueError("Reviewer matrix check/evidence mapping is incomplete")
        for ref in refs:
            read_document(ref)
        bound_refs[cid] = tuple(dict.fromkeys((matrix_ref, diff_ref, *refs)))
    if set(bound_refs) != set(criterion_ids):
        raise ValueError("Reviewer matrix must account for every criterion")

    def declaration_from(value: object) -> dict:
        if isinstance(value, dict) and "findings" in value and "blockingFindings" in value:
            raise ValueError("use blockingFindings or its legacy findings alias, never both")
        blocking_key = "findings" if isinstance(value, dict) and "findings" in value else "blockingFindings"
        optional = tuple(name for name in ("observations", "requestedChecks")
                         if isinstance(value, dict) and name in value)
        raw = _closed(value, ("verdict", "criteria", blocking_key, "warnings", "confidence") + optional, "Reviewer submission")
        _encoded(raw)
        verdict = raw["verdict"]
        if not isinstance(verdict, str) or verdict not in ("approved", "changes_required", "inconclusive"):
            raise ValueError("Reviewer verdict is invalid")
        if not isinstance(raw["confidence"], str) or raw["confidence"] not in ("high", "medium", "low"):
            raise ValueError("Reviewer confidence is invalid")
        explicit: dict[str, str] = {}
        for item in _items(raw["criteria"], "criteria"):
            extra = tuple(name for name in ("rationale", "probeIds", "sourceEvidence")
                          if coverage_guard is not None and isinstance(item, dict) and name in item)
            row = _closed(item, ("criterionId", "status") + extra, "review criterion")
            cid = _text(row["criterionId"], "criterionId", 80)
            if cid not in bound_refs or cid in explicit:
                raise ValueError("review criterion is unknown or duplicated")
            status = row["status"]
            if not isinstance(status, str) or status not in ("satisfied", "failed", "unknown"):
                raise ValueError("review criterion status is invalid")
            explicit[cid] = status
        blocking_values = _items(raw[blocking_key], "blockingFindings")
        if verdict == "approved" and blocking_values:
            raise ValueError(
                "approved requires empty blockingFindings (legacy findings is also blocking); "
                "retain any real defect as blocking and choose changes_required"
            )
        findings = []
        found: set[str] = set()
        seen_findings: set[tuple[tuple[str, ...], str]] = set()
        for item in blocking_values:
            row = _closed(item, ("criterionIds", "statement"), "finding")
            ids = _strings(row["criterionIds"], "finding criterionIds", 80)
            if (not ids or any(cid not in bound_refs for cid in ids)
                    or (verdict != "changes_required"
                        and not any(explicit.get(cid) == "failed" for cid in ids))):
                raise ValueError("each finding must reference known criteria including an explicitly failed criterion")
            statement = _text(row["statement"], "finding statement", 4000)
            signature = (tuple(sorted(ids)), statement)
            if signature in seen_findings:
                raise ValueError("finding is duplicated")
            seen_findings.add(signature)
            found.update(ids)
            findings.append({
                "id": f"finding_{len(findings) + 1}", "severity": "high",
                # ReviewVerdict.v1 has no criterionIds field. Preserve that
                # association in the finding itself for the repair recipient.
                "statement": _text("[" + ", ".join(ids) + "] " + statement, "bound finding statement", 4000),
                "evidenceRefs": list(dict.fromkeys(ref for cid in ids for ref in bound_refs[cid])),
            })
        if any(status == "failed" and cid not in found for cid, status in explicit.items()):
            raise ValueError("each failed criterion requires a concrete finding")
        warnings = _strings(raw["warnings"], "warnings", 4000)
        observations = []
        seen_observations: set[tuple[tuple[str, ...], str]] = set()
        for item in _items(raw.get("observations", []), "observations"):
            row = _closed(item, ("criterionIds", "statement"), "observation")
            ids = _strings(row["criterionIds"], "observation criterionIds", 80)
            if not ids or any(cid not in bound_refs for cid in ids):
                raise ValueError("each observation must reference known criteria")
            statement = _text(row["statement"], "observation statement", 4000)
            signature = (tuple(sorted(ids)), statement)
            if signature in seen_observations:
                raise ValueError("observation is duplicated")
            seen_observations.add(signature)
            observations.append({
                "id": f"observation_{len(observations) + 1}", "severity": "low",
                "statement": _text("[observation: " + ", ".join(ids) + "] " + statement,
                                   "bound observation statement", 4000),
                "evidenceRefs": list(dict.fromkeys(ref for cid in ids for ref in bound_refs[cid])),
            })
        if len(warnings) + len(observations) > 40:
            raise ValueError("warnings and observations together must contain at most 40 items")
        # Only an omitted native field has an empty default. Explicit values
        # still undergo the same validation and approval gate; the canonical
        # ReviewVerdict always contains this field, including the empty list.
        checks = _strings(raw.get("requestedChecks", []), "requestedChecks")
        if verdict == "approved" and (
            set(explicit) != set(criterion_ids)
            or any(status != "satisfied" for status in explicit.values())
            or findings or checks
        ):
            raise ValueError("approved requires every criterion explicitly satisfied and no findings or requested checks")
        if verdict == "changes_required" and (
            not findings or "failed" not in explicit.values()
        ):
            raise ValueError("changes_required requires an explicitly failed criterion and concrete finding")
        declaration = {
            "resultVersion": 1, "assignmentId": assignment.assignment_id,
            "patchDigest": patch_digest, "verdict": verdict,
            "criteria": [{
                "criterionId": cid, "status": explicit.get(cid, "unknown"),
                "evidenceRefs": list(bound_refs[cid]) if explicit.get(cid, "unknown") != "unknown" else [],
            } for cid in criterion_ids],
            "blockingFindings": findings,
            "warnings": [{
                "id": f"warning_{index + 1}", "severity": "medium", "statement": statement,
                "evidenceRefs": [matrix_ref, diff_ref],
            } for index, statement in enumerate(warnings)] + observations,
            "requestedChecks": checks, "confidence": raw["confidence"],
        }
        if verdict == "approved" and coverage_guard is not None:
            if not coverage_ref:
                raise ValueError("host review coverage capability is unavailable")
            declaration["coverageArtifactRef"] = coverage_ref
        # Preserve the closed input form in the channel; parse_role_result adds
        # a resultType to its returned projection, which is not an input field.
        parse_role_result(assignment, declaration)
        _encoded(declaration)
        return declaration

    def validate(value: object):
        # Complete validation runs after ownership checks so a real semantic
        # rejection can carry a host-only retry receipt through ToolRegistry.
        return value

    def run(value: object, context) -> ToolResult:
        if getattr(context, "_subagent_mailbox", None) is not channel:
            return ToolResult(ok=False, output="error[review_submit_owner]: capability belongs to another Assignment")
        if not _channel_live(channel):
            return ToolResult(ok=False, output="error[review_submit_closed]: result capability is unavailable")
        try:
            declaration = declaration_from(value)
        except (ValueError, TypeError, SchedulerArtifactError) as error:
            return ToolResult(ok=False, output=f"error[review_submit_invalid]: {error}",
                              _review_submission_rejected=_channel_live(channel))
        if declaration["verdict"] == "approved" and coverage_guard is not None:
            try:
                coverage_guard(value, declaration, context)
            except (ValueError, TypeError, SchedulerArtifactError) as error:
                return ToolResult(ok=False, output=f"error[review_evidence_incomplete]: {error}",
                                  _review_submission_rejected=_channel_live(channel),
                                  _review_evidence_incomplete=_channel_live(channel))
        try:
            pending = _pending_result(
                parse_role_result(assignment, declaration), context, channel, continuation_guard,
            )
        except (ValueError, TypeError, SchedulerArtifactError) as error:
            return ToolResult(ok=False, output=f"error[review_submit_invalid]: {error}",
                              _review_submission_rejected=_channel_live(channel))
        if pending is not None:
            return pending
        try:
            channel.write(channel.write_key, _encoded(declaration), author="reviewer")
        except SchedulerArtifactError as error:
            return ToolResult(ok=False, output=f"error[review_submit_invalid]: {error}")
        return ToolResult(
            ok=True, output='{"reviewReceived":true,"writeback":"pending"}',
            _implementation_submitted=True,
        )

    coverage_properties = ({
        "rationale": {"type": "string", "minLength": 1, "maxLength": 1200,
                      "description": "Explain how the cited branch/assertions establish this particular requirement; green test totals alone are insufficient."},
        "probeIds": {"type": "array", "uniqueItems": True,
                     "items": {"type": "string"},
                     "description": "IDs returned by review_test that exercise this criterion on the current candidate."},
        "sourceEvidence": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"path": {"type": "string"},
                           "startLine": {"type": "integer", "minimum": 1},
                           "endLine": {"type": "integer", "minimum": 1}},
            "required": ["path", "startLine", "endLine"],
        }, "description": "Exact current source ranges supporting inspection-only requirements such as scope, compatibility or process constraints. Host validates and hashes the file."},
    } if coverage_guard is not None else {})
    tool = ToolDefinition(
        name="review_submit",
        description=(
            "Submit this Reviewer's decision once and finish. For changes_required, "
            "submit confirmed failed criteria with concrete findings; omitted criteria "
            "remain unknown. Approval requires every criterion explicitly satisfied. "
            "Host binds the Assignment, frozen diff and original evidence mapping; "
            "these references do not prove coverage or that you inspected the evidence."
            + (" Approval additionally requires independent review_test evidence and, for every "
               "criterion, rationale plus probeIds and/or exact sourceEvidence. Failed review tests "
               "cannot be ignored; return concrete changes_required to the same Executor."
               if coverage_guard is not None else "")
        ),
        input_schema={
            "type": "object", "additionalProperties": False,
            "properties": {
                "verdict": {"type": "string", "enum": ["approved", "changes_required", "inconclusive"]},
                "criteria": {"type": "array", "maxItems": len(criterion_ids), "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "criterionId": {"type": "string", "enum": list(criterion_ids)},
                        "status": {"type": "string", "enum": ["satisfied", "failed", "unknown"]},
                        **coverage_properties,
                    }, "required": ["criterionId", "status"],
                }},
                "blockingFindings": {"type": "array", "maxItems": 40,
                    "description": "Defects that block approval; approved requires an empty list.", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "criterionIds": {"type": "array", "minItems": 1, "maxItems": len(criterion_ids), "uniqueItems": True,
                                         "items": {"type": "string", "enum": list(criterion_ids)}},
                        "statement": {"type": "string", "minLength": 1, "maxLength": 4000},
                    }, "required": ["criterionIds", "statement"],
                }},
                "observations": {"type": "array", "maxItems": 40,
                    "description": "Nonblocking review observations, including positive evidence; these do not satisfy criteria automatically.",
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "criterionIds": {"type": "array", "minItems": 1, "maxItems": len(criterion_ids), "uniqueItems": True,
                                             "items": {"type": "string", "enum": list(criterion_ids)}},
                            "statement": {"type": "string", "minLength": 1, "maxLength": 4000},
                        }, "required": ["criterionIds", "statement"],
                    }},
                "warnings": {"type": "array", "maxItems": 40, "uniqueItems": True,
                             "items": {"type": "string", "minLength": 1, "maxLength": 4000}},
                "requestedChecks": {"type": "array", "maxItems": 40, "uniqueItems": True, "default": [],
                                    "items": {"type": "string", "minLength": 1, "maxLength": 500}},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["verdict", "criteria", "blockingFindings", "warnings", "confidence"],
        },
        validator=validate, run=run,
        capability_profile=CapabilityProfile(
            domain=CapabilityDomain.COMMUNICATION, scope=CapabilityScope.WRITE,
            effects=CapabilityEffects(read_only=False, destructive=False, external=False, executes_code=False, delegates=False),
            purposes=("Submit one assignment-bound review decision",),
        ),
    )
    _SUBMISSION_CHANNELS[run] = channel
    _SUBMISSION_BINDINGS[run] = (assignment, channel, continuation_guard)
    return tool


__all__ = [
    "create_reviewer_submit_tool", "reviewer_submission_rejected", "reviewer_submission_needs_evidence",
    "reviewer_submission_same_channel", "bind_reviewer_legacy_submission_tool",
]
