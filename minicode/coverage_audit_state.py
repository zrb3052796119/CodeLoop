"""Host-owned continuation of one incomplete independent coverage audit.

The containing native checkpoint or authenticated archive supplies authority.
Hashes check preserved bytes; this is not a model-facing import capability.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath

from minicode.review_evidence_documents import EvidenceDocuments, canonical, content_digest
from minicode.coverage_audit_submission import CoverageDrafts


_BINDINGS = ("candidateDigest", "objectiveDigest", "proposalDigest", "supersessionDigest")
_RECORDS = ("authorityRecord", "documentsRecord", "messagesRecord")
DELIVERY_TOOLS = frozenset({"completion_coverage_audit_submit", "review_evidence_read", "review_probe_read", "review_test"})


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate coverage checkpoint JSON key")
        value[key] = item
    return value


def record_document(record):
    if not isinstance(record, dict) or set(record) != {"ref", "sha256", "rawJson"}:
        raise ValueError("coverage checkpoint requires exact host record bytes")
    reference, raw = record["ref"], record["rawJson"]
    if (not isinstance(reference, str) or "\x00" in reference or "\\" in reference
            or not PurePosixPath(reference).is_absolute() or ".." in PurePosixPath(reference).parts
            or not isinstance(raw, str) or record["sha256"] != content_digest(raw)):
        raise ValueError("coverage checkpoint reference or bytes changed")
    return json.loads(raw, object_pairs_hook=_object,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite coverage checkpoint value")))


def host_record(reference, raw):
    raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    record = {"ref": reference, "sha256": content_digest(raw), "rawJson": raw}
    record_document(record)
    return record


def delivered_tool_pairs(messages):
    calls, seen_results = {}, set()
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("invalid coverage audit message")
        if message.get("role") == "assistant_tool_call":
            identity = message.get("toolUseId")
            if not isinstance(identity, str) or identity in calls or not isinstance(message.get("input"), dict):
                raise ValueError("coverage tool call identity changed")
            calls[identity] = message
        elif message.get("role") == "tool_result":
            identity = message.get("toolUseId")
            call = calls.get(identity)
            if (call is None or identity in seen_results or call["toolName"] != message.get("toolName")
                    or not isinstance(message.get("content"), str) or type(message.get("isError")) is not bool):
                raise ValueError("coverage result is not paired with an actual call")
            seen_results.add(identity)
            yield call, message
    if set(calls) != seen_results:
        raise ValueError("coverage checkpoint has an undelivered tool call; reconcile its actual result before resuming")


def coverage_delivery_pairs(state):
    """Historical delivered bytes are provenance, not restored model context."""
    messages = record_document(state["messagesRecord"])["messages"]
    current = list(delivered_tool_pairs(messages))
    if "deliveryRecord" not in state:
        return [(call, result) for call, result in current if call["toolName"] in DELIVERY_TOOLS]
    document = record_document(state["deliveryRecord"])
    if (set(document) != {"auditId", *_BINDINGS, "pairs"} or document["auditId"] != state["auditId"]
            or any(document[key] != state[key] for key in _BINDINGS) or not isinstance(document["pairs"], list)):
        raise ValueError("coverage delivery provenance bindings changed")
    flat = []
    for pair in document["pairs"]:
        if not isinstance(pair, dict) or set(pair) != {"call", "result"}:
            raise ValueError("coverage delivery provenance needs actual call/result pairs")
        if pair["call"].get("toolName") not in DELIVERY_TOOLS:
            raise ValueError("unexpected coverage delivery provenance tool")
        flat.extend((pair["call"], pair["result"]))
    pairs = list(delivered_tool_pairs(flat))
    by_id = {call["toolUseId"]: (call, result) for call, result in pairs}
    for call, result in current:
        if call["toolName"] not in DELIVERY_TOOLS:
            continue
        original = by_id.get(call["toolUseId"])
        if original is None or any(call.get(key) != original[0].get(key) for key in ("toolName", "input", "toolUseId")):
            raise ValueError("current coverage call lacks delivered provenance")
        if any(result.get(key) != original[1].get(key) for key in ("toolName", "toolUseId", "isError", "_coverage_rejection_record")):
            raise ValueError("current coverage result differs from delivered provenance")
        compacted = any(result.get(key) for key in ("_microcompacted", "_read_history_compacted", "_persisted_path"))
        if not compacted and result.get("content") != original[1].get("content"):
            raise ValueError("current coverage result bytes changed without compaction")
    return pairs


def restore_evidence_documents(state):
    document = record_document(state["documentsRecord"])
    store = EvidenceDocuments.from_snapshot(candidate_digest=state["candidateDigest"], documents=document["documents"])
    if "reviewMaterialsRecord" in state:
        from minicode.supersession_review_material import build_review_materials
        expected = build_review_materials(authority=coverage_audit_authority(state),
            authority_record=state["authorityRecord"], documents_record=state["documentsRecord"], store=store)
        record_document(state["reviewMaterialsRecord"])
        if state["reviewMaterialsRecord"]["rawJson"] != canonical(expected):
            raise ValueError("coverage review materials differ from original host authority")
    drafts = CoverageDrafts({key: state[key] for key in _BINDINGS}, state["auditId"])
    for call, result in coverage_delivery_pairs(state):
        if call["toolName"] == "completion_coverage_audit_submit" and not result["isError"]:
            raise ValueError("completed native coverage decision cannot resume as pending")
        if call["toolName"] == "completion_coverage_audit_submit" and result["isError"]:
            drafts.restore_rejection(call["input"], result["content"],
                rejection_record=result.get("_coverage_rejection_record"), tool_use_id=call["toolUseId"])
        if call["toolName"] == "review_evidence_read" and not result["isError"]:
            store.restore_delivery(call["input"], json.loads(result["content"]))
    return store


def coverage_audit_authority(state):
    return deepcopy(record_document(state["authorityRecord"]))


def validate_coverage_audit_state(state, *, objective, candidate_digest=None, proposal=None,
                                  supersession_obligations=None):
    from minicode.implementation_completion_review import requirement_units

    required_keys = {*_RECORDS, *_BINDINGS, "coverageAuditStateVersion", "status", "auditId"}
    if not isinstance(state, dict):
        raise ValueError("invalid pending coverage audit state")
    version = state.get("coverageAuditStateVersion")
    material_keys = {"reviewMaterialsRecord"} if version == 2 or version == 3 and "reviewMaterialsRecord" in state else set()
    inspection_keys = ({"inspectionRecord"} | ({"legacyAuditRecord"} if "legacyAuditRecord" in state else set())) if version == 3 else set()
    required_keys |= inspection_keys
    required_keys |= material_keys
    if (set(state) not in (required_keys, required_keys | {"deliveryRecord"})
            or type(state["coverageAuditStateVersion"]) is not int
            or state["coverageAuditStateVersion"] not in {1, 2, 3} or state["status"] != "pending"):
        raise ValueError("invalid pending coverage audit state")
    authority, documents, saved = [record_document(state[key]) for key in _RECORDS]
    expected_id = "coverage_audit_" + hashlib.sha256(state["authorityRecord"]["ref"].encode()).hexdigest()[:24]
    if state["auditId"] != expected_id or saved.get("auditId") != expected_id:
        raise ValueError("coverage audit identity changed")
    if any(record.get(key) != state[key] for record in (authority, documents, saved) for key in _BINDINGS):
        raise ValueError("coverage audit checkpoint bindings changed")
    if (authority.get("originalObjective") != objective or state["objectiveDigest"] != content_digest(objective)
            or authority.get("requirements") != list(requirement_units(objective))
            or state["proposalDigest"] != content_digest(canonical(authority.get("proposal")))
            or state["supersessionDigest"] != content_digest(canonical(authority.get("supersessionObligations")))):
        raise ValueError("coverage audit original authority changed")
    if candidate_digest is not None and state["candidateDigest"] != candidate_digest:
        raise ValueError("coverage audit candidate changed")
    if proposal is not None and authority["proposal"] != proposal:
        raise ValueError("coverage audit proposal changed")
    if supersession_obligations is not None and authority["supersessionObligations"] != list(supersession_obligations):
        raise ValueError("coverage audit supersession obligations changed")
    inspection = None
    if version == 3:
        from minicode.completion_coverage_inspection import validate_inspection_record
        inspection = validate_inspection_record(state["inspectionRecord"], state)
        if "legacyAuditRecord" in state:
            legacy = record_document(state["legacyAuditRecord"])
            validate_coverage_audit_state(legacy, objective=objective, candidate_digest=state["candidateDigest"])
            if legacy["coverageAuditStateVersion"] == 3 or inspection["origin"] != "legacy_new_context":
                raise ValueError("inspection legacy epoch must preserve an original pre-inspection audit")
    messages = saved.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError("coverage audit lacks its actual context")
    originals = [m for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str)
                 and m["content"].endswith("=== ORIGINAL REQUIREMENT AUTHORITY ===\n" + objective)]
    packets = [m for m in messages if m.get("role") == "user"]
    inspecting = inspection is not None and inspection["phase"] == "inspection"
    if inspection is not None:
        inspection_packets = [m for m in packets if m.get("content", "").startswith('{"coverageInspection":')]
        # Either phase can compact away its early user message. The exact
        # authenticated task remains in inspectionRecord; absence from active
        # context never grants source/evidence credit or creates a new epoch.
        if len(inspection_packets) > 1:
            raise ValueError("inspection has duplicate original user task packets")
        if inspection_packets and inspection_packets[0] != inspection["taskPacket"]:
            raise ValueError("inspection original user task bytes changed")
        inspected_packet = json.loads(inspection["taskPacket"]["content"])["coverageInspection"]
        if (set(inspected_packet) != {"auditId", "candidateDigest", "objectiveDigest", "requirements", "behaviorContract"}
                or any(inspected_packet.get(key) != state[key] for key in ("auditId", "candidateDigest", "objectiveDigest"))
                or inspected_packet["requirements"] != authority["requirements"]
                or inspected_packet["behaviorContract"] != authority.get("behaviorContract")):
            raise ValueError("inspection original user task bindings changed")
        from minicode.requirement_obligations import build_obligation_manifest
        if inspected_packet["behaviorContract"] != build_obligation_manifest(objective, authority["requirements"]):
            raise ValueError("inspection original behavior contract changed")
        packets = [m for m in packets if m not in inspection_packets]
    if len(originals) != 1 or len(packets) != (0 if inspecting else 1):
        raise ValueError("coverage audit original context changed")
    if inspecting:
        forbidden = {"review_probe_read", "review_evidence_read", "completion_evidence_read", "completion_coverage_audit_submit", "coverage_audit_history_read"}
        if any(m.get("role") == "assistant_tool_call" and m.get("toolName") in forbidden for m in messages):
            raise ValueError("reviewer evidence was exposed during independent inspection")
        restore_evidence_documents(state)
        return deepcopy(state)
    packet = json.loads(packets[0]["content"])
    if (any(packet.get(key) != state[key] for key in _BINDINGS)
            or packet.get("authorityRef") != state["authorityRecord"]["ref"]
            or packet.get("evidenceDocumentsRef") != state["documentsRecord"]["ref"]
            or packet.get("requirements") != authority["requirements"]
            or packet.get("proposal") != {key: authority["proposal"][key] for key in ("status", "requirements", "findings")}):
        raise ValueError("coverage audit initial packet changed")
    if "behaviorContract" in authority:
        from minicode.requirement_obligations import build_obligation_manifest
        if (authority["behaviorContract"] != build_obligation_manifest(authority["originalObjective"], authority["requirements"])
                or packet.get("behaviorContract") != authority["behaviorContract"]):
            raise ValueError("coverage audit original behavior contract changed")
    restore_evidence_documents(state)
    return deepcopy(state)


def build_coverage_audit_state(*, authority_record, documents_record, messages_record, delivery_record=None,
                               review_materials_record=None, inspection_record=None, legacy_audit_record=None):
    authority = record_document(authority_record)
    messages = record_document(messages_record)
    state = {"coverageAuditStateVersion": 2 if review_materials_record is not None else 1, "status": "pending", "auditId": messages.get("auditId"),
        **{key: messages.get(key) for key in _BINDINGS}, "authorityRecord": deepcopy(authority_record),
        "documentsRecord": deepcopy(documents_record), "messagesRecord": deepcopy(messages_record)}
    if delivery_record is not None:
        state["deliveryRecord"] = deepcopy(delivery_record)
    if review_materials_record is not None:
        state["reviewMaterialsRecord"] = deepcopy(review_materials_record)
    if inspection_record is not None:
        state.update(coverageAuditStateVersion=3, inspectionRecord=deepcopy(inspection_record))
        if legacy_audit_record is not None:
            state["legacyAuditRecord"] = deepcopy(legacy_audit_record)
    return validate_coverage_audit_state(state, objective=authority["originalObjective"])
