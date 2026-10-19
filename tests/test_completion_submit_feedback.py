"""Precise completion feedback keeps the gate closed and directs the next action."""

import json

import pytest

from test_implementation_completion_review import _fixture, _probe, _rows, _submit, PROBE, SOURCE


POSITIVE = "The independent check confirms both inventory operations."


def blockers(result):
    assert not result.ok
    document = json.loads(result.output)
    assert document["errorCode"] == "completion_approval_blocked"
    return document["blockers"]


def test_positive_findings_are_identified_without_retesting_or_changing_the_candidate(tmp_path, monkeypatch):
    review, assess, commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        ran, receipt = _probe(tools, child, packet)
        assert ran.ok and packet["publicChecks"]["status"] == "passed"
        rows = _rows(packet, ids=[receipt["probeId"]])
        findings = [POSITIVE]
        rejected = _submit(tools, child, packet, rows=rows, findings=findings)
        assert blockers(rejected) == {
            "findings": [{"index": 0, "text": POSITIVE}],
            "uncoveredRequirements": [], "unpassedRetainedProbes": [],
            "missingRetainedAcceptance": False,
        }
        assert findings == [POSITIVE] and not review.state["reviews"]
        schema = next(tool.input_schema for tool in tools.list() if tool.name == "completion_review_submit")
        description = schema["properties"]["findings"]["description"].lower()
        assert all(term in description for term in ("blocking", "approved", "empty", "rationale"))
        assert len(commands) == 2
        assert _submit(tools, child, packet, rows=rows).ok
        assert len(commands) == 2, "A submission-field correction needs neither new tests nor product edits."

    try:
        assert assess(driver)["status"] == "approved"
        assert (tmp_path / "stock.py").read_text() == SOURCE
    finally:
        capability.close()


@pytest.mark.parametrize("status", ["missing", "unknown"])
def test_noncovered_requirement_has_its_exact_id_and_declared_status(tmp_path, monkeypatch, status):
    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        ran, receipt = _probe(tools, child, packet)
        assert ran.ok
        rows = _rows(packet, ids=[receipt["probeId"]])
        rows[-1]["status"] = status
        observed = blockers(_submit(tools, child, packet, rows=rows))
        assert observed == {
            "findings": [], "uncoveredRequirements": [{"id": rows[-1]["id"], "status": status}],
            "unpassedRetainedProbes": [], "missingRetainedAcceptance": False,
        }
        assert _submit(tools, child, packet, ids=[receipt["probeId"]]).ok

    try:
        assert assess(driver)["status"] == "approved"
    finally:
        capability.close()


@pytest.mark.parametrize("unpassed", ["failed", "inconclusive"])
def test_unpassed_acceptance_is_identified_and_diagnostic_is_not_a_blocker(tmp_path, monkeypatch, unpassed):
    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    if unpassed == "failed":
        (tmp_path / "stock.py").write_text(SOURCE.replace("return n + amount", "return n"))

    def driver(tools, child, packet):
        # A passing narrow probe can support a reviewer's declared rows, but a
        # separate retained counterexample must still prevent approval.
        passed, pass_receipt = _probe(tools, child, packet,
            "from stock import hold\ndef test_hold():\n    assert hold(5, 2) == 3\n")
        assert passed.ok
        diagnostic, diagnostic_receipt = _probe(tools, child, packet,
            "def test_observation():\n    assert False\n", purpose="diagnostic", scenario="Record an obsolete observation")
        assert not diagnostic.ok and diagnostic_receipt["receipt"]["status"] == "failed"
        code = ("from stock import release\ndef test_release():\n    assert release(3, 2) == 5\n"
                if unpassed == "failed" else
                "import pytest\n@pytest.mark.skip(reason='execution unavailable')\ndef test_release():\n    assert True\n")
        failed, failed_receipt = _probe(tools, child, packet, code)
        assert not failed.ok and failed_receipt["receipt"]["status"] == unpassed
        observed = blockers(_submit(tools, child, packet, ids=[pass_receipt["probeId"]]))
        assert observed["findings"] == [] and observed["uncoveredRequirements"] == []
        assert observed["unpassedRetainedProbes"] == [{"probeId": failed_receipt["probeId"], "status": unpassed}]
        assert not observed["missingRetainedAcceptance"]
        assert diagnostic_receipt["probeId"] not in json.dumps(observed)
        assert _submit(tools, child, packet, status="changes_required" if unpassed == "failed" else "inconclusive",
            rows=_rows(packet, status="missing"), findings=["release still needs verified evidence."]).ok

    try:
        assert assess(driver)["status"] == ("changes_required" if unpassed == "failed" else "inconclusive")
    finally:
        capability.close()


def test_all_approval_blockers_are_reported_together_without_dropping_findings(tmp_path, monkeypatch):
    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        rows = _rows(packet, status="unknown")
        findings = ["Need executed acceptance evidence.", POSITIVE]
        observed = blockers(_submit(tools, child, packet, rows=rows, findings=findings))
        assert observed == {
            "findings": [{"index": index, "text": text} for index, text in enumerate(findings)],
            "uncoveredRequirements": [{"id": row["id"], "status": "unknown"} for row in rows],
            "unpassedRetainedProbes": [], "missingRetainedAcceptance": True,
        }
        assert _submit(tools, child, packet, status="inconclusive", rows=rows, findings=findings).ok

    try:
        assert assess(driver)["status"] == "inconclusive"
    finally:
        capability.close()


def test_invalid_coverage_evidence_also_reports_the_other_current_blockers(tmp_path, monkeypatch):
    _review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        rows = _rows(packet)
        rejected = _submit(tools, child, packet, rows=rows, findings=[POSITIVE])
        assert not rejected.ok
        error = json.loads(rejected.output)
        assert error["errorCode"] == "completion_coverage_incomplete"
        assert {row["id"] for row in error["requirementErrors"]} == {row["id"] for row in rows}
        assert error["blockers"] == {
            "findings": [{"index": 0, "text": POSITIVE}],
            "uncoveredRequirements": [{"id": row["id"], "status": "unknown"} for row in rows],
            "unpassedRetainedProbes": [], "missingRetainedAcceptance": True,
        }
        assert _submit(tools, child, packet, status="inconclusive", rows=_rows(packet, status="unknown"),
            findings=["No independent evidence was executed."]).ok

    try:
        assert assess(driver)["status"] == "inconclusive"
    finally:
        capability.close()


@pytest.mark.parametrize("remove,extra,missing,unexpected", [
    (["purpose"], {}, ["purpose"], []),
    ([], {"summary": "notes"}, [], ["summary"]),
    ([], {"correctsProbeId": "review_probe_unknown"}, ["correctionReason", "sourceEvidence"], []),
    (["code"], {"purposeHint": "acceptance"}, ["code"], ["purposeHint"]),
])
def test_review_test_field_errors_are_exact_and_do_not_execute(tmp_path, monkeypatch, remove, extra, missing, unexpected):
    _review, assess, commands, capability = _fixture(tmp_path, monkeypatch)

    def driver(tools, child, packet):
        value = {"purpose": "acceptance", "criterionIds": [row["id"] for row in packet["requirements"]],
                 "scenario": "Both inventory operations", "code": PROBE, **extra}
        for name in remove:
            del value[name]
        result = tools.execute("review_test", value, child)
        assert not result.ok
        error = json.loads(result.output)
        assert error["errorCode"] == "completion_review_invalid"
        assert error["tool"] == "review_test"
        assert error["missingFields"] == missing and error["unexpectedFields"] == unexpected
        assert len(commands) == 1
        valid, receipt = _probe(tools, child, packet)
        assert valid.ok and _submit(tools, child, packet, ids=[receipt["probeId"]]).ok

    try:
        assert assess(driver)["status"] == "approved"
    finally:
        capability.close()
