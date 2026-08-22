from __future__ import annotations

from minicode.agent_loop import run_agent_turn
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.types import AgentStep, ChatMessage, ModelAdapter


class ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = steps
        self.calls = 0

    def next(
        self,
        messages: list[ChatMessage],
        on_stream_chunk=None,
    ) -> AgentStep:
        del messages, on_stream_chunk
        step = self._steps[self.calls]
        self.calls += 1
        return step


def _tool(name: str, runner) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="recovery regression fixture",
        input_schema={"type": "object"},
        validator=lambda value: value,
        run=runner,
    )


def test_equivalent_denied_shell_wrapper_is_suppressed_but_direct_recovery_runs() -> None:
    executed: list[str] = []

    def policy_command(input_data: dict, _context) -> ToolResult:
        command = input_data["command"]
        executed.append(command)
        if command in {"bash", "zsh"}:
            return ToolResult(
                ok=False,
                output="error[permission_denied]: shell wrapper denied",
            )
        return ToolResult(ok=True, output="direct command succeeded")

    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "first-wrapper",
                        "toolName": "run_command",
                        "input": {
                            "command": "zsh",
                            "args": ["-lc", "ruff check src"],
                        },
                    },
                    {
                        "id": "same-batch-wrapper",
                        "toolName": "run_command",
                        "input": {
                            "command": "bash",
                            "args": ["-lc", "ruff check src"],
                        },
                    },
                ],
            ),
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "direct",
                        "toolName": "run_command",
                        "input": {"command": "ruff", "args": ["check", "src"]},
                    }
                ],
            ),
            AgentStep(type="assistant", content="Recovered."),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([_tool("run_command", policy_command)]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        enable_work_chain=False,
    )

    assert executed == ["zsh", "ruff"]
    assert any(
        message.get("role") == "tool_result"
        and "repeated_blocked_action" in message.get("content", "")
        for message in messages
    )
    assert messages[-1] == {"role": "assistant", "content": "Recovered."}


def test_repeated_denied_action_opens_circuit_before_turn_step_limit() -> None:
    executions: list[str] = []

    def denied(input_data: dict, _context) -> ToolResult:
        executions.append(input_data["command"])
        return ToolResult(
            ok=False,
            output="error[permission_denied]: command denied",
        )

    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"denied-{index}",
                        "toolName": "run_command",
                        "input": {
                            "command": "bash",
                            "args": ["-lc", "python -m unittest tests.test_runtime"],
                        },
                    }
                ],
            )
            for index in range(10)
        ]
    )
    events: list[tuple[str, dict]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            del step
            events.append((event_type, dict(payload or {})))

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([_tool("run_command", denied)]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        enable_work_chain=False,
        max_steps=10,
        event_sink=RecordingSink(),
    )

    assert model.calls == 3
    assert executions == ["bash"]
    assert "recovery_exhausted" in messages[-1]["content"]
    event_types = [event_type for event_type, _payload in events]
    assert event_types.index("execution.stopped") < event_types.index("task.outcome")
    stopped = next(
        payload for event_type, payload in events if event_type == "execution.stopped"
    )
    assert stopped == {
        "reasonCode": "repeated_denied_action",
        "stepCount": 3,
        "toolErrorCount": 1,
        "consecutiveFailedSteps": 1,
        "userActionRequired": True,
    }


def test_five_consecutive_failed_tool_steps_switch_strategy_then_stop() -> None:
    attempts: list[int] = []

    def always_fail(input_data: dict, _context) -> ToolResult:
        attempts.append(input_data["attempt"])
        return ToolResult(
            ok=False,
            output="error[invalid_invocation]: fixture failure",
        )

    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"failure-{attempt}",
                        "toolName": "fragile_tool",
                        "input": {"attempt": attempt},
                    }
                ],
            )
            for attempt in range(1, 11)
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([_tool("fragile_tool", always_fail)]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        enable_work_chain=False,
        max_steps=10,
    )

    assert model.calls == 5
    assert attempts == [1, 2, 3, 4, 5]
    assert any(
        "strategy_switch_required" in message.get("content", "")
        for message in messages
        if message.get("role") == "tool_result"
    )
    assert "recovery_exhausted" in messages[-1]["content"]
