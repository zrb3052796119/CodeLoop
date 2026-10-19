"""A public requirement omitted from passing checks must retain its Executor.

This is an independent inventory example, not a benchmark fixture or oracle.
Only the model responses are scripted. Candidate writes, isolated snapshots,
pytest execution, receipt collection and the loop's terminal state are real.
"""

from dataclasses import asdict
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_execution import ImplementationSpec
from minicode.permissions import PermissionManager
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from minicode.turn_cancellation import TurnCancellationRequested, TurnCancellationToken


OBJECTIVE = """Implement both public inventory operations in stock.py:
1. hold(available, amount) returns available minus amount.
2. release(available, amount) returns available plus amount.
Keep quantity(available) compatible. Add regression tests for both operations.
"""
INITIAL_SOURCE = """def quantity(available):
    return available

def hold(available, amount):
    return available

def release(available, amount):
    return available
"""
PARTIAL_TESTS = """from stock import hold

def test_hold():
    assert hold(10, 3) == 7
"""
COMPLETE_TESTS = PARTIAL_TESTS + """
from stock import release

def test_release():
    assert release(7, 3) == 10
"""
PREMATURE_FINAL = "Implemented both requested inventory operations; all checks passed."
COMPLETE_SOURCE = INITIAL_SOURCE.replace(
    "def hold(available, amount):\n    return available",
    "def hold(available, amount):\n    return available - amount",
).replace(
    "def release(available, amount):\n    return available",
    "def release(available, amount):\n    return available + amount",
)


def _tool(identity, name, **values):
    return {"id": identity, "toolName": name, "input": values}


def _run_missing_requirement(tmp_path, monkeypatch, final_type, *,
                             scenario="omitted", max_calls=None, cancel_review=False):
    import minicode.workflow_runner as runner

    (tmp_path / "stock.py").write_text(INITIAL_SOURCE)
    (tmp_path / "test_legacy.py").write_text(
        "from stock import quantity\ndef test_quantity():\n    assert quantity(10) == 10\n"
    )
    # No missing-file/collection failure can be confused with the actual gap.
    (tmp_path / "test_requested.py").write_text("def test_initial():\n    assert True\n")
    commands = []

    def command(argv, **kwargs):
        result = subprocess.run([sys.executable, *argv[1:]], **kwargs)
        commands.append({"argv": list(argv), "cwd": str(kwargs["cwd"]),
                         "exitCode": result.returncode, "stdout": result.stdout})
        return result

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)
    requests = []
    presented = []
    streamed = []
    reviews = []
    token = TurnCancellationToken("turn_" + "a" * 32)

    def review(_self, *, checks_receipt, context, **_kwargs):
        # Substitute only the independent semantic model boundary. All public
        # verification commands, digest calculation and state transitions are
        # production code. Findings refer exclusively to the original request.
        assert checks_receipt["status"] == "passed"
        assert context.cwd == str(tmp_path)
        source = (tmp_path / "stock.py").read_text()
        namespace = {}
        exec(compile(source, "stock.py", "exec"), namespace)
        covered = namespace["hold"](10, 3) == 7 and namespace["release"](7, 3) == 10
        covered = covered and "def test_release" in (tmp_path / "test_requested.py").read_text()
        verdict = {"status": "approved" if covered else "changes_required",
                   "patchDigest": checks_receipt["patchDigest"],
                   "requirements": [
                       {"id": "hold", "text": "hold subtracts the amount", "status": "satisfied"},
                       {"id": "release", "text": "release adds the amount and has a regression",
                        "status": "satisfied" if covered else "missing"},
                   ],
                   "findings": [] if covered else [
                       "release remains a no-op and its requested regression is absent."]}
        reviews.append(verdict)
        if cancel_review:
            token.request()
            token.raise_if_requested()
        if scenario == "stale" and len(reviews) == 1:
            assert covered
            # A concurrent owner edits bytes after approval's observed digest.
            # The gate must reject this obsolete approval and re-run checks.
            (tmp_path / "stock.py").write_text(source.replace(
                "return available + amount", "return available"))
        return verdict

    # Retain the exact same behavioral test on a pre-fix production snapshot.
    # There, no assessment boundary exists and the terminal-state assertion is
    # the red failure, rather than an ImportError caused by the new fixture.
    try:
        review_module = importlib.import_module("minicode.implementation_completion_review")
    except ModuleNotFoundError as error:
        if error.name != "minicode.implementation_completion_review":
            raise
    else:
        monkeypatch.setattr(review_module.ImplementationCompletionReview, "assess", review)

    class Executor:
        def next(self, messages, **_kwargs):
            requests.append(list(messages))
            if scenario == "no_initial_write":
                if len(requests) == 1:
                    return AgentStep(type=final_type, kind="final", content=PREMATURE_FINAL)
                if len(requests) == 2:
                    assert reviews and reviews[-1]["status"] == "changes_required"
                    assert len(commands) == 2 and all(c["exitCode"] == 0 for c in commands)
                    assert (tmp_path / "stock.py").read_text() == INITIAL_SOURCE
                    return AgentStep(type="tool_calls", calls=[
                        _tool("complete-both", "write_file", path="stock.py", content=COMPLETE_SOURCE),
                        _tool("test-both", "write_file", path="test_requested.py", content=COMPLETE_TESTS),
                    ])
                assert len(requests) == 3
                return AgentStep(type=final_type, kind="final", content="Both operations are implemented and tested.")
            if len(requests) == 1:
                if scenario == "stale":
                    return AgentStep(type="tool_calls", calls=[
                        _tool("implement-hold", "write_file", path="stock.py", content=COMPLETE_SOURCE),
                        _tool("test-only-hold", "write_file", path="test_requested.py", content=COMPLETE_TESTS),
                    ])
                calls = [
                    _tool("implement-hold", "edit_file", path="stock.py",
                          old="def hold(available, amount):\n    return available",
                          new="def hold(available, amount):\n    return available "
                              + ("+ amount" if scenario == "public_failed" else "- amount")),
                    _tool("test-only-hold", "write_file", path="test_requested.py",
                          content=PARTIAL_TESTS),
                ]
                if scenario == "failed_edit":
                    calls.append(_tool("failed-release", "edit_file", path="stock.py",
                        old="def release(available, amount):\n    return missing_anchor",
                        new="def release(available, amount):\n    return available + amount"))
                return AgentStep(type="tool_calls", calls=calls)
            if len(requests) == 2:
                stream = _kwargs.get("on_stream_chunk")
                if callable(stream):
                    stream(PREMATURE_FINAL)
                return AgentStep(type=final_type, kind="final", content=PREMATURE_FINAL)
            if len(requests) == 3:
                # A genuine continuation keeps the exact candidate and earlier
                # tool receipts. A restarted Executor would fail these checks.
                assert "def hold" in (tmp_path / "stock.py").read_text()
                assert (tmp_path / "test_requested.py").read_text() == (
                    COMPLETE_TESTS if scenario == "stale" else PARTIAL_TESTS)
                prior_tools = [m for m in messages if m.get("role") == "tool_result"]
                assert any(m.get("toolUseId") == "implement-hold" for m in prior_tools)
                status = "failed" if scenario == "public_failed" else "passed"
                assert any(f'"status":"{status}"' in m.get("content", "").replace(" ", "")
                           for m in messages if m.get("_implementation_batch_feedback"))
                assert sum(bool(m.get("_implementation_context")) for m in messages) == 1
                if scenario == "public_failed":
                    assert reviews == [], "failed public checks must not spend a semantic review"
                if scenario == "failed_edit":
                    assert any(m.get("toolUseId") == "failed-release" and m.get("isError")
                               for m in prior_tools), "the failed final edit must survive continuation"
                return AgentStep(type="tool_calls", calls=[
                    _tool("complete-release", "write_file", path="stock.py", content=COMPLETE_SOURCE),
                    _tool("test-both", "write_file", path="test_requested.py",
                          content=COMPLETE_TESTS),
                ])
            assert len(requests) == 4, "completion must not spin after covered delivery"
            return AgentStep(type=final_type, kind="final", content="Both operations are implemented and tested.")

    model = Executor()
    capture = AgentOutcomeCapture()
    budget = AgentTurnBudget(max_model_calls=max_calls, max_total_tokens=None, max_cost_usd=None)
    spec = ImplementationSpec(("stock.py", "test_requested.py"), (
        VerificationCheck("public_existing", "tests", ("python3", "-m", "pytest", "-q", "test_legacy.py"), 20),
        VerificationCheck("public_regression", "tests", ("python3", "-m", "pytest", "-q", "test_requested.py"), 20),
    ), OBJECTIVE)
    cancelled = False
    try:
        messages = run_agent_turn(model=model, tools=ToolRegistry([edit_file_tool, write_file_tool]),
            messages=[{"role": "user", "content": OBJECTIVE}], cwd=str(tmp_path),
            implementation_task=True, implementation_spec=spec, enable_work_chain=False,
            max_steps=None, agent_budget=budget, outcome_capture=capture,
            cancellation_token=token, on_assistant_message=presented.append,
            on_assistant_stream_chunk=streamed.append)
    except TurnCancellationRequested:
        cancelled = True
        messages = []
    scope = {}
    exec(compile((tmp_path / "stock.py").read_text(), str(tmp_path / "stock.py"), "exec"), scope)
    feedback = [m for m in messages if m.get("_implementation_batch_feedback")]
    witness = {"modelRequests": len(requests), "checkProcesses": len(commands),
               "hostOutcome": asdict(capture.outcome),
               "holdResult": scope["hold"](10, 3), "releaseResult": scope["release"](7, 3),
               "expectedRelease": 10, "publicPassedFeedback": any(
                   '"status":"passed"' in m.get("content", "").replace(" ", "") for m in feedback),
               "allCheckProcessesPassed": bool(commands) and all(c["exitCode"] == 0 for c in commands),
               "prematureFinalPresented": PREMATURE_FINAL in presented,
               "prematureFinalStreamed": PREMATURE_FINAL in streamed,
               "limitTokens": budget.snapshot().limit_total_tokens,
               "limitCalls": budget.snapshot().limit_model_calls,
               "reviews": reviews, "cancelled": cancelled,
               "isolatedChecks": all(Path(c["cwd"]) != tmp_path for c in commands)}
    (tmp_path / "completion-gap-witness.json").write_text(json.dumps(witness, indent=2) + "\n")
    return witness, messages, commands


@pytest.mark.parametrize("final_type", ["assistant", "tool_calls"])
def test_green_old_and_self_checks_cannot_deliver_omitted_public_requirement(
        tmp_path, monkeypatch, final_type):
    witness, messages, commands = _run_missing_requirement(tmp_path, monkeypatch, final_type)
    assert witness["publicPassedFeedback"], witness
    assert witness["allCheckProcessesPassed"], witness
    assert witness["isolatedChecks"], witness
    assert witness["limitTokens"] is None and witness["limitCalls"] is None
    assert witness["modelRequests"] == 4, (
        "The loop accepted green legacy/self-authored tests and a final claim despite "
        "the explicitly requested release operation remaining a no-op. It must retain "
        "this Executor and return the unmet requirement for repair. " + json.dumps(witness)
    )
    assert witness["holdResult"] == 7 and witness["releaseResult"] == 10
    assert not witness["prematureFinalPresented"]
    assert not witness["prematureFinalStreamed"], "unapproved final text must not escape by streaming"
    assert witness["hostOutcome"]["status"] == "success"
    assert all(not Path(c["cwd"]).exists() for c in commands)
    assert messages[-1]["content"] == "Both operations are implemented and tested."
    assert [r["status"] for r in witness["reviews"]] == ["changes_required", "approved"]


@pytest.mark.parametrize("scenario", ["public_failed", "failed_edit", "stale"])
def test_completion_repair_retains_candidate_receipts_and_executor(tmp_path, monkeypatch, scenario):
    witness, messages, commands = _run_missing_requirement(
        tmp_path, monkeypatch, "assistant", scenario=scenario)
    assert witness["modelRequests"] == 4, witness
    assert witness["holdResult"] == 7 and witness["releaseResult"] == 10
    assert witness["hostOutcome"]["status"] == "success"
    assert not witness["prematureFinalPresented"]
    assert not witness["prematureFinalStreamed"]
    assert len(witness["reviews"]) == (1 if scenario == "public_failed" else 2)
    assert witness["reviews"][-1]["status"] == "approved"
    if scenario == "stale":
        assert witness["reviews"][0]["status"] == "approved", "the test must exercise stale approval"
    if scenario == "public_failed":
        assert any(c["exitCode"] != 0 for c in commands), "a real public test must fail"
        assert commands[-1]["exitCode"] == 0
    assert messages[-1]["content"] == "Both operations are implemented and tested."


def test_explicit_budget_end_cannot_promote_incomplete_requirement(tmp_path, monkeypatch):
    witness, _messages, _commands = _run_missing_requirement(
        tmp_path, monkeypatch, "assistant", max_calls=2)
    assert witness["modelRequests"] == 2, witness
    assert witness["releaseResult"] == 7, "the real incomplete candidate must remain available"
    assert witness["hostOutcome"]["status"] != "success", witness
    assert not witness["hostOutcome"]["completion_succeeded"]
    assert not witness["prematureFinalPresented"]
    assert not witness["prematureFinalStreamed"]


def test_cancelled_completion_review_keeps_candidate_without_success(tmp_path, monkeypatch):
    witness, _messages, _commands = _run_missing_requirement(
        tmp_path, monkeypatch, "assistant", cancel_review=True)
    assert witness["cancelled"], witness
    assert witness["modelRequests"] == 2
    assert witness["holdResult"] == 7 and witness["releaseResult"] == 7
    assert witness["hostOutcome"]["status"] == "cancelled", witness
    assert not witness["prematureFinalPresented"]
    assert not witness["prematureFinalStreamed"]


def test_final_without_prior_writes_executes_missing_checks_before_review(tmp_path, monkeypatch):
    witness, messages, commands = _run_missing_requirement(
        tmp_path, monkeypatch, "assistant", scenario="no_initial_write")
    assert witness["modelRequests"] == 3, witness
    assert witness["checkProcesses"] == 4
    assert witness["allCheckProcessesPassed"]
    assert witness["holdResult"] == 7 and witness["releaseResult"] == 10
    assert [r["status"] for r in witness["reviews"]] == ["changes_required", "approved"]
    assert not witness["prematureFinalPresented"]
    assert witness["hostOutcome"]["status"] == "success"
    assert messages[-1]["content"] == "Both operations are implemented and tested."
    assert all(not Path(c["cwd"]).exists() for c in commands)
