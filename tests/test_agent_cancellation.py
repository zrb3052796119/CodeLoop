from __future__ import annotations

import threading

import pytest

import minicode.agent_loop as agent_loop_module
import minicode.tooling as tooling_module
from minicode.agent_metrics import AgentMetricsCollector
from minicode.agent_loop import run_agent_turn
from minicode.central_scheduler import (
    CentralScheduler,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.scheduler_trace import (
    extract_scheduler_trace,
    project_scheduler_trace,
    render_scheduler_trace,
)
from minicode.subagent_result import (
    extract_subagent_result,
    project_subagent_result,
    render_subagent_result,
)
from minicode.task_outcome import AgentOutcomeCapture
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)
from minicode.types import AgentStep


TURN_ID = "turn_" + "a" * 32


def _finalized_authority_output() -> str:
    scheduler = CentralScheduler()
    profile = TaskProfile(
        task_id="task_" + "b" * 32,
        kind=TaskKind.READ_ONLY,
        risk=TaskRisk.LOW,
        complex=False,
        parent_has_sufficient_context=True,
    )
    plan = scheduler.plan(profile)
    transitions = []
    outcome = scheduler.execute(
        profile,
        workflow_id="wf_" + "c" * 32,
        run_worker=lambda _assignment: pytest.fail("worker dispatched"),
        run_verifier=lambda _assignment: pytest.fail("verifier dispatched"),
        commit=lambda _request: pytest.fail("commit requested"),
        on_transition=transitions.append,
    )
    trace = project_scheduler_trace(plan, outcome, transitions)
    projected = project_subagent_result(
        None,
        subagent_id="sub_" + "d" * 32,
        agent_type="workflow",
        outcome="completed",
        fallback_summary="Finalized state change.",
    )
    return (
        "=== WORKFLOW FINALIZATION v1 ===\n"
        '{"changesCommitted":true,"finalizationVersion":1}'
        "\n\n"
        + render_scheduler_trace(trace)
        + "\n\n"
        + render_subagent_result(projected)
    )


class BlockingModel:
    def __init__(self, started: threading.Event, release: threading.Event, result) -> None:
        self.started = started
        self.release = release
        self.result = result
        self.calls = 0

    def next(self, _messages, on_stream_chunk=None):
        self.calls += 1
        self.started.set()
        assert self.release.wait(5)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class ScriptedModel:
    def __init__(self, steps) -> None:
        self.steps = list(steps)
        self.calls = 0

    def next(self, _messages, on_stream_chunk=None):
        step = self.steps[self.calls]
        self.calls += 1
        return step


def _run_in_thread(**kwargs):
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["result"] = run_agent_turn(**kwargs)
        except BaseException as error:  # test captures exact control-flow identity
            outcome["error"] = error

    worker = threading.Thread(target=target)
    worker.start()
    return worker, outcome


def test_cancel_before_first_model_sends_no_provider_request() -> None:
    token = TurnCancellationToken(TURN_ID)
    token.request()
    model = ScriptedModel([AgentStep(type="assistant", content="forbidden")])

    with pytest.raises(TurnCancellationRequested):
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
        )

    assert model.calls == 0


def test_cancel_during_model_stops_after_return_without_assistant_or_next_work() -> None:
    started = threading.Event()
    release = threading.Event()
    token = TurnCancellationToken(TURN_ID)
    model = BlockingModel(started, release, AgentStep(type="assistant", content="late"))
    worker, outcome = _run_in_thread(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    assert started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), TurnCancellationRequested)
    assert model.calls == 1
    assert "result" not in outcome


def test_cancel_before_tool_execution_does_not_invoke_tool() -> None:
    token = TurnCancellationToken(TURN_ID)
    calls: list[str] = []

    def forbidden(_input, _context):
        calls.append("tool")
        return ToolResult(ok=True, output="forbidden")

    tools = ToolRegistry([
        ToolDefinition(
            name="write_safe",
            description="safe fixture",
            input_schema={"type": "object"},
            validator=lambda value: value,
            run=forbidden,
        )
    ])

    class CancellingModel:
        def next(self, _messages, on_stream_chunk=None):
            token.request()
            return AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "write_safe", "input": {}}],
            )

    with pytest.raises(TurnCancellationRequested):
        run_agent_turn(
            model=CancellingModel(),
            tools=tools,
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
        )

    assert calls == []


def test_cancel_during_tool_allows_started_tool_to_finish_then_stops() -> None:
    started = threading.Event()
    release = threading.Event()
    token = TurnCancellationToken(TURN_ID)
    tool_calls: list[str] = []
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "side_effect", "input": {}}],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    def side_effect(_input, _context):
        tool_calls.append("started")
        started.set()
        assert release.wait(5)
        tool_calls.append("finished")
        return ToolResult(ok=True, output="effect happened")

    tools = ToolRegistry([
        ToolDefinition(
            name="side_effect",
            description="fixture",
            input_schema={"type": "object"},
            validator=lambda value: value,
            run=side_effect,
        )
    ])
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    assert started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert tool_calls == ["started", "finished"]
    assert model.calls == 1
    assert isinstance(outcome.get("error"), TurnCancellationRequested)


def test_cancel_racing_finalized_state_change_preserves_tool_result() -> None:
    started = threading.Event()
    release = threading.Event()
    token = TurnCancellationToken(TURN_ID)
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "stateful", "input": {}}],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    authority_output = _finalized_authority_output()

    def stateful(_input, _context):
        started.set()
        assert release.wait(5)
        return ToolResult(
            ok=False,
            output=authority_output,
            _must_deliver=True,
        )

    def broken_result_callback(_name, _output, _is_error):
        raise RuntimeError("post-tool observer failed")

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="stateful",
                description="stateful fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=stateful,
            )
        ]
    )
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
        on_tool_result=broken_result_callback,
    )

    assert started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert "error" not in outcome
    messages = outcome["result"]
    assert isinstance(messages, list)
    assert model.calls == 1
    tool_result = next(
        message for message in messages if message.get("role") == "tool_result"
    )
    assert tool_result["content"] == authority_output
    assert extract_scheduler_trace(tool_result["content"]) is not None
    assert extract_subagent_result(tool_result["content"]) is not None
    assert "=== WORKFLOW FINALIZATION v1 ===" in str(tool_result["content"])


def test_metrics_failures_cannot_override_a_finalized_state_change() -> None:
    authority_output = _finalized_authority_output()

    class BrokenMetrics(AgentMetricsCollector):
        def end_tool(self, *, success, error=""):
            del success, error
            raise KeyboardInterrupt("end_tool observer interrupted")

        def end_turn(self, *, total_tokens=0):
            del total_tokens
            raise KeyboardInterrupt("end_turn observer interrupted")

    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "stateful", "input": {}}],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="stateful",
                description="finalized fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: ToolResult(
                    ok=True,
                    output=authority_output,
                    _must_deliver=True,
                ),
            )
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        metrics_collector=BrokenMetrics(),
    )

    assert model.calls == 1
    tool_result = next(
        message for message in messages if message.get("role") == "tool_result"
    )
    assert tool_result["content"] == authority_output
    assert extract_scheduler_trace(tool_result["content"]) is not None


def test_registry_post_run_sanitizer_cannot_override_finalized_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_output = _finalized_authority_output()
    sanitizer_calls: list[str] = []

    def interrupted_sanitizer(_output, _tool_name):
        sanitizer_calls.append("called")
        raise KeyboardInterrupt("post-run sanitizer interrupted")

    monkeypatch.setattr(tooling_module, "_LARGE_OUTPUT_THRESHOLD", 0)
    monkeypatch.setattr(
        tooling_module,
        "_smart_truncate_output",
        interrupted_sanitizer,
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "stateful", "input": {}}],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="stateful",
                description="finalized fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: ToolResult(
                    ok=True,
                    output=authority_output,
                    _must_deliver=True,
                ),
            )
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
    )

    assert sanitizer_calls == []
    assert model.calls == 1
    tool_result = next(
        message for message in messages if message.get("role") == "tool_result"
    )
    assert tool_result["content"] == authority_output


def test_generator_exit_in_post_tool_callback_cannot_drop_finalized_authority() -> (
    None
):
    authority_output = _finalized_authority_output()
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[{"id": "1", "toolName": "stateful", "input": {}}],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="stateful",
                description="finalized fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: ToolResult(
                    ok=True,
                    output=authority_output,
                    _must_deliver=True,
                ),
            )
        ]
    )

    def exit_callback(*_args) -> None:
        raise GeneratorExit

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        on_tool_result=exit_callback,
    )

    assert model.calls == 1
    tool_result = next(
        message for message in messages if message.get("role") == "tool_result"
    )
    assert tool_result["content"] == authority_output


def test_finalized_state_change_skips_later_serial_calls_and_stays_deliverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = TurnCancellationToken(TURN_ID)
    calls: list[str] = []
    outcome_capture = AgentOutcomeCapture()
    original_scheduler_init = agent_loop_module.ToolScheduler.__init__

    def scheduler_init_with_forced_nudge(self, *args, **kwargs):
        original_scheduler_init(self, *args, **kwargs)
        self._force_nudge_frequency = True

    monkeypatch.setattr(
        agent_loop_module.ToolScheduler,
        "__init__",
        scheduler_init_with_forced_nudge,
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "first_stateful", "input": {}},
                    {"id": "2", "toolName": "second_stateful", "input": {}},
                ],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    def first(_input, _context):
        calls.append("first")
        token.request()
        return ToolResult(
            ok=True,
            output="changesCommitted=true",
            _must_deliver=True,
        )

    def second(_input, _context):
        calls.append("second")
        return ToolResult(ok=True, output="forbidden")

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="first_stateful",
                description="first fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=first,
            ),
            ToolDefinition(
                name="second_stateful",
                description="second fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=second,
            ),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
        outcome_capture=outcome_capture,
    )

    assert calls == ["first"]
    assert model.calls == 1
    tool_results = [
        message for message in messages if message.get("role") == "tool_result"
    ]
    assert len(tool_results) == 2
    assert tool_results[0]["content"] == "changesCommitted=true"
    assert "skipped_after_finalized_state_change" in str(
        tool_results[1]["content"]
    )
    assert outcome_capture.outcome is not None
    assert outcome_capture.outcome.status == "success"


def test_recovery_stop_cannot_override_a_finalized_state_change() -> None:
    authority_output = _finalized_authority_output()
    outcome_capture = AgentOutcomeCapture()
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "denied-first",
                        "toolName": "write_file",
                        "input": {"path": "blocked.txt"},
                    }
                ],
            ),
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "denied-repeat-1",
                        "toolName": "write_file",
                        "input": {"path": "blocked.txt"},
                    },
                    {
                        "id": "denied-repeat-2",
                        "toolName": "write_file",
                        "input": {"path": "blocked.txt"},
                    },
                    {
                        "id": "finalized",
                        "toolName": "stateful",
                        "input": {},
                    },
                ],
            ),
        ]
    )

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="write_file",
                description="denied fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: ToolResult(
                    ok=False,
                    output="error[permission_denied]: write denied",
                ),
            ),
            ToolDefinition(
                name="stateful",
                description="finalized fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: ToolResult(
                    ok=True,
                    output=authority_output,
                    _must_deliver=True,
                ),
            ),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        outcome_capture=outcome_capture,
    )

    assert model.calls == 2
    assert messages[-1]["role"] == "tool_result"
    assert messages[-1]["content"] == authority_output
    assert extract_scheduler_trace(messages[-1]["content"]) is not None
    assert extract_subagent_result(messages[-1]["content"]) is not None
    assert outcome_capture.outcome is not None
    assert outcome_capture.outcome.status == "success"
    assert outcome_capture.outcome.had_tool_errors is True


def test_cancel_before_concurrent_batch_schedules_no_tool() -> None:
    token = TurnCancellationToken(TURN_ID)
    calls: list[str] = []

    def forbidden(_input, _context):
        calls.append("tool")
        return ToolResult(ok=True, output="forbidden")

    tools = ToolRegistry([
        ToolDefinition(
            name=name,
            description="read fixture",
            input_schema={"type": "object"},
            validator=lambda value: value,
            run=forbidden,
        )
        for name in ("read_file", "list_files")
    ])

    class CancellingBatchModel:
        def next(self, _messages, on_stream_chunk=None):
            token.request()
            return AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "read_file", "input": {}},
                    {"id": "2", "toolName": "list_files", "input": {}},
                ],
            )

    with pytest.raises(TurnCancellationRequested):
        run_agent_turn(
            model=CancellingBatchModel(),
            tools=tools,
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
        )

    assert calls == []


def test_cancel_after_concurrent_batch_starts_waits_for_batch_then_stops() -> None:
    token = TurnCancellationToken(TURN_ID)
    all_started = threading.Barrier(3)
    release = threading.Event()
    finished: list[str] = []

    def definition(name: str) -> ToolDefinition:
        def run(_input, _context):
            all_started.wait(5)
            assert release.wait(5)
            finished.append(name)
            return ToolResult(ok=True, output=name)

        return ToolDefinition(
            name=name,
            description="read fixture",
            input_schema={"type": "object"},
            validator=lambda value: value,
            run=run,
        )

    tools = ToolRegistry([definition("read_file"), definition("list_files")])
    model = ScriptedModel([
        AgentStep(
            type="tool_calls",
            calls=[
                {"id": "1", "toolName": "read_file", "input": {}},
                {"id": "2", "toolName": "list_files", "input": {}},
            ],
        ),
        AgentStep(type="assistant", content="forbidden"),
    ])
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    all_started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert sorted(finished) == ["list_files", "read_file"]
    assert model.calls == 1
    assert isinstance(outcome.get("error"), TurnCancellationRequested)


def test_cancel_during_provider_error_bypasses_recovery_and_fallback() -> None:
    started = threading.Event()
    release = threading.Event()
    token = TurnCancellationToken(TURN_ID)
    model = BlockingModel(started, release, ConnectionError("fixture network"))
    worker, outcome = _run_in_thread(
        model=model,
        tools=ToolRegistry([]),
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    assert started.wait(5)
    token.request()
    release.set()
    worker.join(5)

    assert isinstance(outcome.get("error"), TurnCancellationRequested)
    assert model.calls == 1


def test_keyboard_interrupt_identity_is_preserved_with_uncancelled_token() -> None:
    started = threading.Event()
    release = threading.Event()
    release.set()
    model = BlockingModel(started, release, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_agent_turn(
            model=model,
            tools=ToolRegistry([]),
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=TurnCancellationToken(TURN_ID),
        )
