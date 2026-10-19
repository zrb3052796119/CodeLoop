"""An actionable partial review must resume the same real Executor loop."""
import json

import pytest

import test_workflow_completion_coverage as coverage
from minicode.workflow_repair import review_supports_implementation_repair
from minicode.workflow_runner import _reviewer_deadline


@pytest.mark.parametrize("additional_finding", [False, True])
def test_native_partial_verdict_repairs_and_requires_full_final_review(tmp_path, monkeypatch, additional_finding):
    original_tool = coverage._tool
    original_step = coverage.AgentStep
    submissions = []
    closing_calls = []

    def observed_step(*args, **kwargs):
        if kwargs.get("kind") == "final":
            closing_calls.append(kwargs)
        return original_step(*args, **kwargs)

    def native_tool(name, values, call_id):
        if call_id == "review-verdict":
            verdict = json.loads(values["content"])
            failed = verdict["verdict"] == "changes_required"
            criteria = [{"criterionId": item["criterionId"], "status": item["status"]}
                        for item in verdict["criteria"]
                        if not failed or additional_finding or item["status"] == "failed"]
            values = {
                "verdict": verdict["verdict"], "criteria": criteria,
                "findings": [{"criterionIds": ["none"], "statement": item["statement"]}
                             for item in verdict["blockingFindings"]],
                "warnings": [], "requestedChecks": [], "confidence": "high",
            }
            if failed and additional_finding:
                values["findings"].append({
                    "criterionIds": ["trim"],
                    "statement": "The whitespace behavior has a passing assertion.",
                })
            submissions.append(values)
            name = "review_submit"
        return original_tool(name, values, call_id)

    monkeypatch.setattr(coverage, "_tool", native_tool)
    monkeypatch.setattr(coverage, "AgentStep", observed_step)
    # Runs the real tools, pytest checks, transaction, persistent Executor,
    # durable journal reload, final verifier, review and commit. Only model
    # decisions are scripted. The initial native verdict leaves trim unknown.
    coverage.test_green_partial_candidate_is_reviewed_before_commit_and_resumes_same_executor(
        tmp_path, monkeypatch, repair=True, durable=True,
    )
    assert [item["verdict"] for item in submissions] == ["changes_required", "approved"]
    assert {item["criterionId"]: item["status"] for item in submissions[0]["criteria"]} == (
        {"trim": "satisfied", "none": "failed"}
        if additional_finding else {"none": "failed"}
    )
    assert {item["criterionId"] for item in submissions[1]["criteria"]} == {"trim", "none"}
    assert closing_calls == [], "Typed submission must not need an extra closing model call."


@pytest.mark.parametrize("finding", [[], [{"severity": "high", "statement": ""}]])
def test_unknowns_with_no_actionable_finding_do_not_request_repair(finding):
    assert not review_supports_implementation_repair({
        "verdict": "changes_required", "criteria": [{"status": "failed"}, {"status": "unknown"}],
        "blockingFindings": finding, "requestedChecks": [],
    })


def test_review_deadline_leaves_repair_and_owner_delivery_inside_original_600_seconds():
    initial = _reviewer_deadline(600.0, 427.541, first_review=True)
    assert initial == pytest.approx(543.950825)
    assert 427.541 < initial < 600.0
    # After repair the final verdict still cannot take the owner's last turn.
    final = _reviewer_deadline(600.0, 570.0, first_review=False)
    assert final == 597.0
    assert _reviewer_deadline(600.0, 600.0, first_review=True) == 600.0
