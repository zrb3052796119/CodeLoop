"""Scripted parent-loop demo: one reviewer, the same Executor, two repair rounds.

The inventory example is not a hidden oracle. Public checks cover hold. The
reviewer writes the release test the executor did not write. A transport
failure during review retries and still finishes.
"""

import json
from pathlib import Path
import subprocess
import sys
from urllib.error import URLError

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.behavior_review import BehaviorReview
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_execution import ImplementationSpec
from minicode.permissions import PermissionManager
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.edit_file import edit_file_tool
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep


OBJECTIVE = (
    "Implement both inventory operations in stock.py.\n"
    "hold(available, amount) returns available minus amount.\n"
    "release(available, amount) returns available plus amount.\n"
)
INITIAL = (
    "def hold(available, amount):\n"
    "    return available\n"
    "\n"
    "def release(available, amount):\n"
    "    return available\n"
)
HOLD = INITIAL.replace(
    "def hold(available, amount):\n    return available",
    "def hold(available, amount):\n    return available - amount",
)
BOTH = HOLD.replace(
    "def release(available, amount):\n    return available",
    "def release(available, amount):\n    return available + amount",
)
PUBLIC = "from stock import hold\n\ndef test_hold():\n    assert hold(10, 3) == 7\n"
RELEASE_PROBE = (
    "from stock import release\n\n"
    "def test_release():\n"
    "    assert release(7, 3) == 10\n"
)
WRONG_PROBE = RELEASE_PROBE.replace("== 10", "== 99")


def _verdict(status, gaps, code, name="test_release"):
    return json.dumps({
        "status": status,
        "gaps": gaps,
        "files": ["stock.py"],
        "probes": [{"name": name, "code": code}],
    })


def _tool(identity, name, **values):
    return {"id": identity, "toolName": name, "input": values}


def _install_verifier(monkeypatch):
    import minicode.workflow_runner as runner

    def command(argv, **kwargs):
        return subprocess.run([sys.executable, *argv[1:]], **kwargs)

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_args, **_kwargs: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_args, **_kwargs: None)


def _spec():
    return ImplementationSpec(("stock.py", "test_public.py"), (
        VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_public.py"), 30),
    ), OBJECTIVE, behavior_review=True)


class _Script:
    def __init__(self, root, *, transport_failure=False):
        self.root = root
        self.transport_failure = transport_failure
        self.transport_injected = False
        self.reviews = []
        self.executor_ids = []

    def next(self, messages, **_kwargs):
        if messages and messages[0].get("role") == "system" and "BEHAVIOR_REVIEW" in messages[0].get("content", ""):
            if self.transport_failure and not self.transport_injected:
                self.transport_injected = True
                raise URLError("injected tunnel failure")
            source = (self.root / "stock.py").read_text()
            if "return available + amount" in source:
                body = _verdict("pass", [], RELEASE_PROBE)
            else:
                body = _verdict("fail", ["release does not add the requested amount."], RELEASE_PROBE)
            self.reviews.append(json.loads(body)["status"])
            return AgentStep(type="assistant", kind="final", content=body)
        source = (self.root / "stock.py").read_text()
        feedback = "\n".join(message.get("content", "") for message in messages
                             if isinstance(message.get("content"), str))
        if "return available - amount" not in source:
            return AgentStep(type="tool_calls", calls=[
                _tool("implement-hold", "write_file", path="stock.py", content=HOLD),
                _tool("test-hold", "write_file", path="test_public.py", content=PUBLIC),
            ])
        if "Failing tests:" in feedback and "return available + amount" not in source:
            return AgentStep(type="tool_calls", calls=[
                _tool("implement-release", "write_file", path="stock.py", content=BOTH),
            ])
        return AgentStep(type="assistant", kind="final", content=(
            "Hold is implemented and the public hold test is in place.\n"
            "The same session is ready for another check."
        ))


def _run(tmp_path, monkeypatch, *, transport_failure=False):
    _install_verifier(monkeypatch)
    (tmp_path / "stock.py").write_text(INITIAL)
    (tmp_path / "test_public.py").write_text("def test_ready():\n    assert True\n")
    script = _Script(tmp_path, transport_failure=transport_failure)
    events = []

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            if event_type == "implementation.completion_decided":
                events.append(payload)

    capture = AgentOutcomeCapture()
    messages = run_agent_turn(
        model=script, tools=ToolRegistry([edit_file_tool, write_file_tool]),
        messages=[{"role": "user", "content": OBJECTIVE}], cwd=str(tmp_path),
        implementation_task=True, implementation_spec=_spec(), enable_work_chain=False,
        max_steps=None, agent_budget=AgentTurnBudget(max_model_calls=12),
        outcome_capture=capture, event_sink=Sink(), defer_curator_maintenance=True,
        enable_implementation_phases=False)
    return messages, events, capture, script


def test_same_executor_fixes_release_after_the_reviewer_probe(tmp_path, monkeypatch):
    messages, events, capture, script = _run(tmp_path, monkeypatch)
    scope = {}
    exec(compile((tmp_path / "stock.py").read_text(), "stock.py", "exec"), scope)
    blob = json.dumps(messages)
    assert scope["hold"](10, 3) == 7
    assert scope["release"](7, 3) == 10
    assert script.reviews == ["fail", "pass"]
    assert events[0]["status"] == "changes_required"
    assert events[0]["repairProbes"][0]["code"] == RELEASE_PROBE
    assert events[-1]["status"] == "approved"
    assert len({event["executorId"] for event in events}) == 1
    assert all(event.get("reason") != "failure_adjudication_inconclusive" for event in events)
    assert "failure_adjudication_inconclusive" not in blob
    assert capture.outcome.status == "success"
    assert "Failing tests:" in blob and "release does not add" in blob


def test_one_transport_failure_mid_review_still_completes(tmp_path, monkeypatch):
    _messages, events, capture, script = _run(tmp_path, monkeypatch, transport_failure=True)
    scope = {}
    exec(compile((tmp_path / "stock.py").read_text(), "stock.py", "exec"), scope)
    assert script.transport_injected
    assert scope["release"](7, 3) == 10
    assert events[-1]["status"] == "approved"
    assert capture.outcome.status == "success"
    assert all(event.get("reason") != "failure_adjudication_inconclusive" for event in events)
    assert all(event.get("canContinue") is not False for event in events)


def _direct(tmp_path, monkeypatch, model):
    _install_verifier(monkeypatch)
    (tmp_path / "stock.py").write_text(BOTH)
    (tmp_path / "test_public.py").write_text(PUBLIC)
    review = BehaviorReview(_spec())
    context = ToolContext(cwd=str(tmp_path), permissions=PermissionManager(str(tmp_path), prompt=None))
    from minicode.implementation_execution import direct_verification_capability
    checks = direct_verification_capability(_spec()).ensure_completion_checks(context)
    assert checks["status"] == "passed"

    def assess(**kwargs):
        return review.assess(model=model, tools=None, context=context, agent_budget=None,
                             event_sink=None, cancellation_token=None, deadline_monotonic=None,
                             checks_receipt=checks, **kwargs)

    return review, assess


def test_bad_format_gets_one_rewrite_then_a_passing_test(tmp_path, monkeypatch):
    replies = iter(["not json", _verdict("pass", [], RELEASE_PROBE)])

    class Model:
        def next(self, _messages, **_kwargs):
            return AgentStep(type="assistant", kind="final", content=next(replies))

    _review, assess = _direct(tmp_path, monkeypatch, Model())
    result = assess()
    assert result["status"] == "approved"
    assert result["repairProbes"] == []


def test_executor_pushback_makes_the_reviewer_fix_only_that_test(tmp_path, monkeypatch):
    seen = []

    class Model:
        def next(self, messages, **_kwargs):
            packet = messages[-1]["content"]
            seen.append(packet)
            code = RELEASE_PROBE if "Executor pushback:" in packet else WRONG_PROBE
            status = "pass" if code == RELEASE_PROBE else "fail"
            gaps = [] if status == "pass" else ["release does not add the requested amount."]
            return AgentStep(type="assistant", kind="final", content=_verdict(status, gaps, code))

    review, assess = _direct(tmp_path, monkeypatch, Model())
    first = assess()
    assert first["status"] == "changes_required"
    assert first["repairProbes"][0]["code"] == WRONG_PROBE
    second = assess(executor_note="The release test expects the wrong total.")
    assert "Executor pushback:" in seen[-1]
    assert second["status"] == "approved"
    assert review.repair_rounds == 1


def test_repair_cap_hands_to_a_human_and_transport_keeps_probes(tmp_path, monkeypatch):
    class AlwaysFail:
        def next(self, _messages, **_kwargs):
            return AgentStep(type="assistant", kind="final",
                             content=_verdict("fail", ["release is still missing."], RELEASE_PROBE))

    review, assess = _direct(tmp_path, monkeypatch, AlwaysFail())
    (tmp_path / "stock.py").write_text(HOLD)
    from minicode.implementation_execution import direct_verification_capability
    context = ToolContext(cwd=str(tmp_path), permissions=PermissionManager(str(tmp_path), prompt=None))
    checks = direct_verification_capability(_spec()).ensure_completion_checks(context)
    assert checks["status"] == "passed"

    def run():
        return review.assess(model=AlwaysFail(), tools=None, context=context, agent_budget=None,
                             event_sink=None, cancellation_token=None, deadline_monotonic=None,
                             checks_receipt=checks)

    first, second = run(), run()
    assert first["status"] == second["status"] == "changes_required"
    assert review.repair_rounds == 2
    third = run()
    assert third["status"] == "needs_human" and third["canContinue"] is False
    assert third["repairProbes"] and third["repairProbes"][0]["code"] == RELEASE_PROBE

    class Down:
        def next(self, _messages, **_kwargs):
            raise URLError("injected tunnel failure")

    kept = list(review.repair_probes)
    paused = review.assess(model=Down(), tools=None, context=context, agent_budget=None,
                           event_sink=None, cancellation_token=None, deadline_monotonic=None,
                           checks_receipt=checks)
    assert paused["status"] == "inconclusive"
    assert paused["reason"] == "review_transport_retry"
    assert paused["canContinue"] is True
    assert paused["repairProbes"] == kept
    assert paused["reason"] != "failure_adjudication_inconclusive"
