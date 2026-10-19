"""Real pytest execution cannot be replaced by convincing coverage prose."""
from copy import deepcopy

import pytest

import test_completion_coverage_audit as fixtures


def _submit(case, monkeypatch, value=None):
    requests = fixtures._native(monkeypatch, case, [fixtures._read(case),
        ("completion_coverage_audit_submit", value or case["valid"])])
    result = case["assess"]()
    assert len(requests) == 2  # A coverage gap exits review, not a format retry loop.
    return result


def test_uncalled_assertion_with_passing_pytest_returns_repair(tmp_path, monkeypatch):
    code = ("from stock import hold, release\n"
            "def unused_assertion():\n    assert release(3, 2) == 5\n"
            "def test_hold():\n    assert hold(5, 2) == 3\n")
    case = fixtures._case(tmp_path, monkeypatch, code=code,
        product=fixtures.PRODUCT.replace("return n + amount", "return n"))
    value = deepcopy(case["valid"])
    for row in value["requirements"]:
        row["witnesses"][0].update(startLine=3, endLine=3)
    result = _submit(case, monkeypatch, value)
    assert result["status"] == "changes_required"
    assert any("actually executes" in finding for finding in result["findings"])
    assert case["receipt"]["testReport"]["passed"] == 1


PRODUCT = ("class Ledger:\n"
           "    def reserve(self, stock, amount):\n        return stock - amount\n\n"
           "def checkout(stock, amount):\n    return Ledger().reserve(stock, amount)\n")
OBJECTIVE = "1. Actually invoke `checkout` to call `Ledger.reserve` and assert the returned stock.\n"


@pytest.mark.parametrize("mode", ["helper", "substitute", "actual"])
def test_required_candidate_call_chain_is_observed(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(fixtures, "OBJECTIVE", OBJECTIVE)
    code = {"helper": "from stock import Ledger\ndef test_checkout():\n    assert Ledger().reserve(8, 3) == 5\n",
            "substitute": ("from stock import Ledger, checkout\ndef test_checkout(monkeypatch):\n"
                "    monkeypatch.setattr(Ledger, 'reserve', lambda self, n, a: n-a)\n    assert checkout(8, 3) == 5\n"),
            "actual": "from stock import checkout\ndef test_checkout():\n    assert checkout(8, 3) == 5\n"}[mode]
    case = fixtures._case(tmp_path, monkeypatch, code=code, product=PRODUCT)
    value = deepcopy(case["valid"])
    for row in value["requirements"]:
        row["witnesses"][0].update(startLine=len(code.splitlines()), endLine=len(code.splitlines()))
    from minicode.requirement_obligations import build_obligation_manifest
    manifest = build_obligation_manifest(OBJECTIVE, case["units"])
    value["requirements"][0]["obligations"] = [
        {"id": child["id"], "reason": "The checkout result is observed at the public entry.",
         "witnesses": deepcopy(value["requirements"][0]["witnesses"])}
        for child in manifest["obligations"]]
    result = _submit(case, monkeypatch, value)
    assert result["status"] == ("approved" if mode == "actual" else "changes_required")
    if mode != "actual":
        assert any("checkout -> Ledger.reserve" in reason for reason in result["findings"])
    else:
        assert result["requirements"][0]["witnesses"][0]["executedAssertions"]


def test_missing_parallel_obligation_is_a_repair_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, "OBJECTIVE", "1. hold subtracts the amount; release adds the amount.\n")
    case = fixtures._case(tmp_path, monkeypatch)
    result = _submit(case, monkeypatch)
    assert result["status"] == "changes_required"
    assert any("sub-obligation" in reason for reason in result["findings"])


def test_each_obligation_binds_its_executed_assertion(tmp_path, monkeypatch):
    from minicode.requirement_obligations import build_obligation_manifest
    monkeypatch.setattr(fixtures, "OBJECTIVE", "1. hold subtracts the amount; release adds the amount.\n")
    case = fixtures._case(tmp_path, monkeypatch)
    manifest = build_obligation_manifest(fixtures.OBJECTIVE, case["units"])
    assert len(manifest["obligations"]) == 2
    value = deepcopy(case["valid"])
    value["requirements"][0]["obligations"] = [
        {"id": child["id"], "reason": "The returned stock is asserted for the requested operation.",
         "witnesses": [{"probeId": case["probe"].probe_id, "startLine": index+3, "endLine": index+3}]}
        for index, child in enumerate(manifest["obligations"])]
    result = _submit(case, monkeypatch, value)
    assert result["status"] == "approved"
    assert len(result["requirements"][0]["obligations"]) == 2
