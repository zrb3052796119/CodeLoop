import pytest

from minicode.agent_budget import AgentBudgetExceeded, AgentTurnBudget
from minicode.agent_loop import _preemptive_step_cap, run_agent_turn
from minicode.state import create_app_store
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import (
    ToolCapability,
    ToolDefinition,
    ToolExecutionAbandoned,
    ToolMetadata,
    ToolRegistry,
    ToolResult,
)
from minicode.tools.ask_user import ask_user_tool
from minicode.types import AgentStep, ChatMessage, ModelAdapter, ModelUsage, StepDiagnostics


class ScriptedModel(ModelAdapter):
    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = steps
        self.calls = 0

    def next(self, messages: list[ChatMessage], on_stream_chunk=None) -> AgentStep:
        step = self._steps[self.calls]
        self.calls += 1
        return step


class StoreCapturingModel(ModelAdapter):
    def __init__(self) -> None:
        self.received_store = None

    def next(self, messages: list[ChatMessage], on_stream_chunk=None, store=None) -> AgentStep:
        self.received_store = store
        return AgentStep(type="assistant", content="done")


def test_required_skill_prevents_feedforward_step_starvation() -> None:
    assert _preemptive_step_cap(
        max_steps=50,
        recommended_steps=10,
        required_skill_count=1,
    ) == 15
    assert _preemptive_step_cap(
        max_steps=8,
        recommended_steps=10,
        required_skill_count=1,
    ) == 8
    assert _preemptive_step_cap(
        max_steps=50,
        recommended_steps=10,
        required_skill_count=0,
    ) == 10


def test_agent_turn_executes_tool_and_returns_assistant() -> None:
    def run_echo(input_data: dict, _context) -> ToolResult:
        return ToolResult(ok=True, output=f"echo:{input_data['text']}")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="echo tool",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_echo,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "echo", "input": {"text": "hi"}}],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert any(message["role"] == "tool_result" for message in messages)


def test_equivalent_denied_shell_wrapper_is_suppressed_but_direct_recovery_runs(
    tmp_path,
) -> None:
    """A denied wrapper may be replaced by a direct allowed invocation.

    The second wrapper is behaviorally equivalent to the first denial and must
    not reach the tool again.  The direct Python invocation is materially
    different and remains eligible to recover the task.
    """
    from minicode.permissions import PermissionManager
    from minicode.tools.run_command import run_command_tool

    workspace = tmp_path / "workspace"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "tests" / "test_parser_runtime.py").write_text(
        """import unittest


class ParserRuntimeTests(unittest.TestCase):
    def test_contract(self):
        print("VERIFY-PARSER-174")


if __name__ == "__main__":
    unittest.main()
""",
        encoding="utf-8",
    )
    permissions = PermissionManager(
        str(workspace),
        prompt=lambda request: {
            "decision": (
                "allow_once"
                if request.get("review", {}).get("command") == "python"
                else "deny_once"
            )
        },
    )
    executions: list[dict] = []
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "wrapped-zsh",
                        "toolName": "run_command",
                        "input": {
                            "command": "/bin/zsh",
                            "args": [
                                "-lc",
                                "bash -lc \"cd . && python -m unittest -v "
                                "tests.test_parser_runtime.ParserRuntimeTests\"",
                            ],
                        },
                    }
                ],
            ),
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "wrapped-bash",
                        "toolName": "run_command",
                        "input": {
                            "command": "bash",
                            "args": [
                                "-lc",
                                "python -m unittest "
                                "tests.test_parser_runtime.ParserRuntimeTests -v",
                            ],
                        },
                    }
                ],
            ),
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "direct-python",
                        "toolName": "run_command",
                        "input": {
                            "command": "python",
                            "args": [
                                "-m",
                                "unittest",
                                "-v",
                                "tests.test_parser_runtime.ParserRuntimeTests",
                            ],
                        },
                    }
                ],
            ),
            AgentStep(type="assistant", content="Verified: VERIFY-PARSER-174"),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([run_command_tool]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(workspace),
        permissions=permissions,
        enable_work_chain=False,
        on_tool_start=lambda _name, input_data: executions.append(input_data),
    )

    assert [item["command"] for item in executions] == ["/bin/zsh", "python"]
    assert any(
        message.get("role") == "tool_result"
        and "equivalent denied action" in message.get("content", "").lower()
        for message in messages
    )
    assert messages[-1] == {
        "role": "assistant",
        "content": "Verified: VERIFY-PARSER-174",
    }


def test_equivalent_denied_calls_in_one_serial_batch_are_suppressed() -> None:
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

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="run_command",
                description="policy command fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=policy_command,
            )
        ]
    )
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
        tools=registry,
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


def test_repeated_denied_action_opens_recovery_circuit_before_step_limit(
    tmp_path,
) -> None:
    """An obstinate model cannot spend the whole Turn on one denied action."""
    from minicode.permissions import PermissionManager
    from minicode.tools.run_command import run_command_tool

    permissions = PermissionManager(
        str(tmp_path),
        prompt=lambda _request: {"decision": "deny_once"},
    )
    denied_call = {
        "toolName": "run_command",
        "input": {
            "command": "bash",
            "args": ["-lc", "python -m unittest tests.test_runtime -v"],
        },
    }
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": f"denied-{index}", **denied_call}],
            )
            for index in range(10)
        ]
    )
    executions: list[dict] = []
    outcome_capture = AgentOutcomeCapture()
    events: list[tuple[str, int | None, dict]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            events.append((event_type, step, dict(payload or {})))

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([run_command_tool]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        permissions=permissions,
        enable_work_chain=False,
        max_steps=10,
        outcome_capture=outcome_capture,
        event_sink=RecordingSink(),
        on_tool_start=lambda _name, input_data: executions.append(input_data),
    )

    assert model.calls <= 3
    assert len(executions) == 1
    assert messages[-1]["role"] == "assistant"
    assert "recovery circuit opened" in messages[-1]["content"].lower()
    assert outcome_capture.outcome is not None
    assert outcome_capture.outcome.status == "failed"
    event_types = [event_type for event_type, _step, _payload in events]
    assert event_types.index("execution.stopped") < event_types.index("task.outcome")
    stopped = next(
        payload
        for event_type, _step, payload in events
        if event_type == "execution.stopped"
    )
    assert stopped == {
        "reasonCode": "repeated_denied_action",
        "stepCount": 3,
        "toolErrorCount": 1,
        "consecutiveFailedSteps": 1,
        "userActionRequired": True,
    }


def test_consecutive_tool_failures_switch_strategy_then_stop_after_five_steps() -> None:
    attempts: list[int] = []

    def always_fail(input_data: dict, _context) -> ToolResult:
        attempts.append(input_data["attempt"])
        return ToolResult(
            ok=False,
            output=f"error[invalid_invocation]: attempt {input_data['attempt']} failed",
        )

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="fragile_tool",
                description="always fails for this regression",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=always_fail,
            )
        ]
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
    outcome_capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        enable_work_chain=False,
        max_steps=10,
        outcome_capture=outcome_capture,
    )

    assert model.calls == 5
    assert attempts == [1, 2, 3, 4, 5]
    assert any(
        "strategy_switch_required" in message.get("content", "")
        for message in messages
        if message.get("role") == "tool_result"
    )
    assert "recovery circuit opened" in messages[-1]["content"].lower()
    assert outcome_capture.outcome is not None
    assert outcome_capture.outcome.status == "failed"


def test_success_after_strategy_switch_resets_recovery_failure_budget() -> None:
    attempts: list[int] = []

    def recover_on_fourth(input_data: dict, _context) -> ToolResult:
        attempt = input_data["attempt"]
        attempts.append(attempt)
        if attempt < 4:
            return ToolResult(
                ok=False,
                output=f"error[invalid_invocation]: attempt {attempt} failed",
            )
        return ToolResult(ok=True, output="verified recovery")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="recoverable_tool",
                description="succeeds after a strategy switch",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=recover_on_fourth,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"attempt-{attempt}",
                        "toolName": "recoverable_tool",
                        "input": {"attempt": attempt},
                    }
                ],
            )
            for attempt in range(1, 5)
        ]
        + [AgentStep(type="assistant", content="Recovered and verified.")]
    )
    outcome_capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        enable_work_chain=False,
        max_steps=10,
        outcome_capture=outcome_capture,
    )

    assert attempts == [1, 2, 3, 4]
    assert any(
        "strategy_switch_required" in message.get("content", "")
        for message in messages
        if message.get("role") == "tool_result"
    )
    assert messages[-1] == {
        "role": "assistant",
        "content": "Recovered and verified.",
    }
    assert outcome_capture.outcome is not None
    assert outcome_capture.outcome.status == "success"


def test_eight_failed_steps_in_ten_step_window_stops_interleaved_loop() -> None:
    attempts: list[int] = []

    def intermittently_succeeds(input_data: dict, _context) -> ToolResult:
        attempt = input_data["attempt"]
        attempts.append(attempt)
        if attempt in {4, 8}:
            return ToolResult(ok=True, output=f"observation {attempt}")
        return ToolResult(
            ok=False,
            output=f"error[invalid_invocation]: attempt {attempt} failed",
        )

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="intermittent_tool",
                description="two non-terminal successes",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=intermittently_succeeds,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"window-{attempt}",
                        "toolName": "intermittent_tool",
                        "input": {"attempt": attempt},
                    }
                ],
            )
            for attempt in range(1, 13)
        ]
    )
    events: list[tuple[str, dict]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            del step
            events.append((event_type, dict(payload or {})))

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        enable_work_chain=False,
        max_steps=12,
        event_sink=RecordingSink(),
    )

    assert model.calls == 10
    assert attempts == list(range(1, 11))
    stopped = next(
        payload for event_type, payload in events if event_type == "execution.stopped"
    )
    assert stopped["reasonCode"] == "failure_window_exhausted"
    assert stopped["consecutiveFailedSteps"] == 2
    assert "recovery_exhausted" in messages[-1]["content"]


@pytest.mark.parametrize(
    "unfinished",
    [
        "The file moved to backend/auth.py. Let me read it.",
        "文件位于 backend/auth.py。我现在读取它。",
    ],
)
def test_agent_turn_does_not_accept_a_next_action_as_the_final_answer(
    unfinished: str,
) -> None:
    def run_echo(input_data: dict, _context) -> ToolResult:
        return ToolResult(ok=True, output=f"echo:{input_data['text']}")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="echo tool",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_echo,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "locate", "toolName": "echo", "input": {"text": "path"}}],
            ),
            AgentStep(type="assistant", content=unfinished),
            AgentStep(
                type="tool_calls",
                calls=[{"id": "read", "toolName": "echo", "input": {"text": "marker"}}],
            ),
            AgentStep(type="assistant", content="The exact marker is AUTH-73."),
        ]
    )
    progress: list[str] = []

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        on_progress_message=progress.append,
    )

    assert model.calls == 4
    assert messages[-1] == {
        "role": "assistant",
        "content": "The exact marker is AUTH-73.",
    }
    assert unfinished in progress
    assert any(
        message.get("role") == "assistant_progress"
        and message.get("content") == unfinished
        for message in messages
    )


def test_routed_skill_must_be_loaded_before_final_is_accepted(tmp_path) -> None:
    from minicode.tools.load_skill import create_load_skill_tool

    skill_file = tmp_path / ".mini-code" / "skills" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Demo\n\nFollow demo instructions.\n", encoding="utf-8")
    model = ScriptedModel(
        [
            AgentStep(type="assistant", content="premature final"),
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "skill-1", "toolName": "load_skill", "input": {"name": "demo"}}
                ],
            ),
            AgentStep(type="assistant", content="done after loading"),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([create_load_skill_tool(str(tmp_path))]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=str(tmp_path),
        required_skill_names=["demo"],
    )

    assert model.calls == 3
    assert messages[-1] == {"role": "assistant", "content": "done after loading"}
    assert not any(
        message.get("content") == "premature final" for message in messages
    )
    assert any(
        message.get("toolName") == "load_skill"
        and message.get("role") == "tool_result"
        for message in messages
    )


def test_routed_skill_refusal_is_not_reported_as_success() -> None:
    capture = AgentOutcomeCapture()
    messages = run_agent_turn(
        model=ScriptedModel(
            [
                AgentStep(type="assistant", content="first final"),
                AgentStep(type="assistant", content="second final"),
            ]
        ),
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        required_skill_names=["required-skill"],
        outcome_capture=capture,
    )

    assert capture.outcome is not None
    assert capture.outcome.status == "failed"
    assert "required routed Skills were not loaded" in messages[-1]["content"]


@pytest.mark.parametrize(
    ("misused_question", "final_answer"),
    [
        (
            "The requested release goal codename is GOAL-NOVA-31.",
            "GOAL-NOVA-31",
        ),
        (
            "请求的发布目标代号是 GOAL-NOVA-31。",
            "GOAL-NOVA-31",
        ),
    ],
)
def test_noninteractive_turn_rejects_ask_user_and_continues_to_final(
    misused_question: str,
    final_answer: str,
) -> None:
    """Headless execution must not turn an `ask_user` payload into output.

    The contract is capability-based rather than language/content-based: any
    request to wait for a user is unavailable when no user can answer.
    """
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "ask-1",
                        "toolName": "ask_user",
                        "input": {"question": misused_question},
                    }
                ],
            ),
            AgentStep(type="assistant", content=final_answer),
        ]
    )
    capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([ask_user_tool]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        allow_user_interaction=False,
        outcome_capture=capture,
    )

    assert model.calls == 2
    assert messages[-1] == {"role": "assistant", "content": final_answer}
    tool_result = next(
        message for message in messages if message.get("role") == "tool_result"
    )
    assert tool_result["isError"] is True
    assert "unavailable" in tool_result["content"].lower()
    assert capture.outcome is not None
    assert capture.outcome.status == "success"
    assert capture.outcome.errors_recovered is True


def test_interactive_turn_preserves_legitimate_ask_user_pause() -> None:
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "ask-1",
                        "toolName": "ask_user",
                        "input": {
                            "question": "Which deployment region should I use?"
                        },
                    }
                ],
            )
        ]
    )
    capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([ask_user_tool]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        allow_user_interaction=True,
        outcome_capture=capture,
    )

    assert model.calls == 1
    assert messages[-1] == {
        "role": "assistant",
        "content": "Which deployment region should I use?",
    }
    assert capture.outcome is not None
    assert capture.outcome.status == "unknown"
    assert capture.outcome.completion_succeeded is False


def test_tool_timeout_returns_error_without_blocking(monkeypatch) -> None:
    """A hung tool must yield a timeout error promptly instead of blocking
    until the tool finishes, and must not be executed a second time."""
    import threading
    import time

    monkeypatch.setenv("MINICODE_TOOL_TIMEOUT", "1")
    release = threading.Event()
    executions: list[int] = []

    def run_hang(_input_data: dict, _context) -> ToolResult:
        executions.append(1)
        release.wait(timeout=30)
        return ToolResult(ok=True, output="finally finished")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="hang",
                description="hangs",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_hang,
                metadata=ToolMetadata(
                    name="hang",
                    description="read-only hanging probe",
                    capabilities={ToolCapability.READ_ONLY},
                ),
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(type="tool_calls", calls=[{"id": "1", "toolName": "hang", "input": {}}]),
            AgentStep(type="assistant", content="done"),
        ]
    )

    started = time.monotonic()
    try:
        messages = run_agent_turn(
            model=model,
            tools=registry,
            messages=[{"role": "system", "content": "sys"}],
            cwd=".",
        )
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"turn blocked on hung tool for {elapsed:.1f}s"
    tool_results = [m for m in messages if m["role"] == "tool_result"]
    assert tool_results and "timed out" in tool_results[0]["content"]
    assert len(executions) == 1, "tool must not be re-executed after a failure"


def test_write_capable_tool_is_never_abandoned_as_a_background_worker(
    monkeypatch,
) -> None:
    import threading
    import time

    monkeypatch.setenv("MINICODE_TOOL_TIMEOUT", "1")
    entered = threading.Event()
    release = threading.Event()
    writes: list[str] = []
    outcome: dict[str, object] = {}

    def run_writer(_input_data: dict, _context) -> ToolResult:
        entered.set()
        assert release.wait(5)
        writes.append("committed")
        return ToolResult(ok=True, output="write complete")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="writer",
                description="write-capable probe",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_writer,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "writer", "input": {}}],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )

    def run_turn() -> None:
        outcome["messages"] = run_agent_turn(
            model=model,
            tools=registry,
            messages=[{"role": "system", "content": "sys"}],
            cwd=".",
        )

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert entered.wait(2)
    time.sleep(1.1)
    assert worker.is_alive(), "writer must remain owned instead of timing out"
    assert writes == []
    release.set()
    worker.join(3)

    assert not worker.is_alive()
    assert writes == ["committed"]
    assert outcome["messages"][-1]["content"] == "done"


def test_agent_turn_emits_callbacks() -> None:
    events: list[tuple[str, str]] = []

    def run_echo(input_data: dict, _context) -> ToolResult:
        return ToolResult(ok=True, output=f"echo:{input_data['text']}")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="echo tool",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_echo,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(type="tool_calls", content="working", contentKind="progress", calls=[{"id": "1", "toolName": "echo", "input": {"text": "hi"}}]),
            AgentStep(type="assistant", content="done"),
        ]
    )

    run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        on_tool_start=lambda name, _input: events.append(("start", name)),
        on_tool_result=lambda name, _output, _error: events.append(("result", name)),
        on_assistant_message=lambda content: events.append(("assistant", content)),
        on_progress_message=lambda content: events.append(("progress", content)),
    )

    assert ("progress", "working") in events
    assert ("start", "echo") in events
    assert ("result", "echo") in events
    assert ("assistant", "done") in events


def test_agent_turn_retries_empty_response_then_continues() -> None:
    model = ScriptedModel(
        [
            AgentStep(type="assistant", content=""),
            AgentStep(type="assistant", content="done"),
        ]
    )
    registry = ToolRegistry([])

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert any(
        message["role"] == "user" and "last response was empty" in message["content"]
        for message in messages
    )


def test_agent_turn_handles_recoverable_pause_turn() -> None:
    model = ScriptedModel(
        [
            AgentStep(
                type="assistant",
                content="",
                diagnostics=StepDiagnostics(stopReason="pause_turn", ignoredBlockTypes=["thinking"]),
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )
    registry = ToolRegistry([])
    progress_events: list[str] = []

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        on_progress_message=progress_events.append,
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert any("pause_turn" in event for event in progress_events)


def test_agent_turn_returns_fallback_after_repeated_empty_responses() -> None:
    model = ScriptedModel(
        [
            AgentStep(type="assistant", content=""),
            AgentStep(type="assistant", content=""),
            AgentStep(type="assistant", content=""),
        ]
    )
    registry = ToolRegistry([])

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
    )

    assert "empty response" in messages[-1]["content"].lower()


def test_tool_registry_dispose_calls_disposer() -> None:
    disposed: list[bool] = []
    registry = ToolRegistry([], disposer=lambda: disposed.append(True))

    registry.dispose()

    assert disposed == [True]


def test_agent_turn_passes_store_to_provider_adapter() -> None:
    model = StoreCapturingModel()
    registry = ToolRegistry([])
    store = create_app_store()

    messages = run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        store=store,
    )

    assert messages[-1] == {"role": "assistant", "content": "done"}
    assert model.received_store is store


def test_shared_budget_records_actual_model_usage() -> None:
    budget = AgentTurnBudget(max_total_tokens=1000, max_model_calls=5)
    model = ScriptedModel(
        [
            AgentStep(
                type="assistant",
                content="done",
                usage=ModelUsage(
                    input_tokens=20,
                    output_tokens=5,
                    source="provider",
                ),
            )
        ]
    )

    run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        agent_budget=budget,
    )

    snapshot = budget.snapshot()
    assert snapshot.used_model_calls == 1
    assert snapshot.used_total_tokens == 25


def test_budget_exhaustion_falls_back_for_top_level_turn() -> None:
    budget = AgentTurnBudget(max_total_tokens=1)
    model = ScriptedModel([AgentStep(type="assistant", content="never called")])

    messages = run_agent_turn(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "this is long enough"}],
        cwd=".",
        agent_budget=budget,
        budget_exhausted_policy="fallback",
    )

    assert model.calls == 0
    assert "turn_budget_exceeded" in messages[-1]["content"]


def test_model_timeout_records_failed_typed_outcome() -> None:
    class TimeoutModel(ModelAdapter):
        def next(self, _messages, on_stream_chunk=None):
            raise TimeoutError("provider deadline")

    capture = AgentOutcomeCapture()

    messages = run_agent_turn(
        model=TimeoutModel(),
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        outcome_capture=capture,
    )

    assert "Model API timeout" in messages[-1]["content"]
    assert capture.outcome is not None
    assert capture.outcome.status == "failed"
    assert capture.outcome.goal_achieved is False


def test_budget_exhaustion_can_raise_for_nested_loop() -> None:
    budget = AgentTurnBudget(max_total_tokens=1)
    model = ScriptedModel([AgentStep(type="assistant", content="never called")])

    with pytest.raises(AgentBudgetExceeded):
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=[{"role": "system", "content": "this is long enough"}],
            cwd=".",
            agent_budget=budget,
            budget_exhausted_policy="raise",
        )


def test_shared_budget_reaches_tool_context() -> None:
    captured: dict = {}

    def capture_budget(_input_data: dict, context) -> ToolResult:
        captured["budget"] = context._agent_budget
        return ToolResult(ok=True, output="ok")

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="capture",
                description="capture budget",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=capture_budget,
            )
        ]
    )
    budget = AgentTurnBudget(max_model_calls=3)
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "capture", "input": {}}],
            ),
            AgentStep(type="assistant", content="done"),
        ]
    )

    run_agent_turn(
        model=model,
        tools=registry,
        messages=[{"role": "system", "content": "sys"}],
        cwd=".",
        agent_budget=budget,
    )

    assert captured["budget"] is budget


def test_abandoned_tool_worker_stops_before_next_model_call() -> None:
    import threading

    model = ScriptedModel([AgentStep(type="assistant", content="never called")])
    abandoned = threading.Event()
    abandoned.set()

    with pytest.raises(ToolExecutionAbandoned):
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=[{"role": "system", "content": "sys"}],
            cwd=".",
            abandoned_event=abandoned,
        )

    assert model.calls == 0
