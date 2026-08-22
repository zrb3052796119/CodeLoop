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
