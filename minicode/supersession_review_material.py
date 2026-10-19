"""Host-derived mandatory review material, separate from optional raw traces.

The original obligation remains immutable and is the accepted decision binding.
This contract changes only which exact bytes must reach the semantic reviewer;
it never summarizes the sources/invalidity authority or authenticates a witness.
"""
from __future__ import annotations

import json

from minicode.review_evidence_documents import canonical, content_digest


_BINDINGS = ("candidateDigest", "objectiveDigest", "proposalDigest", "supersessionDigest")


def build_review_materials(*, authority: dict, authority_record: dict,
                           documents_record: dict, store) -> dict:
    originals = store.snapshot()
    original_ids = {row["documentId"] for row in originals}
    sources = {source["probeId"]: source for row in originals if row["documentType"] == "probe_source"
               for source in [json.loads(row["text"]) ]}
    index = []
    for obligation in authority["supersessionObligations"]:
        raw_id = store.add(obligation, "supersession_obligation")
        material = {key: value for key, value in obligation.items()
                    if key not in {"originalReceipt", "currentReceipt"}}
        material["effectiveSource"] = sources[obligation["effectiveProbeId"]]
        material["reviewMaterialVersion"] = 1
        material["originalObligationDigest"] = content_digest(canonical(obligation))
        material["optionalExecutionEvidence"] = {}
        for key in ("originalReceipt", "currentReceipt"):
            receipt = obligation.get(key)
            if receipt is not None:
                receipt_id = store.add(receipt, "execution_receipt")
                material["optionalExecutionEvidence"][key] = {
                    "documentId": receipt_id, "originalDigest": content_digest(canonical(receipt)),
                    "receiptRef": receipt.get("receiptRef"),
                    "status": receipt.get("receipt", {}).get("status"),
                    "deliveryRequired": False}
        # A new root packet deliberately does not inherit old factoring edges.
        # It contains complete sources and invalidity records; optional receipt
        # IDs never become mandatory closure dependencies.
        identity = store.add(material, "supersession_review_material", factor=False)
        index.append({"id": obligation["id"],
            "obligationDigest": material["originalObligationDigest"],
            "obligationDocumentId": identity, "originalObligationDocumentId": raw_id})
    return {"reviewMaterialVersion": 1, "deliveryPolicy": "complete_sources_and_invalidity_optional_execution",
        **{key: authority[key] for key in _BINDINGS},
        "originalAuthorityRef": authority_record["ref"], "originalAuthorityDigest": authority_record["sha256"],
        "originalDocumentsRef": documents_record["ref"], "originalDocumentsDigest": documents_record["sha256"],
        "supersessions": index,
        "documents": [row for row in store.snapshot() if row["documentId"] not in original_ids]}
