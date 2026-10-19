"""Unfinished inspection stays with its worker until a real finding can be repaired."""

import json

import pytest

from test_review_requirement_challenge import COMPLETE, REPAIRED_TEST, _exercise_blind_approval


@pytest.mark.parametrize("submission", ["native", "legacy"])
def test_unknown_review_resumes_inspection_then_repairs_with_original_executor(tmp_path, monkeypatch, submission):
    result, trace, audit, journal, run_id, calls, checks, workers, models = _exercise_blind_approval(
        tmp_path, monkeypatch, independent_challenge=True,
        pending_before_probe=submission, pending_rounds=3)
    assert result.ok and trace["committed"], result.output
    assert (tmp_path / "cache.py").read_text() == COMPLETE
    assert (tmp_path / "test_regression.py").read_text() == REPAIRED_TEST
    executors = [item for item in workers if item["role"] == "executor"]
    assert len(executors) == 2
    assert executors[0]["session"] is executors[1]["session"]
    assert executors[0]["model"] is executors[1]["model"]
    assert executors[0]["afterContext"] is executors[1]["beforeContext"]
    assert sum(kind == "general" for kind, _ in models) == 1
    # Exactly one reviewer per candidate revision; pending work did not close
    # the initial reviewer or create a new workflow/model to start over.
    assert len([item for item in workers if item["role"] == "reviewer"]) == 2
    assert [role for role, _ in calls] == (["executor"] * 2 + ["reviewer"] * 5
                                        + ["executor"] * 3 + ["reviewer"])
    assert [item["exit"] for item in checks if item["formal"]] == [0, 0, 0, 0]
    assert sum(item["outcome"] == "repairing" for item in audit.trace["transitions"]) == 1
    assert journal.get_scheduler_commit_ready(run_id, trace["workflowId"]) is not None
    documents = [json.loads(artifact.content) for artifact in audit.artifacts]
    verdicts = [doc for doc in documents if doc.get("resultType") == "ReviewVerdict.v1"]
    assert not any(doc["verdict"] == "inconclusive" for doc in verdicts)
    probes = sorted((doc for doc in documents if doc.get("resultType") == "ReviewProbeExecution.v1"),
                    key=lambda doc: doc["sequence"])
    assert [doc["status"] for doc in probes] == ["failed", "failed", "passed", "passed"]
