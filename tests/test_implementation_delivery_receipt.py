"""Host checks, not final model phrasing, determine incomplete delivery."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_execution import ImplementationSpec
from minicode.implementation_phases import ImplementationPhaseController
from minicode.permissions import PermissionManager
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep, ModelAdapter


def _run_delivery(tmp_path, monkeypatch, *, phases=True, repaired=False, unverified=False,
                  final_text="Finishing the two failing tests now…", presentation_fails=False,
                  final_error=None, outcome_capture=None, presented_capture=None):
    import minicode.workflow_runner as runner

    assert Path(run_agent_turn.__code__.co_filename).resolve().parents[1] == Path(__file__).resolve().parents[1]

    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / "test_app.py").write_text(
        "from app import value\n"
        "def test_one(): assert value == 3\n"
        "def test_two(): assert value == 3\n"
    )
    commands = []
    requests = []
    request_tools = []
    assessments = []
    expected_calls = 1 if unverified else 3 if repaired else 2
    presented = presented_capture if presented_capture is not None else []
    phase_request = ImplementationPhaseController.request

    def command(_argv, *, check=False, **kwargs):
        commands.append(Path(kwargs["cwd"]))
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "test_app.py"], check=check, **kwargs)

    def closing_request(self, snapshot, **kwargs):
        if self.calls >= (2 if repaired else 1):
            kwargs["now"] = max(kwargs["now"], self.final_start)
            # Closing receipts are tested at the actual local call boundary;
            # a soft time estimate must not pretend that repair is exhausted.
            self.call_limit = min(self.call_limit, self.calls + 1)
        return phase_request(self, snapshot, **kwargs)

    def present(content):
        presented.append(content)
        if presentation_fails and "IMPLEMENTATION DELIVERY RECEIPT" in content:
            raise RuntimeError("display unavailable")

    class Model(ModelAdapter):
        def next(self, messages, tools_override=None, **_kwargs):
            requests.append(list(messages))
            request_tools.append({tool.name for tool in tools_override.list()})
            turn = len(requests)
            if not unverified and (turn == 1 or repaired and turn == 2):
                return AgentStep(type="tool_calls", calls=[{
                    "id": f"edit-{turn}", "toolName": "edit_file",
                    "input": {"path": "app.py", "old": f"value = {turn}", "new": f"value = {turn + 1}"},
                }])
            assert turn == (1 if unverified else 3 if repaired else 2), "no extra model call is allowed"
            if phases and not unverified:
                assert not tools_override.list(), "this final request has no execution tools"
            if final_error is not None:
                raise final_error
            return AgentStep(type="assistant", kind="final", content=final_text)

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    monkeypatch.setattr(ImplementationPhaseController, "request", closing_request)
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.implementation_execution import implementation_candidate_digest

    def assess(review, *, checks_receipt, context, **_kwargs):
        # Isolate semantic coverage assessment; real source, subprocess checks
        # and current-candidate evidence must still justify accepted delivery.
        digest = implementation_candidate_digest(review.spec, context)
        checks = checks_receipt.get("checks", [])
        observed = {
            "sourceMatches": (Path(context.cwd) / "app.py").read_text() == "value = 3\n",
            "checksPassed": bool(checks) and checks_receipt.get("status") == "passed"
                and all(check.get("status") == "passed" for check in checks),
            "digestMatches": bool(digest) and checks_receipt.get("patchDigest") == digest
                and all(check.get("patchDigest") == digest for check in checks),
        }
        assessments.append(observed)
        return {"status": "approved" if all(observed.values()) else "changes_required",
                "patchDigest": digest, "findings": [] if all(observed.values())
                else ["Current source and passing checks do not establish the requested repair."]}

    monkeypatch.setattr(ImplementationCompletionReview, "assess", assess)
    outcome = outcome_capture or AgentOutcomeCapture()
    spec = ImplementationSpec(("app.py", "test_app.py"), (VerificationCheck(
        "public", "tests", ("python3", "-m", "pytest", "-q", "test_app.py"), 20),), "Fix app.py")
    messages = run_agent_turn(model=Model(), tools=ToolRegistry([edit_file_tool, write_file_tool]),
        messages=[{"role": "user", "content": "Fix app.py and run the public tests."}],
        cwd=str(tmp_path), implementation_task=True, implementation_spec=spec,
        enable_implementation_phases=phases, bounded_implementation_phases=True,
        enable_work_chain=False, max_steps=expected_calls,
        outcome_capture=outcome, on_assistant_message=present)
    assert len(requests) == expected_calls
    if phases and not unverified:
        assert request_tools[-1] == set(), "final receipt request must not regain execution tools"
    assert assessments == ([{"sourceMatches": True, "checksPassed": True, "digestMatches": True}]
                           if repaired else [])
    return messages, outcome.outcome, commands, requests, presented


@pytest.mark.parametrize("phases", [False, True])
@pytest.mark.parametrize("final_text", ["Finishing the two failing tests now…", "All fixed and tested."])
def test_direct_failed_checks_close_with_host_partial_receipt(tmp_path, monkeypatch, phases, final_text):
    messages, outcome, commands, requests, presented = _run_delivery(
        tmp_path, monkeypatch, phases=phases, final_text=final_text)
    assert len(commands) == 1 and len(requests) == 2
    assert all(path != tmp_path and not path.exists() for path in commands)
    assert (tmp_path / "app.py").read_text() == "value = 2\n"
    evidence = next(m for m in messages if m.get("role") == "tool_result" and m.get("toolUseId") == "edit-1")
    assert "2 failed" in evidence["content"] and '"status": "failed"' in evidence["content"]
    assert any(m.get("_rejected_completion") and m.get("content") == final_text for m in messages)
    assert final_text not in presented, "failed completion text must not be presented as accepted delivery"
    receipts = [m for m in messages if m.get("_host_implementation_receipt")]
    assert len(receipts) == 1, "the model's final wording hid actual failing public checks"
    assert receipts[0]["_partial"] is True
    receipt = json.loads(receipts[0]["content"].split("=== IMPLEMENTATION DELIVERY RECEIPT v1 ===\n")[1])
    assert receipt == {"implementationDeliveryVersion": 1, "executionEnded": True,
        "delivery": "partial", "lastPublicChecks": "failed", "requirementsAccepted": False,
        "completionReview": "rejected", "continuesAfterResponse": False}
    assert presented[-1] == receipts[0]["content"]
    assert outcome.status == "failed" and not outcome.completion_succeeded
    assert not outcome.goal_achieved


def test_host_receipt_does_not_repeat_repaired_and_accepted_check_failures(tmp_path, monkeypatch):
    messages, outcome, commands, requests, _presented = _run_delivery(
        tmp_path, monkeypatch, repaired=True)
    assert len(commands) == 2
    assert len(requests) == 3
    assert not any(m.get("_host_implementation_receipt") for m in messages)
    assert outcome.status == "success"
    evidence = next(m for m in messages if m.get("toolUseId") == "edit-2" and m.get("role") == "tool_result")
    assert "2 passed" in evidence["content"]
    assert (tmp_path / "app.py").read_text() == "value = 3\n"


def test_final_without_edits_runs_required_checks_and_reports_partial(tmp_path, monkeypatch):
    messages, outcome, commands, requests, presented = _run_delivery(
        tmp_path, monkeypatch, unverified=True)
    assert len(commands) == len(requests) == 1
    assert (tmp_path / "app.py").read_text() == "value = 1\n"
    assert all(path != tmp_path and not path.exists() for path in commands)
    receipts = [m for m in messages if m.get("_host_implementation_receipt")]
    assert len(receipts) == 1 and receipts[0]["_partial"] is True
    receipt = json.loads(receipts[0]["content"].split("=== IMPLEMENTATION DELIVERY RECEIPT v1 ===\n")[1])
    assert receipt["lastPublicChecks"] == "failed"
    assert receipt["requirementsAccepted"] is False
    assert receipt["continuesAfterResponse"] is False
    assert presented[-1] == receipts[0]["content"]
    assert outcome.status == "failed" and not outcome.completion_succeeded


def test_host_partial_receipt_survives_display_failure(tmp_path, monkeypatch):
    messages, outcome, *_ = _run_delivery(tmp_path, monkeypatch, presentation_fails=True)
    assert messages[-1].get("_host_implementation_receipt") is True
    assert outcome.status == "failed"


def test_host_partial_receipt_does_not_mask_original_model_exception(tmp_path, monkeypatch):
    original = KeyboardInterrupt("original execution interruption")
    with pytest.raises(KeyboardInterrupt) as caught:
        _run_delivery(tmp_path, monkeypatch, final_error=original, presentation_fails=True)
    assert caught.value is original


def test_host_partial_receipt_keeps_cancelled_outcome_and_exception(tmp_path, monkeypatch):
    from minicode.turn_cancellation import TurnCancellationRequested

    outcome = AgentOutcomeCapture()
    original = TurnCancellationRequested("turn_" + "1" * 32)
    presented = []
    with pytest.raises(TurnCancellationRequested) as caught:
        _run_delivery(tmp_path, monkeypatch, final_error=original,
                      outcome_capture=outcome, presentation_fails=True, presented_capture=presented)
    assert caught.value is original
    assert outcome.outcome.status == "cancelled"
    assert not outcome.outcome.completion_succeeded
    assert any("IMPLEMENTATION DELIVERY RECEIPT" in content for content in presented)
