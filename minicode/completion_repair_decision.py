"""Accepted negative decisions are host repair authority, never approval.

The checkpoint/archive is host authenticated. Embedded hashes preserve exact
native bytes; they do not make model-supplied state trusted.
"""
import hashlib
import json
import re

from minicode.coverage_audit_state import record_document
from minicode.implementation_continuation import implementation_spec_digest
from minicode.review_evidence_documents import canonical


def source_backed_missing(decision):
    return (isinstance(decision, dict) and decision.get("status") == "changes_required"
        and any(isinstance(row, dict) and row.get("status") == "missing"
            and row.get("sourceEvidence") for row in decision.get("requirements", [])))


def delivered_repair_matches(feedback, decision, *, executor_id, candidate_digest, objective):
    """Compare host-authenticated delivery with a freshly validated negative.

    The caller must first verify current checks, candidate and accepted repair
    anchors. This comparison never authorizes repair from historical prose.
    Only the outer delivery envelope and outer storage addresses may differ;
    nested receipts, source bindings, unresolved probes and findings stay exact.
    """
    if (not isinstance(feedback, dict) or feedback.get("role") != "user"
            or feedback.get("_implementation_completion_feedback") is not True
            or feedback.get("_superseded_completion_feedback")
            or not isinstance(decision, dict) or decision.get("status") != "changes_required"
            or decision.get("canContinue") is False or decision.get("patchDigest") != candidate_digest):
        return False
    content = feedback.get("_completion_evidence_original", feedback.get("content"))
    if not isinstance(content, str):
        return False
    projection = feedback.get("_completion_evidence_projection")
    if projection is not None and (not isinstance(projection, dict)
            or projection.get("originalContentDigest") != "sha256:" + hashlib.sha256(content.encode()).hexdigest()):
        return False
    prefix, marker, body = content.rpartition("\nCompletion evidence:\n")
    if not marker or not prefix.endswith("Original public request (unchanged):\n" + objective):
        return False

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate completion receipt field")
            result[key] = value
        return result

    try:
        receipt = json.loads(body, object_pairs_hook=unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite completion receipt")))
    except (ValueError, TypeError):
        return False
    if (not isinstance(receipt, dict) or type(receipt.get("completionVersion")) is not int
            or receipt["completionVersion"] != 1 or receipt.get("executorId") != executor_id
            or type(receipt.get("attempt")) is not int or receipt["attempt"] < 1
            or receipt.get("continuesSameExecutor") is not True
            or receipt.get("patchDigest") != candidate_digest):
        return False
    envelope = {"completionVersion", "executorId", "attempt", "continuesSameExecutor"}
    storage = {"evidenceRef", "repairDecisionRef", "evidenceDirectory"}
    try:
        return (canonical({key: value for key, value in receipt.items() if key not in envelope | storage})
                == canonical({key: value for key, value in decision.items() if key not in storage}))
    except (ValueError, TypeError):
        return False


def validate_repair_decision(record, spec, objective_digest, requirement_ids):
    document = record_document(record)
    if (set(document) != {"repairDecisionVersion", "candidateDigest", "objectiveDigest",
            "specDigest", "origin", "decision", "authorityRecord"}
            or type(document["repairDecisionVersion"]) is not int
            or document["repairDecisionVersion"] != 1
            or document["objectiveDigest"] != objective_digest
            or document["specDigest"] != implementation_spec_digest(spec)
            or not isinstance(document["candidateDigest"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", document["candidateDigest"]) is None):
        raise ValueError("accepted repair decision binding changed")
    decision = document["decision"]
    if (not isinstance(decision, dict) or decision.get("status") != "changes_required"
            or decision.get("patchDigest") != document["candidateDigest"]
            or not isinstance(decision.get("findings"), list) or not decision["findings"]
            or any(not isinstance(x, str) or not x.strip() for x in decision["findings"])):
        raise ValueError("accepted repair decision grants repair only")
    rows = decision.get("requirements")
    if (not isinstance(rows, list) or len(rows) != len(requirement_ids)
            or any(not isinstance(row, dict) for row in rows)
            or {row.get("id") for row in rows} != set(requirement_ids)):
        raise ValueError("accepted repair decision changed original requirements")
    for row in rows:
        if row.get("status") not in {"covered", "missing", "unknown"}:
            raise ValueError("accepted repair row has invalid status")
        for anchor in row.get("sourceEvidence", []):
            if (not isinstance(anchor, dict) or set(anchor) != {"path", "startLine", "endLine", "sourceDigest"}
                    or not isinstance(anchor["sourceDigest"], str)
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", anchor["sourceDigest"]) is None):
                raise ValueError("accepted repair source must preserve native binding")
    authority = record_document(document["authorityRecord"])
    if document["origin"] == "validated_review":
        if (authority != {"nativeNegativeVersion": 1, "candidateDigest": document["candidateDigest"],
                "objectiveDigest": objective_digest, "decision": decision}
                or not source_backed_missing(decision)):
            raise ValueError("repair decision lacks normalized native source-backed omission")
    elif document["origin"] == "validated_coverage_audit":
        audit = decision.get("coverageAudit", {})
        if (authority != {k: v for k, v in audit.items() if k not in {"evidenceRef", "evidenceDigest"}}
                or authority.get("status") != "changes_required"
                or authority.get("candidateDigest") != document["candidateDigest"]
                or authority.get("objectiveDigest") != objective_digest
                or authority.get("judgmentKind") != "independent_model_semantic_coverage"
                or not (any(row.get("status") == "missing" for row in authority.get("requirements", []))
                    or authority.get("missingBehaviorEvidence"))):
            raise ValueError("repair decision lacks accepted native coverage authority")
    else:
        raise ValueError("unknown accepted repair origin")
    return document
