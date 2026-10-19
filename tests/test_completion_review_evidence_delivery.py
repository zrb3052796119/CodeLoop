"""A resumed reviewer gets a current index without losing historical evidence."""

import json

from test_implementation_completion_review import _fixture, _probe, _submit


DIAGNOSTIC = "from stock import release\ndef test_observe():\n    assert release(3, 2) == 3\n"


def _read_all(tools, child, probe_id, **extra):
    offset, chunks = 0, []
    while True:
        result = tools.execute("review_probe_read", {"probeId": probe_id,
            "offset": offset, "limit": 16000, **extra}, child)
        assert result.ok, result.output
        page = json.loads(result.output)
        chunks.append(page["text"])
        if page["nextOffset"] is None:
            return json.loads("".join(chunks))
        offset = page["nextOffset"]


def test_reviewer_index_has_current_diagnostic_status_and_readable_full_history(tmp_path, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch)
    saved = {}

    def diagnose(tools, child, packet):
        result, evidence = _probe(tools, child, packet, DIAGNOSTIC, purpose="diagnostic")
        assert not result.ok
        saved.update(evidence)
        # An ordinary end is not approval; another attempt can read all evidence.

    try:
        assert assess(diagnose)["status"] == "inconclusive"

        def accept(tools, child, packet):
            entries = packet["diagnosticObservations"]
            assert len(entries) == 1
            row = entries[0]
            assert row["probeId"] == saved["probeId"]
            assert row["status"] == "failed"
            assert row["candidateDigest"] == packet["candidateDigest"]
            assert row["receiptCount"] == 1
            assert row["latestReceiptRef"] == saved["receiptRef"]
            assert "source" not in row and "receipts" not in row
            full = _read_all(tools, child, row["probeId"])
            assert full["source"]["code"] == DIAGNOSTIC
            assert full["receipts"] == [saved]
            current = _read_all(tools, child, row["probeId"], receiptView="current")
            assert current["receipts"] == [saved]
            assert current["historyReceiptCount"] == 1
            passed, acceptance = _probe(tools, child, packet)
            assert passed.ok
            assert _submit(tools, child, packet, ids=[acceptance["probeId"]]).ok

        approved = assess(accept)
        assert approved["status"] == "approved"
        assert approved["diagnosticObservations"][0]["source"]["code"] == DIAGNOSTIC
        assert approved["diagnosticObservations"][0]["receipts"] == [saved]
    finally:
        cap.close()


def test_current_receipt_view_does_not_mislabel_previous_candidate_evidence(tmp_path, monkeypatch):
    review, assess, _commands, cap = _fixture(tmp_path, monkeypatch)
    saved = {}

    def seed(tools, child, packet):
        result, evidence = _probe(tools, child, packet, DIAGNOSTIC, purpose="diagnostic")
        assert not result.ok
        saved.update(evidence)

    try:
        assess(seed)
        with (tmp_path / "stock.py").open("a") as handle:
            handle.write("\n# a new candidate\n")

        def resumed(tools, child, packet):
            entry = packet["diagnosticObservations"][0]
            assert entry["status"] is None and entry["latestReceiptRef"] is None
            assert entry["receiptCount"] == 1
            current = _read_all(tools, child, saved["probeId"], receiptView="current")
            assert current["receipts"] == [] and current["historyReceiptCount"] == 1
            full = _read_all(tools, child, saved["probeId"])
            assert full["receipts"] == [saved]
            malformed = tools.execute("review_probe_read", {
                "probeId": saved["probeId"], "receiptView": "ignore_failures"}, child)
            assert not malformed.ok

        assert assess(resumed)["status"] == "inconclusive"
    finally:
        cap.close()
