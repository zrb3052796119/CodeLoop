"""An invalid independent harness may be corrected without retiring a real defect.

All test runs, candidate writes, result channels and audit/promotion are real;
model choices are scripted on an unrelated public miniature cache requirement.
"""

import json

import pytest

from test_review_requirement_challenge import COMPLETE, REPAIRED_TEST, _exercise_blind_approval


@pytest.mark.parametrize("failure_kind", ["exception", "assertion"])
def test_corrected_probe_harness_preserves_real_defect_and_original_executor(tmp_path, monkeypatch, failure_kind):
    result, trace, audit, _journal, _run_id, calls, checks, workers, models = _exercise_blind_approval(
        tmp_path, monkeypatch, independent_challenge=True, harness_correction=True,
        harness_failure_kind=failure_kind)
    assert result.ok and trace["committed"], result.output
    assert (tmp_path / "cache.py").read_text() == COMPLETE
    assert (tmp_path / "test_regression.py").read_text() == REPAIRED_TEST
    executors = [entry for entry in workers if entry["role"] == "executor"]
    assert len(executors) == 2 and executors[0]["session"] is executors[1]["session"]
    assert executors[0]["model"] is executors[1]["model"]
    assert executors[0]["afterContext"] is executors[1]["beforeContext"]
    assert sum(kind == "general" for kind, _ in models) == 1
    documents = {artifact.artifact_id: json.loads(artifact.content) for artifact in audit.artifacts}
    corrections = [doc for doc in documents.values() if doc.get("resultType") == "ReviewProbeCorrection.v1"]
    assert len(corrections) == 1
    correction = corrections[0]
    old_source, new_source = (documents[correction[key]] for key in ("oldSourceRef", "newSourceRef"))
    invalid_setup = "Cache(None)" if failure_kind == "exception" else "Cache(lambda _: 0)"
    assert invalid_setup in old_source["code"]
    assert new_source["code"] == old_source["code"].replace(invalid_setup, "Cache(lambda _: 7)")
    assert 'assert cache.get("a") == 7' in old_source["code"]
    executions = sorted((doc for doc in documents.values() if doc.get("resultType") == "ReviewProbeExecution.v1"),
                        key=lambda doc: doc["sequence"])
    old_attempts = [doc for doc in executions if doc["probeId"] == correction["oldProbeId"]]
    assert len(old_attempts) == 1 and old_attempts[0]["status"] == "failed"
    assert [case["failureKind"] for case in old_attempts[0]["testReport"]["caseReport"]["cases"]] == [failure_kind]
    assert old_attempts[0] == documents[correction["oldReceiptRefs"][0]]
    new_attempts = [doc for doc in executions if doc["probeId"] == correction["newProbeId"]]
    assert len(new_attempts) == 4 and all(doc["status"] == "passed" for doc in new_attempts)
    defect_attempts = [doc for doc in executions if doc["probeId"] not in {correction["oldProbeId"], correction["newProbeId"]}]
    assert [doc["status"] for doc in defect_attempts] == ["failed", "failed", "passed", "passed"]
    assert [doc["evidence"]["exitCode"] for doc in defect_attempts] == [1, 1, 0, 0]
    assert all(any(case["failureKind"] == "assertion" for case in doc["testReport"]["caseReport"]["cases"])
               for doc in defect_attempts[:2])
    assert len({doc["probeId"] for doc in defect_attempts}) == 1
    assert [item["exit"] for item in checks if item["formal"]] == [0, 0, 0, 0]
    correction_ref = next(ref for ref, doc in documents.items() if doc is correction)
    repairs = [doc for doc in documents.values() if doc.get("resultType") == "ImplementationRepairRequest.v1"]
    assert len(repairs) == 1 and correction_ref in repairs[0]["evidenceRefs"]
    executor_messages = [messages for role, messages in calls if role == "executor"]
    assert correction_ref in str(executor_messages[2])
    assert sum(item["outcome"] == "repairing" for item in audit.trace["transitions"]) == 1
