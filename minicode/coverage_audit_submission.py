"""Immutable rejected coverage drafts and explicit model-authored edits.

Resolving a patch is not validation or acceptance. Every resolved proposal must
pass the same current evidence and semantic-submission gates as a full input.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from minicode.review_evidence_documents import canonical, content_digest


_PATH = re.compile(r"/(?:status|findings|requirements|supersessions)\Z|"
    r"/requirements/[0-9]+/(?:status|reason|witnesses|obligations)(?:/[0-9]+/(?:probeId|startLine|endLine|reason|witnesses))?\Z|"
    r"/supersessions/[0-9]+/(?:status|invalidityReason|requirements)"
    r"(?:/[0-9]+/(?:reason|witnesses)(?:/[0-9]+/(?:probeId|startLine|endLine))?)?\Z")


class CoverageDrafts:
    def __init__(self, bindings, audit_id):
        self.bindings = deepcopy(bindings)
        self.audit_id = audit_id
        self._drafts = {}
        self.latest = None

    def resolve(self, value):
        if not isinstance(value, dict):
            raise ValueError("coverage submission must be an object")
        if "draftId" not in value and "updates" not in value:
            return deepcopy(value)
        if (set(value) != {"draftId", "updates"} or not isinstance(value.get("draftId"), str)
                or value["draftId"] not in self._drafts):
            raise ValueError("draft patch requires a retained unaccepted draftId and updates")
        updates = value["updates"]
        if not isinstance(updates, list) or not updates:
            raise ValueError("draft updates must be a nonempty list of explicit path/value edits")
        candidate = deepcopy(self._drafts[value["draftId"]]["submission"])
        paths = set()
        for update in updates:
            if (not isinstance(update, dict) or set(update) != {"path", "value"}
                    or not isinstance(update["path"], str) or not _PATH.fullmatch(update["path"])
                    or update["path"] in paths):
                raise ValueError("draft update path is duplicate or outside editable coverage fields")
            path = update["path"]
            paths.add(path)
            parts = path.split("/")[1:]
            parent = candidate
            try:
                for part in parts[:-1]:
                    parent = parent[int(part)] if isinstance(parent, list) else parent[part]
                key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
                if isinstance(parent, dict) and key not in parent and len(parts) > 1:
                    raise KeyError(key)
                parent[key] = deepcopy(update["value"])
            except (KeyError, IndexError, TypeError, ValueError) as error:
                raise ValueError(f"draft update path does not exist: {path}") from error
        return candidate

    def retain(self, original_input, submission):
        identity = {"auditId": self.audit_id, **self.bindings,
                    "originalInput": original_input, "submission": submission}
        draft_id = "coverage_draft_" + content_digest(canonical(identity))[7:31]
        draft = {"draftVersion": 1, "status": "unaccepted", "draftId": draft_id, **deepcopy(identity)}
        self._drafts.setdefault(draft_id, draft)
        self.latest = draft_id
        return deepcopy(draft)

    def get(self, draft_id):
        return deepcopy(self._drafts[draft_id])

    def restore_rejection(self, original_input, output, *, rejection_record=None, tool_use_id=None):
        """Restore host state from authenticated originals, never display excerpts."""
        if rejection_record is not None:
            from minicode.coverage_audit_state import record_document
            document = record_document(rejection_record)
            expected = rejection_delivery_document(bindings=self.bindings, audit_id=self.audit_id,
                original_input=original_input, tool_use_id=tool_use_id,
                native_output=document.get("nativeOutput"), display_output=document.get("displayOutput"),
                delivered_output=output, projection=document.get("projection"))
            if document != expected:
                raise ValueError("coverage rejection delivery bindings or original bytes changed")
            output = document["nativeOutput"]
        result = json.JSONDecoder().raw_decode(output)[0]
        if not isinstance(result, dict) or result.get("errorCode") != "coverage_audit_invalid":
            return None
        if rejection_record is None and any(key in result for key in ("displayOnly", "documentRef", "originalDigest")):
            raise ValueError("coverage rejection display requires its host original binding")
        try:
            submission = self.resolve(original_input)
        except ValueError:
            if result.get("draftId") is not None:
                raise ValueError("retained coverage draft cannot be reconstructed") from None
            return result
        draft = self.retain(original_input, submission)
        if result.get("draftId", draft["draftId"]) != draft["draftId"]:
            raise ValueError("delivered coverage draft identity changed")
        return result


def rejection_view(value, views):
    """Bound the serialized view; originals remain losslessly addressable."""
    if len(_native_json(value)) <= 20000:
        return deepcopy(value)
    reference = views.reference(value, kind="coverage_rejection")
    result = {**reference,
        **{key: value[key] for key in ("errorCode", "draftId", "draftStatus") if key in value},
        "message": "Coverage submission rejected. Read the full diagnostic before correcting this unaccepted draft.",
        "fieldErrorCount": len(value.get("fieldErrors", [])),
        "missingDocumentCount": len(value.get("missingDocumentIds", [])),
        "readHint": "completion_evidence_read: open documentRef as documentId; follow nextCursor until null. Display pages grant no evidence-delivery credit."}
    # Include only whole errors which fit. Do not truncate JSON string bytes.
    preview = []
    for error in value.get("fieldErrors", []):
        trial = {**result, "fieldErrors": [*preview, error]}
        if len(_native_json(trial)) > 12000:
            break
        preview.append(error)
    result["fieldErrors"] = preview
    if len(_native_json(result)) > 20000:
        raise ValueError("coverage rejection identity exceeds the display envelope")
    return result


def _native_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _valid_host_envelope(display, delivered):
    # Both policies append these native notes after ToolRegistry execution. No
    # arbitrary suffix (or merely JSON-looking prefix) is trusted as delivery.
    from minicode.agent_intelligence import ErrorClassifier, NudgeGenerator
    from minicode.recovery_control import RecoveryGuard
    if not isinstance(delivered, str) or not delivered.startswith(display):
        return False
    suffix = delivered[len(display):]
    if not suffix:
        return True
    classified = ErrorClassifier.classify(display, tool_name="completion_coverage_audit_submit")
    retry = re.search(r" \(This is retry attempt ([1-9][0-9]*)\)", suffix)
    retry_count = int(retry.group(1)) - 1 if retry else 0
    first = "\n\n[System note: " + NudgeGenerator.generate(classified, retry_count=retry_count) + "]"
    if not suffix.startswith(first):
        return False
    rest = suffix[len(first):]
    if not rest:
        return True
    guard = RecoveryGuard()
    guard.complete_step([False])
    guard.complete_step([False])
    strategy = guard.complete_step([False])
    return rest == "\n\n[System note: " + strategy + "]"


def rejection_delivery_document(*, bindings, audit_id, original_input, tool_use_id,
                                native_output, display_output, delivered_output, projection):
    """Authenticate one actual rejected invocation, including host envelopes.

    The legacy policy is for explicit host recovery of archived truncation; it
    never claims that the full native diagnostic was visible to the model.
    """
    from minicode.completion_evidence_view import EvidenceViews
    from minicode.tooling import _LARGE_OUTPUT_THRESHOLD, _smart_truncate_output
    if (set(bindings) != {"candidateDigest", "objectiveDigest", "proposalDigest", "supersessionDigest"}
            or not isinstance(tool_use_id, str) or not tool_use_id
            or not isinstance(native_output, str) or not isinstance(display_output, str)):
        raise ValueError("coverage rejection requires its native invocation and bindings")
    value = json.loads(native_output)
    if (not isinstance(value, dict) or value.get("errorCode") != "coverage_audit_invalid"
            or _native_json(value) != native_output):
        raise ValueError("coverage rejection lacks the exact full native diagnostic")
    if projection == "native_view_v1":
        expected = _native_json(rejection_view(value, EvidenceViews(candidate_digest=bindings["candidateDigest"])))
    elif projection == "legacy_smart_truncation_v1":
        expected = (_smart_truncate_output(native_output, "completion_coverage_audit_submit")
                    if len(native_output) > _LARGE_OUTPUT_THRESHOLD else native_output)
    else:
        raise ValueError("unknown coverage rejection display policy")
    if display_output != expected or not _valid_host_envelope(display_output, delivered_output):
        raise ValueError("coverage rejection projection or host envelope changed")
    return {"rejectionDeliveryVersion": 1, "auditId": audit_id, **deepcopy(bindings),
        "toolName": "completion_coverage_audit_submit", "toolUseId": tool_use_id, "isError": True,
        "inputDigest": content_digest(canonical(original_input)), "nativeOutput": native_output,
        "displayOutput": display_output, "deliveredOutput": delivered_output, "projection": projection}
