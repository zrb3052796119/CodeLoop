"""Evidence bindings for pending, independently audited test replacements.

Structural similarity is a useful classification, not semantic authority. These
helpers never approve a replacement or the implementation.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from minicode.review_probe_source import validate_review_probe_source
from minicode.review_probe_semantic_correction import validate_adjudicated_probe_correction
from minicode.reviewer_verification import ReviewerProbe


def digest(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def admit_replacement(old, new):
    """Classify a proposed replacement; independent coverage audit is mandatory."""
    if (not validate_review_probe_source(old) or not validate_review_probe_source(new)
            or old["probeId"] == new["probeId"] or old["criterionIds"] != new["criterionIds"]):
        raise ValueError("replacement requires distinct exact sources and all original criteria")
    try:
        ast.parse(new["code"])
    except (SyntaxError, ValueError) as error:
        raise ValueError("replacement must be valid Python") from error
    try:
        return validate_adjudicated_probe_correction(old, new)
    except ValueError:
        return {"mode": "semantic_replacement"}


def make_supersession(old, new, authority, original, replacement):
    document = {"supersessionVersion": 1, "objectiveDigest": authority["objectiveDigest"],
        "candidateDigest": original["candidateDigest"], "oldProbeId": old["probeId"],
        "newProbeId": new["probeId"], "oldSourceDigest": old["sourceDigest"],
        "newSourceDigest": new["sourceDigest"], "criterionIds": old["criterionIds"],
        "adjudicationRef": authority["adjudicationRef"], "originalReceipt": original["receiptRef"],
        "replacementReceipt": replacement["receiptRef"], "correctionMode": admit_replacement(old, new)["mode"]}
    return {**document, "supersessionId": "supersession_" + digest(document)[7:31]}


def validate_supersession(document, *, objective_digest, probes, receipts, adjudications):
    if not isinstance(document, dict):
        raise ValueError("supersession must be a host record")
    try:
        old, new = probes[document["oldProbeId"]], probes[document["newProbeId"]]
        original, replacement = receipts[document["originalReceipt"]], receipts[document["replacementReceipt"]]
        authority = next(row for row in adjudications if row["adjudicationRef"] == document["adjudicationRef"])
    except (KeyError, StopIteration) as error:
        raise ValueError("supersession lacks its original sources, receipts or independent authority") from error
    old, new = old.to_dict(), new.to_dict()
    expected = make_supersession(old, new, authority, original, replacement)
    if ({k: v for k, v in document.items() if k != "supersessionRef"} != expected
            or expected["objectiveDigest"] != objective_digest or authority.get("verdict") != "probe_error"
            or authority.get("receiptRef") != original["receiptRef"]
            or authority.get("candidateDigest") != original["candidateDigest"]
            or original["probeId"] != old["probeId"] or replacement["probeId"] != new["probeId"]
            or original["candidateDigest"] != replacement["candidateDigest"]
            or ReviewerProbe.create(criterion_ids=authority["replacement"].get("criterionIds"),
                scenario=authority["replacement"].get("scenario"), code=authority["replacement"].get("code")).to_dict() != new):
        raise ValueError("supersession source, requirement or independent authority binding changed")
    for row in receipts.values():
        if row["probeId"] == old["probeId"] and row["receipt"].get("status") == "passed":
            raise ValueError("a previously passed acceptance cannot be proposed for supersession")
        if row["receiptRef"] == original["receiptRef"]:
            break
    return expected


def read_audit_record(record):
    """Authenticate the exact persisted audit decision, not its semantic truth."""
    if not isinstance(record, dict):
        raise ValueError("supersession requires an independent coverage audit")
    path = Path(record.get("evidenceRef", ""))
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("coverage audit evidence is unavailable")
    saved = json.loads(path.read_text(encoding="utf-8"))
    expected = {**saved, "evidenceRef": str(path), "evidenceDigest": digest(saved)}
    if (record not in (expected, {key: expected[key] for key in ("evidenceRef", "evidenceDigest")})
            or saved.get("judgmentKind") != "independent_model_semantic_coverage"):
        raise ValueError("coverage audit evidence changed")
    authority_path = Path(saved.get("authorityRef", ""))
    if (authority_path.parent.resolve() != path.parent.resolve() or authority_path.is_symlink()
            or not authority_path.is_file()):
        raise ValueError("coverage audit original authority is unavailable")
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    for key in ("objectiveDigest", "candidateDigest", "proposalDigest", "supersessionDigest"):
        if saved.get(key) != authority.get(key):
            raise ValueError("coverage audit authority binding changed")
    if digest(authority.get("supersessionObligations", [])) != saved.get("supersessionDigest"):
        raise ValueError("coverage audit supersession obligations changed")
    if (digest(authority.get("originalObjective", "")) != saved.get("objectiveDigest")
            or digest(authority.get("proposal")) != saved.get("proposalDigest")):
        raise ValueError("coverage audit original objective or proposal changed")
    return saved, authority


def validate_acceptance(request, audit, *, probes, receipts, objective, chains=()):
    """Validate durable, per-edge approval independently of task approval."""
    from types import SimpleNamespace
    from minicode.completion_coverage_audit import _assertion_range
    from minicode.probe_failure_adjudication import validate_adjudication_record

    saved, authority = read_audit_record(audit)
    if any(row["probeId"] == request["oldProbeId"] and row["receipt"].get("status") == "passed"
           for row in receipts.values()):
        raise ValueError("a previously passed acceptance cannot be superseded")
    if authority["originalObjective"] != objective or saved["objectiveDigest"] != request["objectiveDigest"]:
        raise ValueError("supersession audit objective changed")
    matches = [row for row in authority["supersessionObligations"] if row.get("id") == request["supersessionId"]]
    decisions = [row for row in saved.get("supersessions", []) if row.get("id") == request["supersessionId"]]
    if len(matches) != 1 or len(decisions) != 1 or decisions[0].get("status") != "accepted":
        raise ValueError("supersession lacks its exact independent acceptance")
    obligation, decision = matches[0], decisions[0]
    if (obligation.get("request") != request or decision.get("obligationDigest") != digest(obligation)
            or obligation.get("oldSource") != probes[request["oldProbeId"]].to_dict()
            or obligation.get("newSource") != probes[request["newProbeId"]].to_dict()):
        raise ValueError("supersession audit source or obligation binding changed")
    original = receipts[request["originalReceipt"]]
    invalidity = obligation.get("authority", {})
    if (obligation.get("originalReceipt") != original
            or invalidity.get("adjudicationRef") != request["adjudicationRef"]):
        raise ValueError("supersession audit original failed evidence changed")
    validate_adjudication_record(SimpleNamespace(objective=objective), obligation["oldSource"], original,
        {key: value for key, value in invalidity.items() if key != "adjudicationRef"})
    effective = obligation["effectiveProbeId"]
    links = {row.get("supersessionId", row.get("correctionRef")): {
        "id": row.get("supersessionId", row.get("correctionRef")),
        "oldProbeId": row["oldProbeId"], "newProbeId": row["newProbeId"]} for row in chains}
    terminal, seen = request["newProbeId"], {request["oldProbeId"]}
    for link in obligation.get("replacementChain", []):
        if link != links.get(link.get("id")) or link["oldProbeId"] != terminal or terminal in seen:
            raise ValueError("supersession audit successor chain changed")
        seen.add(terminal)
        terminal = link["newProbeId"]
    if terminal != effective or effective in seen:
        raise ValueError("supersession audit witnesses belong to an unrelated successor")
    source = probes[effective].to_dict()
    current = obligation["currentReceipt"]
    if (current != receipts.get(current.get("receiptRef")) or current.get("probeId") != effective
            or current.get("candidateDigest") != saved["candidateDigest"]):
        raise ValueError("supersession audit requires an exact current execution receipt")
    if current["receipt"]["status"] != "passed":
        gap = obligation.get("productGapAuthority")
        if not isinstance(gap, dict) or gap.get("verdict") != "product_gap":
            raise ValueError("failed replacement lacks independent product-gap authority")
        validate_adjudication_record(SimpleNamespace(objective=objective), source, current,
            {key: value for key, value in gap.items() if key != "adjudicationRef"})
    rows = decision.get("requirements", [])
    if (len(rows) != len(request["criterionIds"])
            or {row.get("id") for row in rows} != set(request["criterionIds"])):
        raise ValueError("supersession audit omitted an original obligation")
    for row in rows:
        if not row.get("witnesses") or not row.get("reason"):
            raise ValueError("supersession audit omitted concrete assertion evidence")
        for witness in row["witnesses"]:
            text = _assertion_range(source["code"], witness["startLine"], witness["endLine"])
            if (row["id"] not in source["criterionIds"] or witness.get("probeId") != effective
                    or witness.get("sourceDigest") != source["sourceDigest"]
                    or witness.get("lineDigest") != digest(text) or witness.get("receiptDigest") != digest(current)
                    or witness.get("receiptRef") != current["receiptRef"]):
                raise ValueError("supersession audit witness binding changed")
    return obligation


__all__ = ["admit_replacement", "make_supersession", "validate_supersession", "read_audit_record", "validate_acceptance"]
