"""Passing weak tests cannot promote the author's proposal without authority."""
import json

import pytest

from test_implementation_completion_review import _fixture, _probe, _submit, _coverage_approval


WEAK = [
    "from stock import hold\ndef test_inventory():\n    assert hold(5, 2) == 3\n",
    "from stock import hold, release\ndef test_inventory():\n    assert hold(5, 2) == 3\n    assert release(3, 0) == 3\n",
    "from stock import hold\ndef release(n, amount): return n+amount\ndef test_inventory():\n    assert hold(5, 2) == 3\n    assert release(3, 2) == 5\n",
]


@pytest.mark.parametrize("weak", WEAK, ids=["borrowed_requirement", "trivial_input", "self_written_substitute"])
def test_actual_gate_refuses_authors_approved_proposal_when_independent_audit_finds_gap(tmp_path, monkeypatch, weak):
    audits = []

    def coverage(tools, child, packet):
        audits.append(packet)
        assert "completion_review_submit" not in tools.list_all()
        assert packet["proposal"]["status"] == "approved"
        assert all(row["status"] == "covered" for row in packet["proposal"]["requirements"])
        result = tools.execute("completion_coverage_audit_submit", {"status": "changes_required",
            "requirements": [{"id": packet["requirements"][-1]["id"], "status": "missing",
                "reason": "The passed test does not establish the actual release behavior for a nonzero amount.",
                "witnesses": []}], "findings": ["The actual release consumer still returns unchanged stock."]}, child)
        assert result.ok, result.output

    review, assess, commands, capability = _fixture(tmp_path, monkeypatch, coverage_driver=coverage)
    broken = "def hold(n, amount): return n-amount\n\ndef release(n, amount):\n    return n\n# consumer remains incomplete\n"
    (tmp_path / "stock.py").write_text(broken)

    def author(tools, child, packet):
        passed, evidence = _probe(tools, child, packet, weak)
        assert passed.ok and evidence["receipt"]["status"] == "passed"
        assert _submit(tools, child, packet, ids=[evidence["probeId"]]).ok

    try:
        decision = assess(author)
        assert decision["status"] == "changes_required"
        assert len(audits) == 1
        assert decision["requirements"][-1]["status"] == "missing"
        assert review._cached[decision["patchDigest"]]["status"] == "changes_required"
        assert len(commands) == 2
        assert (tmp_path / "stock.py").read_text() == broken
    finally:
        capability.close()


def test_audit_approval_cannot_erase_new_failed_acceptance_challenge(tmp_path, monkeypatch):
    challenged = []

    def coverage(tools, child, packet):
        result, evidence = _probe(tools, child, packet,
            "from stock import release\ndef test_real_release():\n    assert release(3, 2) == 5\n")
        assert not result.ok and evidence["receipt"]["status"] == "failed"
        challenged.append(evidence["probeId"])
        pid = packet["evidenceIndex"][0]["probeId"]
        read = tools.execute("review_probe_read", {"probeId": pid, "receiptView": "current", "limit": 16000}, child)
        source = json.loads(json.loads(read.output)["text"])["source"]
        # Even an incorrect independent semantic judgment cannot override a
        # real failing retained challenge. The host, not this model, owns it.
        result = tools.execute("completion_coverage_audit_submit", _coverage_approval(packet, {pid: source}), child)
        assert result.ok, result.output

    review, assess, _commands, capability = _fixture(tmp_path, monkeypatch, coverage_driver=coverage)
    source = (tmp_path / "stock.py").read_text().replace("return n + amount", "return n")
    (tmp_path / "stock.py").write_text(source)

    def author(tools, child, packet):
        _result, evidence = _probe(tools, child, packet, WEAK[0])
        assert _submit(tools, child, packet, ids=[evidence["probeId"]]).ok

    try:
        decision = assess(author)
        assert decision["status"] == "changes_required"
        assert decision["coverageAudit"]["status"] == "approved"
        assert {item["probeId"] for item in decision["repairProbes"]} == set(challenged)
        assert (tmp_path / "stock.py").read_text() == source
    finally:
        capability.close()
