"""Rejected native submissions survive interruption without becoming approval."""

import copy
import json

import pytest

from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_execution import direct_verification_capability
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.read_file import read_file_tool
from minicode.types import AgentStep
from test_implementation_completion_review import PROBE, _fixture, _rows, _native_coverage_step, _inspection_step


def _call(identity, name, value):
    return AgentStep(type="tool_calls", calls=[{"id": identity, "toolName": name, "input": value}])


@pytest.mark.parametrize("changed_candidate", [False, True])
def test_rejected_native_draft_is_retained_and_revalidated_after_interruption(
        tmp_path, monkeypatch, changed_candidate):
    review, _unused, commands, cap = _fixture(tmp_path, monkeypatch)
    context = ToolContext(cwd=str(tmp_path), _runtime={"model": "draft-test", "reflectionMode": "rule"})
    tools = ToolRegistry([read_file_tool])
    submitted = {}

    class InterruptedReviewer:
        model_id = "draft-test"

        def __init__(self):
            self.calls = 0

        def next(self, messages, **kwargs):
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            self.calls += 1
            if self.calls == 1:
                return _call("probe", "review_test", {"purpose": "acceptance",
                    "criterionIds": [r["id"] for r in review.requirements],
                    "scenario": "Both stock operations satisfy their public contract", "code": PROBE})
            if self.calls == 2:
                result = next(m for m in messages
                    if m.get("role") == "tool_result" and m.get("toolUseId") == "probe")
                receipt = json.JSONDecoder().raw_decode(result["content"])[0]
                submitted.update(status="approved", findings=["Both operations passed the acceptance test."],
                    requirements=_rows({"requirements": review.requirements}, ids=[receipt["probeId"]]))
                return _call("submit", "completion_review_submit", submitted)
            assert self.calls == 3
            result = next(m for m in messages
                if m.get("role") == "tool_result" and m.get("toolUseId") == "submit")
            assert result["isError"]
            raise KeyboardInterrupt("preserve the rejected native draft")

    def assess(instance, model, checks):
        return instance.assess(model=model, tools=tools, context=context, agent_budget=None,
            event_sink=None, cancellation_token=None, deadline_monotonic=None, checks_receipt=checks)

    try:
        with pytest.raises(KeyboardInterrupt):
            assess(review, InterruptedReviewer(), cap.ensure_completion_checks(context))
        state = review.state
        draft = state["pendingSubmissionDraft"]
        assert draft["status"] == "unaccepted"
        assert json.loads(draft["started"]["rawJson"])["input"] == submitted
        assert state["reviews"] == []
        cap.close()
        if changed_candidate:
            with (tmp_path / "stock.py").open("a") as handle:
                handle.write("\n# changed candidate\n")
        restored = ImplementationCompletionReview.from_state(review.spec, state)
        cap = direct_verification_capability(restored.spec)
        before = len(commands)

        class ResumedReviewer:
            model_id = "draft-test"

            def next(self, messages, **kwargs):
                inspection_step = _inspection_step(messages, kwargs["tools_override"])
                if inspection_step is not None:
                    return inspection_step
                audit_step = _native_coverage_step(messages)
                if audit_step is not None:
                    return audit_step
                packet = next(json.loads(m["content"]) for m in messages
                    if m.get("role") == "user" and '"retainedProbes"' in m.get("content", ""))
                assert len(commands) == before + 2, "public and retained acceptance must actually rerun"
                assert packet["retainedProbes"][0]["status"] == "passed"
                if changed_candidate:
                    assert "unacceptedSubmissionDraft" not in packet
                else:
                    pending = packet["unacceptedSubmissionDraft"]
                    assert pending["status"] == "unaccepted" and pending["input"] == submitted
                    assert pending["output"] == json.loads(draft["finished"]["rawJson"])["output"]
                    assert "rawJson" not in json.dumps(pending)
                value = copy.deepcopy(submitted)
                value["findings"] = []
                return _call("fixed-submit", "completion_review_submit", value)

        result = assess(restored, ResumedReviewer(), cap.ensure_completion_checks(context))
        assert result["status"] == "approved"
        assert restored.state["pendingSubmissionDraft"] is None
    finally:
        cap.close()
