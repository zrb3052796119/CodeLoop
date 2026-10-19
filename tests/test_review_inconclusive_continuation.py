"""Uncertain review coverage must not hide a concrete, accepted defect."""

import json

import pytest

from minicode.workflow_repair import review_supports_implementation_repair
from test_reviewer_result_submission import _bound, _execute, _rejection


def test_accepted_inconclusive_defect_is_actionable_for_existing_executor():
    _assignment, store, channel, tool, _ = _bound()
    value = _rejection()
    value["verdict"] = "inconclusive"
    value["requestedChecks"] = ["Exercise the missing public failure path."]
    result = _execute(tool, channel, value)
    assert result.ok, result.output
    document = json.loads(store.read(channel.write_key).content)
    assert document["verdict"] == "inconclusive"
    assert {item["status"] for item in document["criteria"]} == {"failed", "unknown"}
    assert review_supports_implementation_repair(document)


@pytest.mark.parametrize("statuses,findings", [
    (["unknown"], []),
    (["satisfied"], []),
    (["failed"], []),
    (["failed"], [{"severity": "high", "statement": "  "}]),
    (["failed"], [{"severity": "critical", "statement": "Needs new authority."}]),
])
def test_inconclusive_without_safe_concrete_defect_does_not_authorize_repair(statuses, findings):
    assert not review_supports_implementation_repair({
        "verdict": "inconclusive", "criteria": [{"status": status} for status in statuses],
        "blockingFindings": findings, "requestedChecks": [],
    })


def test_inconclusive_known_defect_reaches_same_executor_and_fresh_gates(tmp_path, monkeypatch):
    import test_workflow_completion_coverage as workflow

    original = workflow._tool
    submitted = []

    def native_review(name, values, call_id):
        if call_id == "review-verdict":
            verdict = json.loads(values["content"])
            negative = verdict["verdict"] == "changes_required"
            values = {
                "verdict": "inconclusive" if negative else "approved",
                "criteria": [{"criterionId": item["criterionId"], "status": item["status"]}
                             for item in verdict["criteria"] if not negative or item["status"] == "failed"],
                "blockingFindings": [{"criterionIds": ["none"], "statement": item["statement"]}
                                     for item in verdict["blockingFindings"]],
                "warnings": [], "requestedChecks": ["Assert the public None path."] if negative else [],
                "confidence": "high",
            }
            submitted.append(values["verdict"])
            name = "review_submit"
        return original(name, values, call_id)

    monkeypatch.setattr(workflow, "_tool", native_review)
    workflow.test_green_partial_candidate_is_reviewed_before_commit_and_resumes_same_executor(
        tmp_path, monkeypatch, repair=True, durable=True,
    )
    assert submitted == ["inconclusive", "approved"]
