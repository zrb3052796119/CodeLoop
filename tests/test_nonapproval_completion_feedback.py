"""Local generic protocol regression; no model/provider is called.

The inventory fixture and test source are independent of the live evaluation.
Only the review loop boundary is driven directly; checks and probes run real
managed pytest processes with the verifier's disposable HOME and workspaces.
"""

import hashlib
import json
from pathlib import Path
import sys

import pytest

from minicode.coverage_audit_state import record_document
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_execution import ImplementationSpec, direct_verification_capability
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from test_completion_coverage_audit import _inspection_step
from test_implementation_completion_review import _DeliveredRegistry, _coverage_approval, _coverage_probe_ids


PROBE = ("from stock import hold, release\ndef test_operations():\n"
         "    assert hold(10, 3) == 7\n    assert release(7, 3) == 10\n")


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    import minicode.agent_loop as loop
    import minicode.workflow_runner as runner

    (tmp_path / "stock.py").write_text(
        "def hold(n, amount):\n    return n - amount\n\n"
        "def release(n, amount):\n    return n\n")
    (tmp_path / "test_public.py").write_text(
        "from stock import hold\ndef test_hold():\n    assert hold(10, 3) == 7\n")
    launches = []

    class Sandbox:
        def wrap_command(self, *, argv, cwd, verifier_home, **_kwargs):
            launches.append({"cwd": str(cwd), "home": verifier_home})
            assert Path(cwd) != tmp_path
            assert Path(verifier_home).is_dir() and Path(verifier_home) != Path.home()
            return argv

    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(sandbox_backend=Sandbox(),
                            explicit_trusted_executables={"python3": sys.executable}))
    spec = ImplementationSpec(("stock.py", "test_public.py"), (
        VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 20),
    ), "1. hold subtracts the amount.\n2. release adds the amount.")
    context = ToolContext(cwd=str(tmp_path))
    checks = direct_verification_capability(spec)
    review = ImplementationCompletionReview(spec)
    ids = [unit["id"] for unit in review.requirements]
    calls = []

    def assess(driver, *, receipt=None):
        def drive(**values):
            tools = values["tools"]
            child = ToolContext(cwd=values["cwd"], permissions=values["permissions"])
            if "completion_coverage_inspection_submit" in tools.list_all():
                calls.append("inspection_boundary")
                tools = _DeliveredRegistry(tools, values["context_manager"].messages)
                while True:
                    step = _inspection_step(values["context_manager"].messages, tools)
                    assert step is not None and len(step.calls) == 1
                    call = step.calls[0]
                    result = tools.execute(call["toolName"], call["input"], child)
                    assert result.ok, result.output
                    if call["toolName"] == "completion_coverage_inspection_submit":
                        assert json.loads(result.output)["grantsCoverage"] is False
                        return
            if "completion_coverage_audit_submit" in tools.list_all():
                tools = _DeliveredRegistry(tools, values["context_manager"].messages)
                calls.append("coverage_boundary")
                assert set(tools.list_all()) == {
                    "read_file", "review_test", "review_probe_read", "review_evidence_read",
                    "completion_coverage_audit_submit", "completion_evidence_read"}
                packets = [json.loads(message["content"]) for message in values["messages"]
                           if message.get("role") == "user" and '"evidenceIndex"' in message.get("content", "")]
                assert len(packets) == 1
                packet = packets[0]
                sources = {}
                for probe_id in _coverage_probe_ids(packet):
                    offset, pages = 0, []
                    while True:
                        result = tools.execute("review_probe_read", {
                            "probeId": probe_id, "receiptView": "current",
                            "offset": offset, "limit": 16000}, child)
                        assert result.ok, result.output
                        page = json.loads(result.output)
                        pages.append(page["text"])
                        if page["nextOffset"] is None:
                            break
                        offset = page["nextOffset"]
                    sources[probe_id] = json.loads("".join(pages))["source"]
                result = tools.execute("completion_coverage_audit_submit",
                    _coverage_approval(packet, sources), child)
                assert result.ok and result._implementation_submitted, result.output
                return
            if "completion_probe_adjudication_submit" in tools.list_all():
                calls.append("adjudication_boundary")
                assert set(tools.list_all()) == {"read_file", "completion_probe_adjudication_submit", "completion_evidence_read"}
                packet = json.loads(values["messages"][1]["content"])
                assert packet["originalObjective"] == spec.objective
                assert packet["probe"]["code"] == PROBE
                assert packet["receipt"]["receipt"]["status"] == "failed"
                source = tools.execute("read_file", {"path": "stock.py"}, child)
                assert source.ok, source.output
                assert "return n" in source.output and "return n + amount" not in source.output
                result = tools.execute("completion_probe_adjudication_submit", {
                    "verdict": "product_gap", "reason": "release returns the unchanged count rather than adding the amount.",
                    "objectiveEvidence": [{"startLine": 2, "endLine": 2}],
                    "sourceEvidence": [{"path": "stock.py", "startLine": 4, "endLine": 5}]}, child)
                assert result.ok and result._implementation_submitted, result.output
                return
            calls.append("review_boundary")
            return driver(tools, child)
        monkeypatch.setattr(loop, "run_agent_turn", drive)
        current = checks.ensure_completion_checks(context) if receipt is None else receipt
        assert current["status"] == "passed", current
        return review.assess(model=object(), tools=ToolRegistry([read_file_tool]), context=context,
            agent_budget=None, event_sink=None, cancellation_token=None, deadline_monotonic=None,
            checks_receipt=current)

    try:
        yield tmp_path, review, checks, context, ids, assess, calls
    finally:
        checks.close()
        assert launches and all(not Path(item["cwd"]).exists() and not Path(item["home"]).exists()
                                for item in launches)


def row(identity, status, *, probes=()):
    return {"id": identity, "status": status, "sourceEvidence": (
        [{"path": "stock.py", "startLine": 1, "endLine": 5}] if status == "covered" else []),
        "probeIds": list(probes), "rationale": "The source contains the operation; independent coverage is incomplete."}


def submit(tools, child, rows, status="changes_required", findings=None):
    return tools.execute("completion_review_submit", {
        "status": status, "requirements": rows,
        "findings": (["release(7, 3) remains 7; the requested addition is absent."]
                     if findings is None and status != "approved" else findings or []),
    }, child)


@pytest.mark.parametrize("other_issue", ["unproved_covered", "invalid_source_range"])
def test_known_omission_reaches_executor_without_proving_unrelated_positive_claims(inventory, other_issue):
    _root, _review, _checks, _context, ids, assess, _calls = inventory

    def driver(tools, child):
        rows = [row(ids[0], "covered"), row(ids[1], "missing")]
        if other_issue == "invalid_source_range":
            rows[0]["sourceEvidence"][0]["endLine"] = 999
        result = submit(tools, child, rows)
        assert result.ok and result._implementation_submitted, (
            "An actionable non-approval must reach the same Executor; unproved "
            "positive claims must remain unknown instead of blocking all findings. " + result.output)
    result = assess(driver)
    assert result["status"] == "changes_required"
    assert {item["id"] for item in result["requirements"]} == set(ids)
    assert result["requirements"][0]["status"] != "covered", "receiving feedback must not certify unproved coverage"
    assert result["findings"]


@pytest.mark.parametrize("invalid", ["no_probe", "wrong_requirement", "failed_probe", "invalid_range"])
def test_approval_still_rejects_missing_or_unbound_evidence(inventory, invalid):
    _root, _review, _checks, _context, ids, assess, _calls = inventory

    def driver(tools, child):
        probes = []
        if invalid in {"wrong_requirement", "failed_probe", "invalid_range"}:
            code = (PROBE if invalid == "failed_probe" else
                    "from stock import hold\ndef test_hold():\n    assert hold(10, 3) == 7\n")
            evidence = tools.execute("review_test", {"purpose": "acceptance",
                "criterionIds": ids[:1] if invalid == "wrong_requirement" else ids,
                "scenario": "Independent inventory behavior", "code": code}, child)
            receipt = json.loads(evidence.output)
            probes = [receipt["probeId"]]
            assert receipt["receipt"]["status"] == ("failed" if invalid == "failed_probe" else "passed")
        rows = [row(identity, "covered", probes=probes) for identity in ids]
        if invalid == "invalid_range":
            rows[0]["sourceEvidence"][0]["endLine"] = 999
        result = submit(tools, child, rows, status="approved")
        if invalid == "failed_probe":
            assert result.ok and result._implementation_submitted, result.output
            decision = json.loads(result.output)
            assert decision["status"] == "changes_required"
            assert decision["reason"] == "confirmed_product_gap"
        else:
            assert not result.ok and not result._implementation_submitted, result.output
    result = assess(driver)
    assert result["status"] != "approved"


def test_stale_candidate_check_receipt_cannot_reach_approval(inventory):
    root, review, checks, context, ids, assess, calls = inventory
    old = checks.ensure_completion_checks(context)
    (root / "stock.py").write_text((root / "stock.py").read_text() + "\n# later candidate\n")
    result = assess(lambda *_a: pytest.fail("stale check must fail before review"), receipt=old)
    assert result["status"] == "inconclusive" and calls == []
    assert not review.state["reviews"]


def test_current_protocol_can_recover_by_downgrading_unproved_coverage(inventory):
    root, review, _checks, _context, ids, assess, calls = inventory
    failed_probe = []

    def driver(tools, child):
        evidence = tools.execute("review_test", {"purpose": "acceptance", "criterionIds": ids,
            "scenario": "An independent assertion identifies the omitted inventory behavior.", "code": PROBE}, child)
        receipt = json.loads(evidence.output)
        assert receipt["receipt"]["status"] == "failed"
        failed_probe.append(receipt["probeId"])
        result = submit(tools, child, [row(ids[0], "unknown"), row(ids[1], "missing")])
        assert result.ok and result._implementation_submitted, result.output

    result = assess(driver)
    assert result["status"] == "changes_required"
    # Accepting the downgraded proposal preserves the independent authority;
    # its outer decision need not copy the adjudicator's reason code.
    assert calls == ["review_boundary", "adjudication_boundary"]
    assert len(review.state["failureAdjudications"]) == 1
    authority = review.state["failureAdjudications"][0]
    assert authority["verdict"] == "product_gap"
    assert authority["oldProbeId"] == failed_probe[0]
    assert authority["candidateDigest"] == result["patchDigest"]
    assert authority["objectiveEvidence"] == [{"startLine": 2, "endLine": 2,
        "lineDigest": "sha256:" + hashlib.sha256(b"2. release adds the amount.").hexdigest()}]
    assert authority["sourceEvidence"] == [{"path": "stock.py", "startLine": 4, "endLine": 5,
        "sourceDigest": "sha256:" + hashlib.sha256((root / "stock.py").read_bytes()).hexdigest(),
        "lineDigest": "sha256:" + hashlib.sha256(b"def release(n, amount):\n    return n\n").hexdigest()}]
    persisted = json.loads(Path(authority["adjudicationRef"]).read_text())
    assert persisted == {key: value for key, value in authority.items() if key != "adjudicationRef"}
    assert {item["id"] for item in result["requirements"]} == set(ids)
    assert all(item.get("status") != "covered" for item in result["requirements"])
    assert result["probeReceipts"][0]["probeId"] == failed_probe[0]
    assert result["probeReceipts"][0]["receipt"]["status"] == "failed"
    assert result["repairProbes"][0]["code"] == PROBE


def test_fully_evidenced_correct_candidate_can_still_be_approved(inventory):
    root, _review, _checks, _context, ids, assess, calls = inventory
    (root / "stock.py").write_text((root / "stock.py").read_text().replace(
        "def release(n, amount):\n    return n", "def release(n, amount):\n    return n + amount"))

    def driver(tools, child):
        evidence = tools.execute("review_test", {"purpose": "acceptance", "criterionIds": ids,
            "scenario": "Independent inventory behavior", "code": PROBE}, child)
        assert evidence.ok, evidence.output
        receipt = json.loads(evidence.output)
        assert receipt["receipt"]["status"] == "passed"
        result = submit(tools, child, [row(identity, "covered", probes=[receipt["probeId"]]) for identity in ids],
                        status="approved")
        assert result.ok and result._implementation_submitted, result.output
    result = assess(driver)
    assert result["status"] == "approved"
    audit = result["coverageAudit"]
    assert audit["status"] == "approved" and audit["inspectionVersion"] == 1
    inspection = record_document(audit["inspectionRecord"])
    assert inspection["phase"] == "reconciliation"
    assert inspection["result"]["status"] == "ready_for_evidence"
    assert len(inspection["reads"]) == 1
    read = inspection["reads"][0]
    assert read["path"] == "stock.py"
    assert read["sourceDigest"] == "sha256:" + hashlib.sha256((root / "stock.py").read_bytes()).hexdigest()
    assert read["result"]["isError"] is False
    assert "return n + amount" in read["result"]["content"]
    assert calls == ["review_boundary", "inspection_boundary", "coverage_boundary"]
