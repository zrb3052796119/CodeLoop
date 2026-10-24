"""Real pytest + audit gates consume complete, source-bound review material."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

from minicode.implementation_completion_review import ImplementationCompletionReview
from test_implementation_completion_review import _coverage_approval, _fixture, _probe, _submit
from test_probe_supersession import BAD, GOOD


def _read(tools, child, document_id):
    pieces, request, pages = [], {"documentId": document_id}, []
    while True:
        result = tools.execute("review_evidence_read", request, child)
        assert result.ok, result.output
        page = json.loads(result.output)
        pages.append(page)
        assert page["documentId"] == document_id
        pieces.append(page["text"])
        if page["nextCursor"] is None:
            text = "".join(pieces)
            assert page["completeDelivered"]
            assert page["documentDigest"] == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
            return json.loads(text), page, pages
        request = {"cursor": page["nextCursor"]}


def test_supersession_needs_complete_sources_and_independent_authority_material(tmp_path, monkeypatch):
    suffix = "# Inventory setup and nonzero quantity restoration remain separate checks.\n" * 250
    old_code, new_code = BAD + suffix, GOOD + suffix
    observations = {}

    def adjudicate(tools, child, packet):
        result = tools.execute("completion_probe_adjudication_submit", {
            "verdict": "probe_error", "reason": "The fixture calls an already constructed inventory twice.",
            "objectiveEvidence": [{"startLine": 1, "endLine": 4}],
            "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
            "replacement": {"criterionIds": packet["probe"]["criterionIds"],
                "scenario": packet["probe"]["scenario"], "code": new_code}}, child)
        assert result.ok, result.output

    def audit(tools, child, packet):
        originals = {p["probeId"]: p for p in review.state["probes"]}
        approval = _coverage_approval(packet, originals)
        blocked = tools.execute("completion_coverage_audit_submit", approval, child)
        assert not blocked.ok
        missing = json.loads(blocked.output)["missingDocumentIds"]
        assert missing
        edge_id = packet["supersessionIndex"][0]["obligationDocumentId"]
        assert edge_id in missing
        first_result = tools.execute("review_evidence_read", {"documentId": edge_id}, child)
        assert first_result.ok
        first = json.loads(first_result.output)
        assert first["documentType"] == "supersession_review_material"
        assert first["nextCursor"] is not None and not first["completeDelivered"]
        refused = tools.execute("completion_coverage_audit_submit", approval, child)
        assert not refused.ok
        assert edge_id in json.loads(refused.output)["missingDocumentIds"]
        assert review.state["retiredProbeIds"] == []
        forged = tools.execute("review_evidence_read", {
            "cursor": first["nextCursor"], "offset": 9000}, child)
        assert not forged.ok
        manifest, manifest_page, pages = _read(tools, child, edge_id)
        assert len(pages) >= 2 and manifest_page["requiredDocumentIds"] == []
        assert manifest["reviewMaterialVersion"] == 1
        assert manifest["oldSource"] == originals[manifest["request"]["oldProbeId"]]
        assert manifest["newSource"] == originals[manifest["request"]["newProbeId"]]
        assert manifest["effectiveSource"] == originals[manifest["effectiveProbeId"]]
        authority = json.loads(Path(packet["authorityRef"]).read_text())
        obligation = authority["supersessionObligations"][0]
        assert manifest["authority"] == obligation["authority"]
        from minicode.review_evidence_documents import canonical, content_digest
        assert manifest["originalObligationDigest"] == content_digest(canonical(obligation))
        # Exact execution traces remain readable, but are not mandatory source
        # and invalidity delivery. Approve before reading those optional IDs.
        optional_ids = {value["documentId"] for value in manifest["optionalExecutionEvidence"].values()}
        assert optional_ids and edge_id not in optional_ids
        assert all(value["deliveryRequired"] is False for value in manifest["optionalExecutionEvidence"].values())
        approved = tools.execute("completion_coverage_audit_submit", approval, child)
        assert approved.ok, approved.output
        observations.update(packet=packet, manifest=manifest)

    review, assess, commands, cap = _fixture(tmp_path, monkeypatch,
        adjudication_driver=adjudicate, coverage_driver=audit)
    original_product = (tmp_path / "stock.py").read_bytes()
    try:
        def seed(tools, child, packet):
            result, receipt = _probe(tools, child, packet, old_code)
            assert not result.ok and receipt["receipt"]["status"] == "failed"

        def propose(tools, child, packet):
            assert _submit(tools, child, packet, ids=[p["probeId"] for p in packet["retainedProbes"]]).ok

        assess(seed)
        assert assess(propose)["status"] == "approved"
        assert review.state["retiredProbeIds"] == [review.state["supersessions"][0]["oldProbeId"]]
        assert ImplementationCompletionReview.from_state(review.spec, review.state).state["corrections"] == review.state["corrections"]
        archive = json.loads(Path(observations["packet"]["evidenceDocumentsRef"]).read_text())
        assert len({d["documentId"] for d in archive["documents"]}) == len(archive["documents"])
        assert sum(d["text"].count("Inventory setup and nonzero") for d in archive["documents"]) == 500
        assert (tmp_path / "stock.py").read_bytes() == original_product
        assert len(commands) == 3
    finally:
        cap.close()


def test_legacy_reader_keeps_current_view_across_new_per_call_tool_contexts(tmp_path, monkeypatch):
    source = "from stock import hold, release\n" + "# A large stock evidence source.\n" * 300 + (
        "def test_inventory():\n    assert hold(5, 2) == 3\n    assert release(3, 2) == 5\n")

    def audit(tools, child, packet):
        pid = packet["evidenceIndex"][0]["probeId"]
        scope = object()
        def execute(value):
            return tools.execute("review_probe_read", value, replace(child, _event_sink=scope))
        first = execute({"probeId": pid, "receiptView": "current", "offset": 0, "limit": 500})
        assert first.ok
        initial = json.loads(first.output)
        second = execute({"probeId": pid, "offset": initial["nextOffset"], "limit": 500})
        assert second.ok, second.output
        page = json.loads(second.output)
        assert page["receiptView"] == "current" and page["documentId"] == initial["documentId"]
        pieces = [initial["text"], page["text"]]
        while page["nextCursor"]:
            result = execute({"cursor": page["nextCursor"]})
            assert result.ok, result.output
            page = json.loads(result.output)
            assert page["probeId"] == pid and page["documentId"] == initial["documentId"]
            pieces.append(page["text"])
        evidence = json.loads("".join(pieces))
        assert len(evidence["receipts"]) == 1 and evidence["historyReceiptCount"] == 2
        approved = tools.execute("completion_coverage_audit_submit",
            _coverage_approval(packet, {pid: evidence["source"]}), child)
        assert approved.ok, approved.output

    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch, coverage_driver=audit)
    try:
        assess(lambda tools, child, packet: _probe(tools, child, packet, source))
        stock = tmp_path / "stock.py"
        stock.write_text(stock.read_text() + "\n# second real candidate\n")
        assert assess(lambda tools, child, packet: _submit(tools, child, packet,
            ids=[packet["retainedProbes"][0]["probeId"]]))["status"] == "approved"
        assert [r["receipt"]["status"] for r in review.state["receipts"]] == ["passed", "passed"]
    finally:
        cap.close()
