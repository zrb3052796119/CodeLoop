"""Resume an interrupted independent audit inside the original implementation.

Only semantic model decisions are scripted. Native parent/child loops, writes,
isolated pytest, evidence delivery, checkpoints and final gates are real.
"""

from copy import deepcopy
import json

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.implementation_completion_review import requirement_units
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.types import AgentStep
import test_completion_feedback_chronology as chronology
from test_completion_feedback_chronology import COMPLETE, OBJECTIVE, PARTIAL, PROBE, _call, _result
from test_implementation_completion_review import _native_coverage_step, _inspection_step


inventory = chronology.inventory


def _probe_commands(case):
    return [row for row in case["commands"] if "test_public.py" not in row["argv"]]


@pytest.mark.parametrize("rejection", ["positive-findings", "outside-source"])
@pytest.mark.parametrize("repair_after_resume", [False, True], ids=["submit-correction", "then-implementation-repair"])
def test_same_owner_resumes_audit_evidence_then_runs_final_checks(inventory, rejection, repair_after_resume, monkeypatch):
    # This full evidence-delivery conversation deliberately exceeds the bounded cap.
    monkeypatch.setenv("MINI_CODE_COMPLETION_REVIEW_MODE", "full")
    case = inventory
    ids = [row["id"] for row in requirement_units(OBJECTIVE)]
    initial_source = PARTIAL if repair_after_resume else COMPLETE
    retained_work = "\n# Existing partial work must survive the interrupted audit.\n"
    initial_source += retained_work
    final_source = COMPLETE + retained_work
    # A weak passed test may be an author's proposed evidence, but the resumed
    # independent auditor must still be able to reject its semantic adequacy.
    code = (PROBE.replace("    assert release(7, 3) == 10\n", "") if repair_after_resume else PROBE)
    code += "# Retained generic inventory test context.\n" * 500
    child_messages = []

    class Seed:
        parent_calls = 0
        reviewer_calls = 0
        rejected = False

        def next(self, messages, **kwargs):
            inspection_step = _inspection_step(messages, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs["tools_override"].list_all())
            if "completion_coverage_audit_submit" in names:
                if self.rejected:
                    assert _result(messages, "rejected-audit")["isError"]
                    child_messages[:] = deepcopy(messages)
                    raise KeyboardInterrupt("preserve the real rejected audit and all delivered pages")
                step = _native_coverage_step(messages, stable_evidence=True)
                if step.calls[0]["toolName"] == "completion_coverage_audit_submit":
                    value = step.calls[0]["input"]
                    if rejection == "positive-findings":
                        value["findings"] = ["Both quantity operations passed."]
                    else:
                        value["requirements"][0]["witnesses"][0]["endLine"] = len(code.splitlines()) + 1
                    step.calls[0]["id"] = "rejected-audit"
                    self.rejected = True
                return step
            if "completion_review_submit" in names:
                self.reviewer_calls += 1
                if self.reviewer_calls == 1:
                    return _call("actual-probe", "review_test", purpose="acceptance", criterionIds=ids,
                        scenario="Both actual inventory operations satisfy their public contract.", code=code)
                assert self.reviewer_calls == 2
                receipt = json.JSONDecoder().raw_decode(_result(messages, "actual-probe")["content"])[0]
                assert receipt["receipt"]["status"] == "passed"
                return _call("proposal", "completion_review_submit", status="approved", findings=[],
                    requirements=[{"id": identity, "status": "covered", "probeIds": [receipt["probeId"]],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
                        "rationale": "The original quantity assertions passed on the candidate."} for identity in ids])
            self.parent_calls += 1
            if self.parent_calls == 1:
                return _call("original-read", "read_file", path="stock.py")
            if self.parent_calls == 2:
                return _call("original-edit", "write_file", path="stock.py", content=initial_source)
            assert self.parent_calls == 3
            return AgentStep(type="assistant", kind="final", content="Quantity operations are ready.")

    seed = Seed()
    with pytest.raises(KeyboardInterrupt, match="preserve the real rejected audit"):
        run_agent_turn(model=seed, messages=[{"role": "user", "content": OBJECTIVE}], **case["options"])
    document, messages = deepcopy(case["checkpoints"][-1])
    original_read = deepcopy(_result(messages, "original-read"))
    original_edit = deepcopy(_result(messages, "original-edit"))
    saved_probe_count = len(_probe_commands(case))
    restored = HostImplementationContinuation.from_document(document, spec=case["spec"], messages=messages)

    class Resume:
        auditor_calls = 0
        parent_calls = 0
        reviewer_calls = 0

        def next(self, actual, **kwargs):
            inspection_step = _inspection_step(actual, kwargs["tools_override"])
            if inspection_step is not None:
                return inspection_step
            names = set(kwargs["tools_override"].list_all())
            if "completion_review_submit" in names:
                assert repair_after_resume and self.parent_calls >= 3, "The original author review must not restart."
                self.reviewer_calls += 1
                if self.reviewer_calls == 1:
                    return _call("both-after-repair", "review_test", purpose="acceptance", criterionIds=ids,
                        scenario="Actual hold and release results after the implementation repair.", code=PROBE)
                assert self.reviewer_calls == 2
                receipt = json.JSONDecoder().raw_decode(_result(actual, "both-after-repair")["content"])[0]
                assert receipt["receipt"]["status"] == "passed"
                return _call("repaired-proposal", "completion_review_submit", status="approved", findings=[],
                    requirements=[{"id": identity, "status": "covered", "probeIds": [receipt["probeId"]],
                        "sourceEvidence": [{"path": "stock.py", "startLine": 1, "endLine": 5}],
                        "rationale": "The repaired original implementation passed both operations."} for identity in ids])
            if "completion_coverage_audit_submit" in names:
                self.auditor_calls += 1
                if self.auditor_calls == 1:
                    assert actual[:len(child_messages)] == child_messages
                    assert len(_probe_commands(case)) == saved_probe_count, "Do not change receipt refs before resuming audit."
                step = _native_coverage_step(actual, stable_evidence=True)
                if self.auditor_calls == 1:
                    assert step.calls[0]["toolName"] == "completion_coverage_audit_submit"
                    if repair_after_resume:
                        step.calls[0]["input"] = {"status": "changes_required",
                            "requirements": [{"id": ids[1], "status": "missing", "witnesses": [],
                                "reason": "The proposed test never checks release, and release still returns n."}],
                            "findings": ["Implement release addition and test its actual returned quantity."]}
                else:
                    assert repair_after_resume and self.parent_calls >= 3
                return step
            self.parent_calls += 1
            assert _result(actual, "original-read") == original_read
            assert _result(actual, "original-edit") == original_edit
            if repair_after_resume:
                if self.parent_calls == 1:
                    assert (case["workspace"] / "stock.py").read_text() == initial_source
                    assert any(m.get("_implementation_completion_feedback") for m in actual)
                    return _call("resumed-repair", "write_file", path="stock.py", content=final_source)
                if self.parent_calls == 2:
                    assert _result(actual, "resumed-repair")["isError"] is False
                    return _call("resumed-check", "executor_check", check=1)
                assert self.parent_calls == 3
                assert _result(actual, "resumed-check")["isError"] is False
            else:
                assert self.parent_calls == 1
            return AgentStep(type="assistant", kind="final", content="Both quantity operations are verified.")

    resume = Resume()
    result = run_agent_turn(model=resume, messages=messages, implementation_continuation=restored, **case["options"])
    assert resume.auditor_calls >= 1
    if not repair_after_resume:
        assert resume.auditor_calls == 1
    assert len(_probe_commands(case)) > saved_probe_count, "Restoring an audit must not waive final probe execution."
    assert (case["workspace"] / "stock.py").read_text() == final_source
    assert _result(result, "original-edit") == original_edit
    starts = [row["payload"] for row in case["events"] if row["type"] == "implementation.completion_review_started"]
    decisions = [row["payload"] for row in case["events"] if row["type"] == "implementation.completion_decided"]
    # A resumed approval is checked again when the parent emits its final;
    # the existing same-candidate cache emits a decision, not another audit.
    assert len(starts) == 3
    assert len({row["executorId"] for row in starts}) == 1
    assert [row["status"] for row in decisions] == (
        ["changes_required", "approved"] if repair_after_resume else ["approved", "approved"])
    assert decisions[-1]["status"] == "approved" and decisions[-1]["executorId"] == starts[0]["executorId"]
    assert result[-1]["role"] == "assistant"
