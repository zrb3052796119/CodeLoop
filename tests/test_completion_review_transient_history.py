"""Unavailable execution history must not permanently poison fixture repair."""

import minicode.implementation_completion_review as completion
from test_implementation_completion_review import _fixture, _probe, _rows, _submit


def test_retry_after_transient_unavailability_can_correct_a_real_fixture_error(tmp_path, monkeypatch):
    review, assess, _commands, capability = _fixture(tmp_path, monkeypatch)
    broken = ("from stock import hold, release\ndef test_inventory():\n"
              "    amount = int('invalid fixture')\n"
              "    assert hold(5, amount) == 3\n    assert release(3, amount) == 5\n")
    corrected = broken.replace("int('invalid fixture')", "int('2')")
    execute = completion.run_isolated_review_probe
    attempts = []

    def unavailable_once(**kwargs):
        attempts.append(kwargs["probe"].probe_id)
        if len(attempts) == 1:
            # Only the first host execution boundary is unavailable. The retry
            # and corrected probe below execute real managed pytest processes.
            return {"probeId": kwargs["probe"].probe_id,
                    "patchDigest": kwargs["frozen"].patch_digest,
                    "status": "inconclusive", "reason": "review_probe_execution_unavailable",
                    "evidence": {"failureReason": "timeout"}, "testReport": None}
        return execute(**kwargs)

    monkeypatch.setattr(completion, "run_isolated_review_probe", unavailable_once)
    retained = []

    def first(tools, child, packet):
        result, receipt = _probe(tools, child, packet, broken)
        assert not result.ok and receipt["receipt"]["status"] == "inconclusive"
        retained.append(receipt["probeId"])
        assert _submit(tools, child, packet, rows=_rows(packet, status="unknown"),
                       status="inconclusive", findings=["The independent check timed out."]).ok

    def second(tools, child, packet):
        # The unchanged candidate's retained probe actually retried and failed
        # in the test body. A future valid correction must not erase that trace.
        assert packet["retainedProbes"][0]["status"] == "failed"
        fixed, replacement = _probe(tools, child, packet, corrected,
            correctsProbeId=retained[0],
            correctionReason="The public operations accept integer quantities; the test fixture parsed invalid text.",
            sourceEvidence=[{"path": "stock.py", "startLine": 1, "endLine": 5}])
        assert fixed.ok, (
            "An earlier unavailable attempt must not permanently block an explicit "
            "fixture correction after a real classified failure: " + fixed.output)
        assert _submit(tools, child, packet, ids=[replacement["probeId"]]).ok

    try:
        assert assess(first)["status"] == "inconclusive"
        result = assess(second)
        assert result["status"] == "approved"
        states = review.state
        assert [row["receipt"]["status"] for row in states["receipts"]] == [
            "inconclusive", "failed", "passed"]
        assert states["retiredProbeIds"] == retained
        assert len(states["corrections"]) == 1
    finally:
        capability.close()
