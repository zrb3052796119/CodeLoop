"""Small, host-selected references for a new independent failure attribution.

Earlier judgments are advisory observations. They never establish the verdict,
coverage, repair permission or acceptance of a different probe/receipt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


_PREVIEW_ENTRIES = 4
_SEMANTICS = "advisory_only_not_authority_or_permission"


def _digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _binding(spec, probe, receipt):
    return {"candidateDigest": receipt["candidateDigest"],
            "objectiveDigest": _digest(spec.objective),
            "currentProbeId": probe["probeId"], "currentSourceDigest": probe["sourceDigest"],
            "currentReceiptRef": receipt["receiptRef"]}


def _material(spec, record):
    from minicode.probe_failure_adjudication import _read_evidence, validate_adjudication_record

    record = {key: value for key, value in record.items() if key != "adjudicationRef"}
    authority_raw = _read_evidence(record["authorityRef"], record["evidenceDirectory"], "authority")
    authority = json.loads(authority_raw)
    validate_adjudication_record(spec, authority["probe"], authority["receipt"], record)
    decision_raw = _read_evidence(record["evidenceRef"], record["evidenceDirectory"], "decision")
    return authority, json.loads(decision_raw), "sha256:" + hashlib.sha256(authority_raw).hexdigest()


def build_advisory_context(*, spec, probe, receipt, probes, adjudications, chains=()):
    """Select authenticated same-scope history without copying its large bodies.

The caller owns validated review state. Filtering occurs before reading any
historical authority, so records for another candidate/objective are not opened.
Only a compact reference index is persisted in the new authority document.
"""
    binding = _binding(spec, probe, receipt)
    sources = {key: value.to_dict() if hasattr(value, "to_dict") else value
               for key, value in probes.items()}
    ancestors = {probe["probeId"]}
    edges = [row for row in chains if row.get("candidateDigest") == binding["candidateDigest"]
             and row.get("objectiveDigest", binding["objectiveDigest"]) == binding["objectiveDigest"]
             and row.get("oldProbeId") in sources and row.get("newProbeId") in sources]
    while True:
        expanded = ancestors | {row["oldProbeId"] for row in edges if row["newProbeId"] in ancestors}
        if expanded == ancestors:
            break
        ancestors = expanded
    eligible = []
    for sequence, record in enumerate(adjudications):
        if any(record.get(key) != binding[key] for key in ("candidateDigest", "objectiveDigest")):
            continue
        source = sources.get(record.get("oldProbeId"))
        if not source or source.get("sourceDigest") != record.get("sourceDigest"):
            continue
        common = sorted(set(source["criterionIds"]) & set(probe["criterionIds"]))
        related = source["probeId"] in ancestors
        if not related and not common:
            continue
        relations = (["correction_ancestor"] if related and source["probeId"] != probe["probeId"] else
                     ["same_probe_prior_receipt"] if related else [])
        if common:
            relations.append("shared_criteria")
        eligible.append(((related, len(common), sequence), record, source, relations))
    entries = []
    seen = set()
    for _rank, record, source, relations in sorted(eligible, key=lambda row: row[0], reverse=True):
        if record["evidenceRef"] in seen:
            continue
        seen.add(record["evidenceRef"])
        authority, _decision, authority_digest = _material(spec, record)
        if authority["probe"] != source:
            raise ValueError("advisory history source differs from its authenticated authority")
        entries.append({"probeId": source["probeId"], "sourceDigest": source["sourceDigest"],
            "criterionIds": list(source["criterionIds"]), "relations": relations,
            "verdictClaim": record["verdict"], "reasonExcerpt": record["reason"][:320],
            "authorityRef": record["authorityRef"], "authorityDigest": authority_digest,
            "decisionRef": record["evidenceRef"], "decisionDigest": record["evidenceDigest"]})
    return {"advisoryVersion": 1, "semantics": _SEMANTICS, **binding,
            "relatedRecordCount": len(eligible), "entries": entries}


def project_advisory_context(context, *, spec, probe, receipt, views):
    """Register exact originals; the wire receives advisory summaries and refs."""
    from minicode.probe_failure_adjudication import _read_evidence

    binding = _binding(spec, probe, receipt)
    if (not isinstance(context, dict) or context.get("advisoryVersion") != 1
            or context.get("semantics") != _SEMANTICS
            or any(context.get(key) != value for key, value in binding.items())
            or not isinstance(context.get("entries"), list)):
        raise ValueError("advisory context does not bind this current failed probe")
    entries = []
    for entry in context["entries"]:
        directory = str(Path(entry["decisionRef"]).parent)
        raw = _read_evidence(entry["decisionRef"], directory, "decision")
        if "sha256:" + hashlib.sha256(raw).hexdigest() != entry["decisionDigest"]:
            raise ValueError("advisory decision changed")
        saved = json.loads(raw)
        record = {**saved["decision"], "evidenceRef": entry["decisionRef"],
                  "evidenceDigest": entry["decisionDigest"], "evidenceDirectory": directory}
        if (any(record.get(key) != value for key, value in binding.items()
                if key in {"candidateDigest", "objectiveDigest"})
                or record.get("oldProbeId") != entry["probeId"]
                or record.get("sourceDigest") != entry["sourceDigest"]
                or record.get("authorityRef") != entry["authorityRef"]):
            raise ValueError("advisory record is outside its stated scope")
        authority, decision, authority_digest = _material(spec, record)
        if (authority_digest != entry["authorityDigest"]
                or authority["probe"]["criterionIds"] != entry["criterionIds"]
                or record["verdict"] != entry["verdictClaim"]
                or record["reason"][:320] != entry["reasonExcerpt"]):
            raise ValueError("advisory index differs from its exact original")
        entries.append({key: entry[key] for key in ("probeId", "sourceDigest", "criterionIds",
                "relations", "verdictClaim", "reasonExcerpt")} | {
            "authority": views.reference(authority, kind="advisory_authority"),
            "source": views.reference(authority["probe"], kind="advisory_probe_source"),
            "receipt": views.reference(authority["receipt"], kind="advisory_probe_receipt"),
            "decision": views.reference(decision, kind="advisory_attribution")})
    return {"advisoryVersion": 1, "semantics": _SEMANTICS, **binding,
            "relatedRecordCount": context["relatedRecordCount"], "entries": entries[:_PREVIEW_ENTRIES],
            "completeIndex": views.reference({"entries": entries}, kind="advisory_complete_index"),
            "readHint": "Earlier claims are reference material only. completeIndex contains every related entry, including those omitted from the preview. Use completion_evidence_read for that full index and exact originals; independently decide this current probe and receipt from the original objective and current source."}


__all__ = ["build_advisory_context", "project_advisory_context"]
