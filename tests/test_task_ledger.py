from __future__ import annotations

from minicode.agent_loop import run_agent_turn
from minicode.context_compactor import (
    AutoCompactConfig,
    AutoCompactDispatcher,
    ReactiveCompactEngine,
)
from minicode.task_ledger import TaskLedger
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep
from minicode.verification_observation import project_verification


def _history() -> list[dict[str, object]]:
    return [
        {"role": "system", "content": "base system"},
        {
            "role": "user",
            "content": "Fix the cache race. Do not change the public API. 必须运行测试。",
        },
        *[
            {
                "role": "assistant",
                "content": f"history-{index} " + "filler " * 400,
            }
            for index in range(60)
        ],
    ]


def test_task_ledger_records_only_bounded_parent_observations() -> None:
    ledger = TaskLedger.from_messages(_history())
    assert ledger is not None
    ledger.record_verification(
        {
            "verificationVersion": 1,
            "kind": "tests",
            "outcome": "passed",
            "source": "run_command_exit",
        }
    )
    ledger.record_failed_attempt("edit_file", "error[conflict]: stale revision")
    ledger.record_failed_attempt("edit_file", "untyped prose with /private/secret")

    message = ledger.to_message()
    content = message["content"]
    assert message["_task_ledger"] is True
    assert "Fix the cache race" in content
    assert "Do not change the public API" in content
    assert "必须运行测试" in content
    assert "tests passed via run_command_exit" in content
    assert "edit_file: error[conflict]" in content
    assert "/private/secret" not in content


def test_task_ledger_reconcile_replaces_stale_projection() -> None:
    ledger = TaskLedger.from_messages(_history())
    assert ledger is not None
    first = ledger.reconcile(_history())
    ledger.record_verification(
        {
            "verificationVersion": 1,
            "kind": "lint",
            "outcome": "passed",
            "source": "run_command_exit",
        }
    )
    second = ledger.reconcile(first)

    projected = [message for message in second if message.get("_task_ledger")]
    assert len(projected) == 1
    assert "lint passed via run_command_exit" in projected[0]["content"]


def test_task_ledger_survives_repeated_full_and_reactive_compaction() -> None:
    ledger = TaskLedger.from_messages(_history())
    assert ledger is not None
    ledger.record_verification(
        {
            "verificationVersion": 1,
            "kind": "tests",
            "outcome": "passed",
            "source": "test_runner",
        }
    )
    ledger.record_failed_attempt("run_command", "error[timeout]: bounded")
    messages = ledger.reconcile(_history())
    dispatcher = AutoCompactDispatcher(
        context_window=100_000,
        config=AutoCompactConfig(min_keep_tokens=0, min_keep_messages=5),
    )

    first = dispatcher.dispatch(messages, force_full=True)
    first.messages.extend(
        {
            "role": "assistant" if index % 2 else "user",
            "content": f"second-round-{index} " + "more " * 400,
        }
        for index in range(50)
    )
    second = dispatcher.dispatch(first.messages, force_full=True)
    reactive = ReactiveCompactEngine(dispatcher).try_recover_from_overflow(
        second.messages,
        "prompt too long",
    )
    assert reactive is not None

    for result in (first, second, reactive):
        ledgers = [
            message for message in result.messages if message.get("_task_ledger")
        ]
        assert len(ledgers) == 1
        assert "Fix the cache race" in ledgers[0]["content"]
        assert "tests passed via test_runner" in ledgers[0]["content"]
        assert "run_command: error[timeout]" in ledgers[0]["content"]


def test_agent_loop_refreshes_ledger_after_typed_verification() -> None:
    class CapturingModel:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, object]]] = []

        def next(self, messages, on_stream_chunk=None):
            del on_stream_chunk
            self.calls.append([dict(message) for message in messages])
            if len(self.calls) == 1:
                return AgentStep(
                    type="tool_calls",
                    calls=[{"id": "verify-1", "toolName": "test_runner", "input": {}}],
                )
            return AgentStep(type="assistant", content="done")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="test_runner",
                description="run tests",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(
                    ok=True,
                    output="1 passed",
                    verification=project_verification(
                        kind="tests",
                        passed=True,
                        source="test_runner",
                    ),
                ),
            )
        ]
    )
    model = CapturingModel()

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Fix it. Do not change the API."},
        ],
        cwd=".",
        enable_work_chain=False,
    )

    first_ledger = next(
        message for message in model.calls[0] if message.get("_task_ledger")
    )
    second_ledger = next(
        message for message in model.calls[1] if message.get("_task_ledger")
    )
    assert "Do not change the API" in first_ledger["content"]
    assert "tests passed via test_runner" in second_ledger["content"]
    assert len([message for message in messages if message.get("_task_ledger")]) == 1


def test_reactive_compaction_retains_latest_completion_decision_after_rebinding(
    tmp_path, monkeypatch,
) -> None:
    """Two host decisions replace feedback; compaction must not revive the first."""
    from copy import deepcopy
    import subprocess
    import sys

    import pytest
    import minicode.agent_loop as loop
    import minicode.workflow_runner as runner
    from minicode.agent_budget import AgentTurnBudget
    from minicode.context_compactor import CompactStrategy, CompactTrigger, CompactionResult
    from minicode.context_manager import ContextManager
    from minicode.deterministic_verifier import DeterministicVerifier, VerificationCheck
    from minicode.implementation_completion_review import ImplementationCompletionReview
    from minicode.implementation_execution import ImplementationSpec
    from minicode.permissions import PermissionManager
    from minicode.tools.write_file import write_file_tool

    objective = "Implement both operations and preserve the public API."
    (tmp_path / "candidate.py").write_text("STATE = 0\n")
    (tmp_path / "test_candidate.py").write_text(
        "from candidate import STATE\ndef test_state(): assert STATE in (1, 2)\n")
    spec = ImplementationSpec(("candidate.py", "test_candidate.py"), (VerificationCheck(
        "candidate", "tests", ("python3", "-m", "pytest", "-q", "test_candidate.py"), 20),), objective)
    checks, decisions, requests, recovery_inputs = [], [], [], []
    recovered_lists = []
    manager = ContextManager(model="default")

    def command(argv, **kwargs):
        result = subprocess.run([sys.executable, *argv[1:]], check=kwargs.pop("check", False), **kwargs)
        checks.append(result.returncode)
        return result

    monkeypatch.setattr(runner, "_deterministic_verifier_for_context",
        lambda *_args, **_kwargs: DeterministicVerifier(command_runner=command))
    monkeypatch.setattr(PermissionManager, "ensure_command", lambda *_args, **_kwargs: None)

    def assess(_self, *, checks_receipt, **_kwargs):
        # The semantic boundary denies completion based on actual current source;
        # the host still executes and binds each deterministic check receipt.
        decisions.append((deepcopy(checks_receipt), (tmp_path / "candidate.py").read_text()))
        return {"status": "changes_required", "patchDigest": checks_receipt["patchDigest"],
            "findings": [f"Missing operation after candidate revision {len(decisions)}."]}

    monkeypatch.setattr(ImplementationCompletionReview, "assess", assess)
    class DisabledCybernetics:
        def __bool__(self):
            return False

        def set_intent(self, _intent):
            pass

    monkeypatch.setattr(loop, "ContextCyberneticsOrchestrator",
        lambda *_args, **_kwargs: DisabledCybernetics())
    monkeypatch.setattr(loop.ContextCompactor, "process_request",
        lambda _self, messages, **_kwargs: CompactionResult(success=False,
            strategy=CompactStrategy.FULL, trigger=CompactTrigger.AUTO, messages=messages))

    def recover(_self, messages, _error):
        recovery_inputs.append(messages)
        first_feedback = next(m for m in recovery_inputs[0] if m.get("_implementation_completion_feedback"))
        # The second compressor intentionally retains stale context. It is the
        # loop's retention boundary that owns which host decision remains active.
        compacted = [{"role": "system", "content": "compacted history"}, first_feedback]
        recovered_lists.append(compacted)
        return CompactionResult(success=True, strategy=CompactStrategy.REACTIVE,
            trigger=CompactTrigger.REACTIVE, messages=compacted, tokens_freed=100)

    monkeypatch.setattr(loop.ContextCompactor, "reactive_recover", recover)

    class Model:
        def next(self, messages, **_kwargs):
            requests.append(deepcopy(messages))
            index = len(requests)
            if index in (1, 4):
                state = 1 if index == 1 else 2
                return AgentStep(type="tool_calls", calls=[{"id": f"write-{state}",
                    "toolName": "write_file", "input": {"path": "candidate.py", "content": f"STATE = {state}\n"}}])
            if index in (2, 5):
                return AgentStep(type="assistant", kind="final", content="Complete.")
            if index in (3, 6):
                raise RuntimeError("prompt too long")
            raise KeyboardInterrupt("observe retained second decision")

    with pytest.raises(KeyboardInterrupt, match="observe retained second decision"):
        run_agent_turn(model=Model(), tools=ToolRegistry([write_file_tool]),
            messages=[{"role": "user", "content": objective}], cwd=str(tmp_path),
            context_manager=manager, implementation_spec=spec, implementation_task=True,
            enable_implementation_phases=False, max_steps=None, agent_budget=AgentTurnBudget())

    assert checks == [0, 0]
    assert [source for _receipt, source in decisions] == ["STATE = 1\n", "STATE = 2\n"]
    assert all(receipt["status"] == "passed" and receipt["checks"] and receipt["patchDigest"]
               for receipt, _source in decisions)
    assert decisions[0][0]["patchDigest"] != decisions[1][0]["patchDigest"]
    assert len(requests) == 7 and len(recovery_inputs) == 2
    for attempt, request in enumerate((requests[3], requests[6]), start=1):
        feedback = [m for m in request if m.get("_implementation_completion_feedback")]
        assert len(feedback) == 1
        assert f'"attempt": {attempt}' in feedback[0]["content"]
        assert objective in feedback[0]["content"]
        assert len([m for m in request if m.get("_task_ledger")]) == 1
        assert "public API" in next(m["content"] for m in request if m.get("_task_ledger"))
    assert recovery_inputs[0] is not recovery_inputs[1]
    assert manager.messages is not recovery_inputs[-1]
    assert all(manager.messages is not candidate for candidate in recovered_lists)
    assert manager.messages[:-1] == requests[-1]
    assert manager.messages[-1].get("_host_implementation_receipt") is True
    assert manager.messages[-1].get("_partial") is True
    assert '"requirementsAccepted":false' in manager.messages[-1]["content"]
