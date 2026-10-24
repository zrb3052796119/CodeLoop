"""Additional safety boundaries for the isolated candidate, without any model."""

import json

import pytest

from test_nonapproval_completion_feedback import PROBE, inventory as inventory_fixture, row, submit

inventory = inventory_fixture


@pytest.mark.parametrize("invalid", [
    "missing_id", "bad_shape", "unknown_probe", "path_escape",
    "unsafe_after_incomplete", "empty_findings",
])
def test_nonapproval_does_not_absorb_identity_schema_or_scope_errors(inventory, invalid):
    _root, _review, _checks, _context, ids, assess, _calls = inventory

    def driver(tools, child):
        rows = [row(identity, "unknown") for identity in ids]
        findings = None
        if invalid == "missing_id":
            rows[0]["id"] = "invented-requirement"
        elif invalid == "bad_shape":
            rows[0]["unexpected"] = True
        elif invalid == "unknown_probe":
            rows[0]["probeIds"] = ["invented-probe"]
        elif invalid in {"path_escape", "unsafe_after_incomplete"}:
            rows[0]["sourceEvidence"] = [{"path": "../outside.py", "startLine": 1, "endLine": 1}]
            if invalid == "unsafe_after_incomplete":
                rows[0]["sourceEvidence"].insert(0, {"path": "stock.py", "startLine": 1, "endLine": 999})
        else:
            findings = []
        result = submit(tools, child, rows, findings=findings)
        assert not result.ok and not result._implementation_submitted, result.output

    assert assess(driver)["status"] != "approved"


def test_failed_current_probe_returns_as_feedback_but_cannot_approve(inventory):
    _root, _review, _checks, _context, ids, assess, _calls = inventory
    delivered_decisions = []

    def driver(tools, child):
        evidence = tools.execute("review_test", {"purpose": "acceptance", "criterionIds": ids,
            "scenario": "Inventory release must restore the quantity.", "code": PROBE}, child)
        receipt = json.loads(evidence.output)
        assert receipt["receipt"]["status"] == "failed"
        rows = [row(identity, "covered", probes=[receipt["probeId"]]) for identity in ids]
        accepted = submit(tools, child, rows, status="approved")
        # A confirmed failure terminates with authoritative repair feedback,
        # even when the caller attempts to approve it. Tool success denotes
        # receipt of that negative decision, never coverage approval.
        assert accepted.ok and accepted._implementation_submitted, accepted.output
        decision = json.loads(accepted.output)
        assert decision["status"] == "changes_required"
        assert decision["reason"] == "confirmed_product_gap"
        delivered_decisions.append(decision)

    result = assess(driver)
    assert result["status"] == "changes_required"
    assert result["reason"] == "confirmed_product_gap"
    assert len(delivered_decisions) == 1
    assert not any(item.get("status") == "covered" for item in result.get("requirements", []))
    assert result["failureAdjudications"][0]["verdict"] == "product_gap"
    assert _calls == ["review_boundary", "adjudication_boundary"]
    assert result["repairProbes"][0]["code"] == PROBE
    assert result["probeReceipts"][0]["receipt"]["status"] == "failed"
