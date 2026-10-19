"""Interrupted inventory repair retains its owner, files and actual receipts."""
from copy import deepcopy
import subprocess
import sys

import pytest

from minicode.agent_budget import AgentTurnBudget
from minicode.agent_loop import run_agent_turn
from minicode.context_manager import ContextManager
from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
from minicode.implementation_completion_review import ImplementationCompletionReview
from minicode.implementation_continuation import HostImplementationContinuation
from minicode.implementation_execution import ImplementationSpec
from minicode.permissions import PermissionManager
from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep


OBJECTIVE = "Implement hold by subtraction and release by addition. Test both behaviors."
PARTIAL = "def hold(n, k): return n-k\ndef release(n, k): return n\n"
COMPLETE = PARTIAL.replace("def release(n, k): return n", "def release(n, k): return n+k")
TESTS = "from stock import hold, release\ndef test_hold(): assert hold(7, 3) == 4\n"


def specification():
    return ImplementationSpec(("stock.py", "test_stock.py"), (
        VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "test_stock.py"), 20),
    ), OBJECTIVE)


def write_step(identity, path, content):
    return AgentStep(type="tool_calls", calls=[{"id": identity, "toolName": "write_file",
        "input": {"path": path, "content": content}}])


def test_actual_interruption_restores_same_owner_and_repairs_before_final(tmp_path, monkeypatch):
    import minicode.workflow_runner as runner

    (tmp_path / "stock.py").write_text(PARTIAL)
    (tmp_path / "test_stock.py").write_text(TESTS)
    commands, checkpoints, requests, reviews = [], [], [], []

    def command(argv, **kwargs):
        result = subprocess.run([sys.executable, *argv[1:]], **kwargs)
        commands.append((str(kwargs["cwd"]), result.returncode))
        return result

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
        lambda *_a, **_k: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_a, **_k: None)

    def assess(self, *, context, checks_receipt, **kwargs):
        assert checks_receipt["status"] == "passed"
        complete = (tmp_path / "stock.py").read_text() == COMPLETE
        complete &= "test_release" in (tmp_path / "test_stock.py").read_text()
        reviews.append(complete)
        return {"status": "approved" if complete else "changes_required",
            "patchDigest": checks_receipt["patchDigest"],
            "findings": [] if complete else ["release and its regression are missing."]}

    monkeypatch.setattr(ImplementationCompletionReview, "assess", assess)

    class InterruptedModel:
        def next(self, messages, **kwargs):
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                return write_step("original-edit", "stock.py", PARTIAL + "# existing modification\n")
            if len(requests) == 2:
                return AgentStep(type="assistant", kind="final", content="All work done.")
            assert any(m.get("_implementation_completion_feedback") for m in messages)
            raise KeyboardInterrupt("operator stopped invalid gate")

    manager = ContextManager(model="default")
    spec = specification()
    options = dict(cwd=str(tmp_path), implementation_spec=spec, implementation_task=True,
        enable_work_chain=False, max_steps=None, agent_budget=AgentTurnBudget(),
        implementation_checkpoint_sink=lambda document, messages: checkpoints.append((document, messages)))
    with pytest.raises(KeyboardInterrupt):
        run_agent_turn(model=InterruptedModel(), tools=ToolRegistry([write_file_tool]),
            messages=[{"role": "user", "content": OBJECTIVE}], context_manager=manager, **options)
    assert len(checkpoints) == 1 and reviews == [False]
    document, saved_messages = checkpoints[0]
    assert document["attempt"] == 1
    assert document["completionFeedback"] in saved_messages
    assert saved_messages == manager.messages
    owner = document["executorId"]
    restore = HostImplementationContinuation.from_document(document, spec=spec, messages=saved_messages)
    resumed_requests = []

    class ResumedModel:
        def next(self, messages, **kwargs):
            resumed_requests.append(deepcopy(messages))
            if len(resumed_requests) == 1:
                assert "existing modification" in (tmp_path / "stock.py").read_text()
                assert any(m.get("role") == "tool_result" and m.get("toolUseId") == "original-edit"
                           for m in messages)
                assert sum(bool(m.get("_implementation_context")) for m in messages) == 1
                feedback = next(m for m in messages if m.get("_implementation_completion_feedback"))
                assert owner in feedback["content"] and OBJECTIVE in feedback["content"]
                return write_step("repair", "stock.py", COMPLETE)
            if len(resumed_requests) == 2:
                return write_step("regression", "test_stock.py", TESTS +
                    "def test_release(): assert release(7, 3) == 10\n")
            return AgentStep(type="assistant", kind="final", content="Both operations tested and delivered.")

    result = run_agent_turn(model=ResumedModel(), tools=ToolRegistry([write_file_tool]),
        messages=saved_messages, implementation_continuation=restore, **options)
    assert reviews == [False, True]
    assert len(resumed_requests) == 3
    assert checkpoints[-1][0]["executorId"] == owner
    assert checkpoints[-1][0]["attempt"] == 2
    assert checkpoints[-1][0]["candidateDigest"] != document["candidateDigest"]
    assert commands and all(cwd != str(tmp_path) and code == 0 for cwd, code in commands)
    assert result[-1]["content"] == "Both operations tested and delivered."


@pytest.mark.parametrize("tamper", ["candidate", "objective", "messages", "finite_budget", "untyped", "review"])
def test_invalid_restore_rejected_before_any_model_request(tmp_path, tamper):
    (tmp_path / "stock.py").write_text(PARTIAL)
    spec, messages = specification(), [{"role": "user", "content": OBJECTIVE}]
    continuation = HostImplementationContinuation.capture(spec=spec,
        context=ToolContext(cwd=str(tmp_path)), messages=messages,
        executor_id="implementation_" + "a" * 32, attempt=2)
    budget = AgentTurnBudget()
    if tamper == "candidate":
        (tmp_path / "stock.py").write_text(COMPLETE)
    elif tamper == "objective":
        spec = ImplementationSpec(spec.write_paths, spec.checks, "A different requirement")
    elif tamper == "messages":
        messages.append({"role": "user", "content": "Drop the missing requirement"})
    elif tamper == "finite_budget":
        budget = AgentTurnBudget(max_model_calls=3)
    elif tamper == "review":
        document = continuation.document
        document["reviewState"] = {"objective": OBJECTIVE, "probes": [{"probeId": "invented"}]}
        continuation = HostImplementationContinuation.from_document(document, spec=spec, messages=messages)
    else:
        continuation = continuation.document

    class NeverCalled:
        def next(self, *args, **kwargs):
            pytest.fail("invalid restored state must fail before any model call")

    with pytest.raises(ValueError):
        run_agent_turn(model=NeverCalled(), tools=ToolRegistry([write_file_tool]),
            cwd=str(tmp_path), messages=messages, implementation_spec=spec,
            implementation_continuation=continuation, agent_budget=budget)
