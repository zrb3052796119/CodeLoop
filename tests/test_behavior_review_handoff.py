"""Scripted parent-loop demo: one reviewer, the same Executor, two repair rounds.

The inventory example is not a hidden oracle. Public checks cover hold. The
reviewer writes the release test the executor did not write. A transport
failure during review retries and still finishes.
"""

import json
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

    class Sandbox:
        def wrap_command(self, *, argv, cwd, **kwargs):
            return argv

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
                        lambda *_args, **_kwargs: DeterministicVerifier(
                            sandbox_backend=Sandbox(),
                            explicit_trusted_executables={"python3": sys.executable}))
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


def _run(tmp_path, monkeypatch, *, transport_failure=False, max_model_calls=12,
         model=None, tools=None, on_assistant_stream_chunk=None, initial_public=None):
    _install_verifier(monkeypatch)
    (tmp_path / "stock.py").write_text(INITIAL)
    (tmp_path / "test_public.py").write_text(
        initial_public or "def test_ready():\n    assert True\n")
    script = model or _Script(tmp_path, transport_failure=transport_failure)
    events = []

    class Sink:
        def emit(self, event_type, *, step=None, payload=None):
            if event_type == "implementation.completion_decided":
                events.append(payload)

    capture = AgentOutcomeCapture()
    messages = run_agent_turn(
        model=script, tools=tools or ToolRegistry([edit_file_tool, write_file_tool]),
        messages=[{"role": "user", "content": OBJECTIVE}], cwd=str(tmp_path),
        implementation_task=True, implementation_spec=_spec(), enable_work_chain=False,
        max_steps=None, agent_budget=AgentTurnBudget(max_model_calls=max_model_calls),
        outcome_capture=capture, event_sink=Sink(), defer_curator_maintenance=True,
        enable_implementation_phases=False,
        on_assistant_stream_chunk=on_assistant_stream_chunk)
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


def _direct(tmp_path, monkeypatch, model, *, product=BOTH, agent_budget=None):
    _install_verifier(monkeypatch)
    (tmp_path / "stock.py").write_text(product)
    (tmp_path / "test_public.py").write_text(PUBLIC)
    review = BehaviorReview(_spec())
    context = ToolContext(cwd=str(tmp_path), permissions=PermissionManager(str(tmp_path), prompt=None))
    from minicode.implementation_execution import direct_verification_capability
    checks = direct_verification_capability(_spec()).ensure_completion_checks(context)
    assert checks["status"] == "passed"

    def assess(**kwargs):
        return review.assess(model=model, tools=None, context=context,
                             agent_budget=kwargs.pop("agent_budget", agent_budget),
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


def test_collection_or_import_error_is_a_host_retry_not_a_behavior_gap(tmp_path, monkeypatch):
    def unavailable(**_kwargs):
        return {
            "status": "inconclusive",
            "reason": "review_probe_collection_or_runtime_unavailable",
            "evidence": {"exitCode": 1, "stderr": {
                "preview": 'ImportError: Error importing plugin "_minicode_pytest_x": No module named _minicode_pytest_x',
            }},
        }

    monkeypatch.setattr("minicode.behavior_review.run_isolated_review_probe", unavailable)

    class Model:
        calls = 0

        def next(self, _messages, **_kwargs):
            self.calls += 1
            return AgentStep(type="assistant", kind="final",
                             content=_verdict("fail", ["release does not add the requested amount."], RELEASE_PROBE))

    review, assess = _direct(tmp_path, monkeypatch, Model())
    first = assess()
    assert first["status"] == "inconclusive"
    assert first["reason"] == "review_host_retry"
    assert first["canContinue"] is True
    assert first["repairProbes"] == []
    assert review.repair_rounds == 0
    second = assess()
    assert second["status"] == "inconclusive" and second["reason"] == "review_host_retry"
    assert review.repair_rounds == 0


def test_assertion_failure_is_still_a_behavior_gap(tmp_path, monkeypatch):
    class Model:
        def next(self, _messages, **_kwargs):
            return AgentStep(type="assistant", kind="final",
                             content=_verdict("fail", ["release does not add the requested amount."], RELEASE_PROBE))

    review, assess = _direct(tmp_path, monkeypatch, Model(), product=HOLD)
    result = assess()
    assert result["status"] == "changes_required"
    assert result["reason"] == "behavior_gap"
    assert result["canContinue"] is True
    assert result["repairProbes"][0]["code"] == RELEASE_PROBE
    assert review.repair_rounds == 1


def test_reviewer_calls_count_and_an_exhausted_budget_hands_to_a_human(tmp_path, monkeypatch):
    budget = AgentTurnBudget(max_model_calls=1)

    class Model:
        calls = 0

        def next(self, _messages, **_kwargs):
            self.calls += 1
            return AgentStep(type="assistant", kind="final", content=_verdict("pass", [], RELEASE_PROBE))

    model = Model()
    review, assess = _direct(tmp_path, monkeypatch, model, agent_budget=budget)
    admitted = assess()
    assert admitted["status"] == "approved"
    assert model.calls == 1
    assert budget.snapshot().used_model_calls == 1
    stopped = assess()
    assert stopped["status"] == "needs_human"
    assert stopped["reason"] == "review_call_budget"
    assert stopped["canContinue"] is False
    assert model.calls == 1
    assert review.repair_rounds == 0
    assert budget.snapshot().used_model_calls == 1

    messages, events, _capture, script = _run(tmp_path, monkeypatch, max_model_calls=2)
    assert script.reviews == []
    assert events[-1]["status"] == "needs_human"
    assert events[-1]["reason"] == "review_call_budget"
    assert events[-1]["canContinue"] is False
    assert "review call budget is exhausted" in json.dumps(messages)


def test_reviewer_usage_is_charged_to_the_turn_budget(tmp_path, monkeypatch):
    from decimal import Decimal

    from minicode.cost_tracker import calculate_cost
    from minicode.types import ModelUsage

    usage = ModelUsage(input_tokens=1200, output_tokens=340, source="provider")

    class Model:
        def next(self, _messages, **_kwargs):
            return AgentStep(type="assistant", kind="final",
                             content=_verdict("pass", [], RELEASE_PROBE), usage=usage)

    budget = AgentTurnBudget(max_model_calls=2)
    _review, assess = _direct(tmp_path, monkeypatch, Model(), agent_budget=budget)
    result = assess()
    snapshot = budget.snapshot()
    assert result["status"] == "approved"
    assert snapshot.used_model_calls == 1
    assert snapshot.used_input_tokens == 1200
    assert snapshot.used_output_tokens == 340
    assert Decimal(snapshot.used_cost_usd) == Decimal(str(calculate_cost(
        "default", input_tokens=1200, output_tokens=340)))
    assert Decimal(snapshot.used_cost_usd) > 0


def test_implementation_turns_stream_without_publishing_unadmitted_text(tmp_path, monkeypatch):
    from minicode.agent_loop import _discard_unadmitted_implementation_stream

    ui = []

    class Watching(_Script):
        def __init__(self, root):
            super().__init__(root)
            self.executor_callbacks = []

        def next(self, messages, on_stream_chunk=None, **kwargs):
            reviewing = bool(messages and messages[0].get("role") == "system"
                             and "BEHAVIOR_REVIEW" in str(messages[0].get("content", "")))
            if not reviewing:
                self.executor_callbacks.append(on_stream_chunk)
                if on_stream_chunk is not None:
                    on_stream_chunk("unadmitted draft")
            return super().next(messages, on_stream_chunk=on_stream_chunk, **kwargs)

    script = Watching(tmp_path)
    _messages, events, _capture, script = _run(
        tmp_path, monkeypatch, model=script, on_assistant_stream_chunk=ui.append)
    assert events[-1]["status"] == "approved"
    assert script.executor_callbacks
    assert all(callback is _discard_unadmitted_implementation_stream
               for callback in script.executor_callbacks)
    assert "unadmitted draft" not in ui


def test_repaired_mid_turn_verification_failure_still_achieves_the_goal(tmp_path, monkeypatch):
    from minicode.tools.run_command import run_command_tool

    class MissThenFix(_Script):
        def __init__(self, root):
            super().__init__(root)
            self.ran_miss = False

        def next(self, messages, **kwargs):
            reviewing = bool(messages and messages[0].get("role") == "system"
                             and "BEHAVIOR_REVIEW" in str(messages[0].get("content", "")))
            if not reviewing and not self.ran_miss:
                self.ran_miss = True
                return AgentStep(type="tool_calls", calls=[
                    _tool("run-miss", "run_command", command="python3",
                          args=["-m", "pytest", "-q", "test_public.py"]),
                ])
            return super().next(messages, **kwargs)

    script = MissThenFix(tmp_path)
    _messages, events, capture, script = _run(
        tmp_path, monkeypatch, model=script,
        tools=ToolRegistry([edit_file_tool, write_file_tool, run_command_tool]),
        initial_public="def test_miss():\n    assert False\n",
    )
    assert script.ran_miss
    assert events[-1]["status"] == "approved"
    assert events[-1]["reason"] == "behavior_review_passed"
    assert capture.outcome.status == "success"
    assert capture.outcome.verification_failed_count >= 1
    assert capture.outcome.verification_passed_count >= 1
    assert capture.outcome.verification_status == "verified"
    assert capture.outcome.goal_achieved is True
