from __future__ import annotations

import concurrent.futures
import threading

import pytest

import minicode.agent_loop as agent_loop_module
import minicode.tooling as tooling_module
from minicode.agent_loop import run_agent_turn
from minicode.agent_metrics import AgentMetricsCollector
from minicode.central_scheduler import (
    CentralScheduler,
    TaskKind,
    TaskProfile,
    TaskRisk,
)
from minicode.concurrent_tool_terminal import project_concurrent_tool_terminal
from minicode.hooks import HookEvent
from minicode.scheduler_trace import (
    extract_scheduler_trace,
    project_scheduler_trace,
    render_scheduler_trace,
)
from minicode.state import create_app_store
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


class CustomFatalSignal(BaseException):
    pass


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


def _invoke_after_first_completed_wait(
    monkeypatch: pytest.MonkeyPatch,
    callback,
) -> None:
    original_wait = concurrent.futures.wait
    invoked = False

    def wrapped_wait(fs, *args, **kwargs):
        nonlocal invoked
        done, pending = original_wait(fs, *args, **kwargs)
        if done and not invoked:
            invoked = True
            callback()
        return done, pending

    monkeypatch.setattr(
        agent_loop_module.concurrent.futures,
        "wait",
        wrapped_wait,
    )


def _raise_after_outer_pool_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    calls: list[tuple[bool, bool]],
) -> None:
    original_shutdown = concurrent.futures.ThreadPoolExecutor.shutdown

    def wrapped_shutdown(
        executor,
        wait=True,
        *,
        cancel_futures=False,
    ):
        result = original_shutdown(
            executor,
            wait=wait,
            cancel_futures=cancel_futures,
        )
        if getattr(executor, "_thread_name_prefix", "") == "mc-tool":
            calls.append((wait, cancel_futures))
            raise error
        return result

    monkeypatch.setattr(
        agent_loop_module.concurrent.futures.ThreadPoolExecutor,
        "shutdown",
        wrapped_shutdown,
    )


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


def test_model_tool_call_limit_fails_closed_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []

    def run_read(_input, _context):
        executed.append("tool")
        return ToolResult(ok=True, output="forbidden")

    def forbidden_schedule(*_args, **_kwargs):
        pytest.fail("over-limit calls reached the scheduler")

    monkeypatch.setattr(
        agent_loop_module.ToolScheduler,
        "schedule_calls",
        forbidden_schedule,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="limit fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=run_read,
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"call-{index}",
                        "toolName": "read_file",
                        "input": {},
                    }
                    for index in range(1_001)
                ],
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

    assert executed == []
    assert model.calls == 1
    assert messages[-1] == {
        "role": "assistant",
        "content": (
            "error[tool_call_limit_exceeded]: model returned 1001 tool "
            "calls; the per-turn limit is 1000. No tools were executed."
        ),
    }


def test_cancel_after_concurrent_batch_starts_stops_before_next_model_turn() -> None:
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


def test_cancel_after_concurrent_results_finalize_post_hooks_before_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = TurnCancellationToken(TURN_ID)
    original_guard = agent_loop_module.RecoveryGuard
    observed_results = 0
    post_tools: list[str] = []

    class CancellingGuard(original_guard):
        def observe(self, call, *, ok, output) -> None:
            nonlocal observed_results
            super().observe(call, ok=ok, output=output)
            observed_results += 1
            if observed_results == 2:
                token.request()

    def record_hook(event, **kwargs) -> None:
        if event is HookEvent.POST_TOOL_USE:
            post_tools.append(str(kwargs["tool_name"]))

    monkeypatch.setattr(agent_loop_module, "RecoveryGuard", CancellingGuard)
    monkeypatch.setattr(agent_loop_module, "fire_hook_sync", record_hook)
    tools = ToolRegistry(
        [
            ToolDefinition(
                name=name,
                description="completed read fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context, result=name: ToolResult(
                    ok=True,
                    output=result,
                ),
            )
            for name in ("read_file", "list_files")
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "read_file", "input": {}},
                    {"id": "2", "toolName": "list_files", "input": {}},
                ],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    with pytest.raises(TurnCancellationRequested):
        run_agent_turn(
            model=model,
            tools=tools,
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
        )

    assert model.calls == 1
    assert observed_results == 2
    assert sorted(post_tools) == ["list_files", "read_file"]


def test_cancel_sweep_finalizes_only_completed_result_before_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = TurnCancellationToken(TURN_ID)
    release_hung = threading.Event()
    hung_started = threading.Event()
    hung_finished = threading.Event()
    post_tools: list[str] = []
    trace_calls: list[str] = []
    ledgers = []
    store = create_app_store()
    original_from_messages = (
        agent_loop_module.TaskLedger.from_messages.__func__
    )
    original_append_trace = agent_loop_module._append_tool_trace_events

    def capture_ledger(cls, messages):
        ledger = original_from_messages(cls, messages)
        if ledger is not None:
            ledgers.append(ledger)
        return ledger

    def record_hook(event, **kwargs) -> None:
        if event is HookEvent.POST_TOOL_USE:
            post_tools.append(str(kwargs["tool_name"]))

    def record_trace(trace, call, result, step, recovery_note=None) -> None:
        trace_calls.append(str(call["id"]))
        original_append_trace(
            trace,
            call,
            result,
            step,
            recovery_note=recovery_note,
        )

    def fast_failure(_input, _context):
        assert hung_started.wait(5)
        return ToolResult(
            ok=False,
            output="error[fixture_failure]: completed before cancellation",
        )

    def hung(_input, _context):
        hung_started.set()
        assert release_hung.wait(5)
        hung_finished.set()
        return ToolResult(ok=True, output="late sibling result")

    monkeypatch.setattr(
        agent_loop_module.TaskLedger,
        "from_messages",
        classmethod(capture_ledger),
    )
    monkeypatch.setattr(agent_loop_module, "fire_hook_sync", record_hook)
    monkeypatch.setattr(
        agent_loop_module,
        "_append_tool_trace_events",
        record_trace,
    )
    _invoke_after_first_completed_wait(monkeypatch, token.request)
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="completed failure fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=fast_failure,
            ),
            ToolDefinition(
                name="list_files",
                description="hung sibling fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=hung,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "fast", "toolName": "read_file", "input": {}},
                    {"id": "hung", "toolName": "list_files", "input": {}},
                ],
            )
        ]
    )
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[
            {"role": "system", "content": "safe"},
            {"role": "user", "content": "finish the fixture"},
        ],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
        store=store,
    )

    try:
        assert hung_started.wait(5)
        worker.join(0.5)

        assert not worker.is_alive()
        assert isinstance(outcome.get("error"), TurnCancellationRequested)
        assert post_tools == ["read_file"]
        assert store.get_state().tool_call_count == 1
        assert len(ledgers) == 1
        assert ledgers[0].rejected_attempts == [
            "read_file: error[fixture_failure]"
        ]
        assert trace_calls.count("fast") == 1
        assert trace_calls.count("hung") == 1
    finally:
        release_hung.set()
        worker.join(5)
        assert hung_finished.wait(5)


def test_cancel_requested_during_post_hook_preempts_await_user_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = TurnCancellationToken(TURN_ID)
    post_seen = threading.Event()

    def request_from_post_hook(event, **_kwargs) -> None:
        if event is HookEvent.POST_TOOL_USE:
            post_seen.set()
            token.request()

    monkeypatch.setattr(
        agent_loop_module,
        "fire_hook_sync",
        request_from_post_hook,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="ask_user",
                description="await-user fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: ToolResult(
                    ok=True,
                    output="Please confirm",
                    awaitUser=True,
                ),
            )
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "await", "toolName": "ask_user", "input": {}}
                ],
            )
        ]
    )

    with pytest.raises(TurnCancellationRequested):
        run_agent_turn(
            model=model,
            tools=tools,
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
        )

    assert post_seen.is_set()


def test_cancel_concurrent_batch_does_not_wait_for_uncooperative_read() -> None:
    token = TurnCancellationToken(TURN_ID)
    cancellable_started = threading.Event()
    blocked_started = threading.Event()
    release_cancellable = threading.Event()
    release_blocked = threading.Event()
    events: list[tuple[str, dict[str, object]]] = []

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            del step
            events.append((event_type, dict(payload or {})))

    def cancellable(_input, _context):
        cancellable_started.set()
        assert release_cancellable.wait(5)
        token.request()
        return ToolResult(ok=True, output="late cancellable result")

    def blocked(_input, _context):
        blocked_started.set()
        assert release_blocked.wait(5)
        return ToolResult(ok=True, output="late blocked result")

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="cancellable fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=cancellable,
            ),
            ToolDefinition(
                name="list_files",
                description="blocked fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=blocked,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "read_file", "input": {}},
                    {"id": "2", "toolName": "list_files", "input": {}},
                ],
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
        event_sink=RecordingSink(),
    )

    try:
        assert cancellable_started.wait(5)
        assert blocked_started.wait(5)
        release_cancellable.set()

        worker.join(0.5)
        assert not worker.is_alive()
        assert isinstance(outcome.get("error"), TurnCancellationRequested)
        terminal_events = [
            payload
            for event_type, payload in events
            if event_type == "tool.concurrent_terminal"
        ]
        assert len(terminal_events) == 2
        call_1_ref = project_concurrent_tool_terminal(
            call_id="1",
            tool_name="read_file",
            status="cancelled",
        )["callId"]
        call_2_ref = project_concurrent_tool_terminal(
            call_id="2",
            tool_name="list_files",
            status="abandoned_running",
        )["callId"]
        by_call_id = {payload["callId"]: payload for payload in terminal_events}
        assert set(by_call_id) == {call_1_ref, call_2_ref}
        assert by_call_id[call_1_ref] == {
            "terminalVersion": 1,
            "callId": call_1_ref,
            "toolName": "read_file",
            "status": "cancelled",
            "reason": "turn_cancelled",
            "executionMayContinue": False,
        }
        assert by_call_id[call_2_ref] == {
            "terminalVersion": 1,
            "callId": call_2_ref,
            "toolName": "list_files",
            "status": "abandoned_running",
            "reason": "turn_cancelled",
            "executionMayContinue": True,
        }
    finally:
        release_cancellable.set()
        release_blocked.set()
        worker.join(5)


def test_cancel_concurrent_batch_wakes_when_every_read_is_uncooperative() -> None:
    token = TurnCancellationToken(TURN_ID)
    all_started = threading.Barrier(3)
    release = threading.Event()

    def blocked(_input, _context):
        all_started.wait(5)
        assert release.wait(5)
        return ToolResult(ok=True, output="late result")

    tools = ToolRegistry(
        [
            ToolDefinition(
                name=name,
                description="blocked read fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=blocked,
            )
            for name in ("read_file", "list_files")
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "read_file", "input": {}},
                    {"id": "2", "toolName": "list_files", "input": {}},
                ],
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
    )

    try:
        all_started.wait(5)
        token.request()

        worker.join(0.5)
        assert not worker.is_alive()
        assert isinstance(outcome.get("error"), TurnCancellationRequested)
    finally:
        release.set()
        worker.join(5)


def test_concurrent_finalized_authority_outranks_sibling_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = TurnCancellationToken(TURN_ID)
    allow_cancellation = threading.Event()
    authority_output = _finalized_authority_output()

    def finalized(_input, _context):
        return ToolResult(
            ok=True,
            output=authority_output,
            _must_deliver=True,
        )

    def cancels_turn(_input, _context):
        assert allow_cancellation.wait(5)
        token.request()
        return ToolResult(ok=True, output="must not replace authority")

    _invoke_after_first_completed_wait(
        monkeypatch,
        allow_cancellation.set,
    )

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="finalized authority fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=finalized,
            ),
            ToolDefinition(
                name="list_files",
                description="cancellation fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=cancels_turn,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "authority", "toolName": "read_file", "input": {}},
                    {"id": "cancel", "toolName": "list_files", "input": {}},
                ],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        cancellation_token=token,
    )

    assert model.calls == 1
    results = [
        message for message in messages if message.get("role") == "tool_result"
    ]
    assert len(results) == 2
    assert results[0]["toolUseId"] == "authority"
    assert results[0]["content"] == authority_output
    assert results[1]["toolUseId"] == "cancel"
    assert "turn_cancelled" in str(results[1]["content"])


def test_concurrent_finalized_authority_survives_fatal_terminal_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = TurnCancellationToken(TURN_ID)
    allow_cancellation = threading.Event()
    cancellation_requested = threading.Event()
    release_cancellation = threading.Event()
    authority_output = _finalized_authority_output()
    post_tools: list[str] = []
    store = create_app_store()

    class FatalTerminalSink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            del step, payload
            if event_type == "tool.concurrent_terminal":
                raise KeyboardInterrupt("terminal sink failed")

    def finalized(_input, _context):
        return ToolResult(
            ok=True,
            output=authority_output,
            _must_deliver=True,
        )

    def cancels_turn(_input, _context):
        assert allow_cancellation.wait(5)
        token.request()
        cancellation_requested.set()
        assert release_cancellation.wait(5)
        return ToolResult(ok=True, output="must not replace authority")

    def record_hook(event, **kwargs) -> None:
        if event is HookEvent.POST_TOOL_USE:
            post_tools.append(str(kwargs["tool_name"]))

    _invoke_after_first_completed_wait(
        monkeypatch,
        allow_cancellation.set,
    )
    monkeypatch.setattr(agent_loop_module, "fire_hook_sync", record_hook)
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="finalized authority fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=finalized,
            ),
            ToolDefinition(
                name="list_files",
                description="cancellation fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=cancels_turn,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "authority", "toolName": "read_file", "input": {}},
                    {"id": "cancel", "toolName": "list_files", "input": {}},
                ],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    try:
        messages = run_agent_turn(
            model=model,
            tools=tools,
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
            cancellation_token=token,
            event_sink=FatalTerminalSink(),
            store=store,
        )
        assert cancellation_requested.is_set()
    finally:
        release_cancellation.set()

    results = [
        message for message in messages if message.get("role") == "tool_result"
    ]
    assert model.calls == 1
    assert results[0]["toolUseId"] == "authority"
    assert results[0]["content"] == authority_output
    assert post_tools == ["read_file"]
    assert store.get_state().tool_call_count == 1


def test_fatal_terminal_sink_outranks_cancellation_without_authority() -> None:
    token = TurnCancellationToken(TURN_ID)
    all_started = threading.Barrier(3)
    release = threading.Event()
    fatal = KeyboardInterrupt("terminal sink failed")

    class FatalTerminalSink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            del step, payload
            if event_type == "tool.concurrent_terminal":
                raise fatal

    def blocked(_input, _context):
        all_started.wait(5)
        assert release.wait(5)
        return ToolResult(ok=True, output="late result")

    tools = ToolRegistry(
        [
            ToolDefinition(
                name=name,
                description="blocked read fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=blocked,
            )
            for name in ("read_file", "list_files")
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "read_file", "input": {}},
                    {"id": "2", "toolName": "list_files", "input": {}},
                ],
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
        event_sink=FatalTerminalSink(),
    )

    try:
        all_started.wait(5)
        token.request()
        worker.join(0.5)

        assert not worker.is_alive()
        assert outcome.get("error") is fatal
    finally:
        release.set()
        worker.join(5)


def test_concurrent_fatal_signal_outranks_turn_cancellation() -> None:
    cancellation = TurnCancellationRequested(TURN_ID)
    fatal = KeyboardInterrupt("fatal worker signal")
    fatal_future: concurrent.futures.Future[ToolResult] = (
        concurrent.futures.Future()
    )
    cancelled_future: concurrent.futures.Future[ToolResult] = (
        concurrent.futures.Future()
    )
    fatal_future.set_exception(fatal)
    cancelled_future.set_exception(cancellation)

    decision = agent_loop_module._reduce_concurrent_batch_shutdown(
        future_to_call={
            fatal_future: {"id": "fatal", "toolName": "read_file", "input": {}},
            cancelled_future: {
                "id": "cancelled",
                "toolName": "list_files",
                "input": {},
            },
        },
        observed_results={},
        observed_errors={},
        cancellation=cancellation,
    )

    assert decision.control_error is fatal
    assert [item.status for item in decision.dispositions] == [
        "failed",
        "cancelled",
    ]


def test_custom_base_exception_outranks_turn_cancellation() -> None:
    cancellation = TurnCancellationRequested(TURN_ID)
    fatal = CustomFatalSignal("custom fatal worker signal")
    fatal_future: concurrent.futures.Future[ToolResult] = (
        concurrent.futures.Future()
    )
    cancelled_future: concurrent.futures.Future[ToolResult] = (
        concurrent.futures.Future()
    )
    fatal_future.set_exception(fatal)
    cancelled_future.set_exception(cancellation)

    decision = agent_loop_module._reduce_concurrent_batch_shutdown(
        future_to_call={
            fatal_future: {"id": "fatal", "toolName": "read_file", "input": {}},
            cancelled_future: {
                "id": "cancelled",
                "toolName": "list_files",
                "input": {},
            },
        },
        observed_results={},
        observed_errors={},
        cancellation=cancellation,
    )

    assert decision.control_error is fatal


def test_custom_base_exception_detaches_hung_sibling_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hung_started = threading.Event()
    hung_finished = threading.Event()
    release_hung = threading.Event()
    fatal = CustomFatalSignal("custom fatal worker signal")
    cleanup_fatal = CustomFatalSignal("custom fatal from outer shutdown")
    shutdown_calls: list[tuple[bool, bool]] = []
    captured_statuses: dict[str, str] = {}
    terminal_events: list[dict[str, object]] = []
    original_reducer = agent_loop_module._reduce_concurrent_batch_shutdown

    def recording_reducer(**kwargs):
        decision = original_reducer(**kwargs)
        captured_statuses.update(
            {
                str(item.call["id"]): item.status
                for item in decision.dispositions
            }
        )
        return decision

    class RecordingSink:
        def emit(self, event_type, *, step=None, payload=None) -> None:
            del step
            if event_type == "tool.concurrent_terminal":
                terminal_events.append(dict(payload or {}))

    def hung(_input, _context):
        hung_started.set()
        assert release_hung.wait(5)
        hung_finished.set()
        return ToolResult(ok=True, output="late sibling result")

    def raises_fatal(_input, _context):
        assert hung_started.wait(5)
        raise fatal

    monkeypatch.setattr(
        agent_loop_module,
        "_reduce_concurrent_batch_shutdown",
        recording_reducer,
    )
    _raise_after_outer_pool_shutdown(
        monkeypatch,
        cleanup_fatal,
        shutdown_calls,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="custom fatal fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=raises_fatal,
            ),
            ToolDefinition(
                name="list_files",
                description="hung sibling fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=hung,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "fatal", "toolName": "read_file", "input": {}},
                    {"id": "hung", "toolName": "list_files", "input": {}},
                ],
            )
        ]
    )
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
        event_sink=RecordingSink(),
    )

    try:
        assert hung_started.wait(5)
        worker.join(0.5)

        assert not worker.is_alive()
        assert outcome.get("error") is fatal
        assert captured_statuses == {
            "fatal": "failed",
            "hung": "abandoned_running",
        }
        assert shutdown_calls == [(False, True)]
        assert terminal_events == []
    finally:
        release_hung.set()
        worker.join(5)
        assert hung_finished.wait(5)


def test_main_thread_wait_fatal_detaches_all_hung_tools_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    fatal = CustomFatalSignal("custom fatal from concurrent wait")
    captured_statuses: dict[str, str] = {}
    original_reducer = agent_loop_module._reduce_concurrent_batch_shutdown

    def recording_reducer(**kwargs):
        decision = original_reducer(**kwargs)
        captured_statuses.update(
            {
                str(item.call["id"]): item.status
                for item in decision.dispositions
            }
        )
        return decision

    def fatal_wait(_futures, *args, **kwargs):
        del args, kwargs
        assert first_started.wait(5)
        assert second_started.wait(5)
        raise fatal

    def first_hung(_input, _context):
        first_started.set()
        assert release.wait(5)
        return ToolResult(ok=True, output="late first result")

    def second_hung(_input, _context):
        second_started.set()
        assert release.wait(5)
        return ToolResult(ok=True, output="late second result")

    monkeypatch.setattr(
        agent_loop_module,
        "_reduce_concurrent_batch_shutdown",
        recording_reducer,
    )
    monkeypatch.setattr(
        agent_loop_module.concurrent.futures,
        "wait",
        fatal_wait,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="first hung fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=first_hung,
            ),
            ToolDefinition(
                name="list_files",
                description="second hung fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=second_hung,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "first", "toolName": "read_file", "input": {}},
                    {"id": "second", "toolName": "list_files", "input": {}},
                ],
            )
        ]
    )
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
    )

    try:
        assert first_started.wait(5)
        assert second_started.wait(5)
        worker.join(0.5)

        assert not worker.is_alive()
        assert outcome.get("error") is fatal
        assert captured_statuses == {
            "first": "abandoned_running",
            "second": "abandoned_running",
        }
    finally:
        release.set()
        worker.join(5)


def test_partial_submit_fatal_detaches_already_running_tool_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = threading.Event()
    release = threading.Event()
    fatal = CustomFatalSignal("custom fatal from partial dispatch")
    original_submit = concurrent.futures.ThreadPoolExecutor.submit
    submit_count = 0

    def fatal_second_submit(executor, function, /, *args, **kwargs):
        nonlocal submit_count
        if getattr(executor, "_thread_name_prefix", "") == "mc-tool":
            submit_count += 1
            if submit_count == 2:
                assert first_started.wait(5)
                raise fatal
        return original_submit(executor, function, *args, **kwargs)

    def first_hung(_input, _context):
        first_started.set()
        assert release.wait(5)
        return ToolResult(ok=True, output="late first result")

    second_ran = False

    def second_must_not_run(_input, _context):
        nonlocal second_ran
        second_ran = True
        return ToolResult(ok=True, output="unexpected second result")

    monkeypatch.setattr(
        agent_loop_module.concurrent.futures.ThreadPoolExecutor,
        "submit",
        fatal_second_submit,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="first hung fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=first_hung,
            ),
            ToolDefinition(
                name="list_files",
                description="never submitted fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=second_must_not_run,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "first", "toolName": "read_file", "input": {}},
                    {"id": "second", "toolName": "list_files", "input": {}},
                ],
            )
        ]
    )
    worker, outcome = _run_in_thread(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
    )

    try:
        assert first_started.wait(5)
        worker.join(0.5)

        assert not worker.is_alive()
        assert outcome.get("error") is fatal
        assert second_ran is False
    finally:
        release.set()
        worker.join(5)


def test_partial_submit_must_deliver_balances_never_submitted_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority_finished = threading.Event()
    authority_output = _finalized_authority_output()
    fatal = CustomFatalSignal("custom fatal after finalized dispatch")
    cleanup_fatal = CustomFatalSignal("custom fatal from outer shutdown")
    shutdown_calls: list[tuple[bool, bool]] = []
    original_submit = concurrent.futures.ThreadPoolExecutor.submit
    submit_count = 0
    second_ran = False

    def fatal_second_submit(executor, function, /, *args, **kwargs):
        nonlocal submit_count
        if getattr(executor, "_thread_name_prefix", "") == "mc-tool":
            submit_count += 1
            if submit_count == 2:
                assert authority_finished.wait(5)
                raise fatal
        return original_submit(executor, function, *args, **kwargs)

    def finalized(_input, _context):
        result = ToolResult(
            ok=True,
            output=authority_output,
            _must_deliver=True,
        )
        authority_finished.set()
        return result

    def second_must_not_run(_input, _context):
        nonlocal second_ran
        second_ran = True
        return ToolResult(ok=True, output="unexpected second result")

    monkeypatch.setattr(
        agent_loop_module.concurrent.futures.ThreadPoolExecutor,
        "submit",
        fatal_second_submit,
    )
    _raise_after_outer_pool_shutdown(
        monkeypatch,
        cleanup_fatal,
        shutdown_calls,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="finalized authority fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=finalized,
            ),
            ToolDefinition(
                name="list_files",
                description="never submitted fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=second_must_not_run,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "authority",
                        "toolName": "read_file",
                        "input": {},
                    },
                    {
                        "id": "never-submitted",
                        "toolName": "list_files",
                        "input": {},
                    },
                ],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
    )

    results = [
        message for message in messages if message.get("role") == "tool_result"
    ]
    assert model.calls == 1
    assert second_ran is False
    assert [result["toolUseId"] for result in results] == [
        "authority",
        "never-submitted",
    ]
    assert results[0]["content"] == authority_output
    assert "skipped_after_finalized_state_change" in str(results[1]["content"])
    assert shutdown_calls == [(False, True)]


@pytest.mark.parametrize("fatal_cleanup", [True, False])
def test_outer_shutdown_error_reduces_against_turn_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    fatal_cleanup: bool,
) -> None:
    token = TurnCancellationToken(TURN_ID)
    all_started = threading.Barrier(3)
    release = threading.Event()
    cleanup_error: BaseException = (
        CustomFatalSignal("custom fatal from outer shutdown")
        if fatal_cleanup
        else RuntimeError("ordinary outer shutdown failure")
    )
    shutdown_calls: list[tuple[bool, bool]] = []

    def blocked(_input, _context):
        all_started.wait(5)
        assert release.wait(5)
        return ToolResult(ok=True, output="late result")

    _raise_after_outer_pool_shutdown(
        monkeypatch,
        cleanup_error,
        shutdown_calls,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name=name,
                description="blocked cleanup fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=blocked,
            )
            for name in ("read_file", "list_files")
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "read_file", "input": {}},
                    {"id": "2", "toolName": "list_files", "input": {}},
                ],
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
    )

    try:
        all_started.wait(5)
        token.request()
        worker.join(0.5)

        assert not worker.is_alive()
        if fatal_cleanup:
            assert outcome.get("error") is cleanup_error
        else:
            assert isinstance(
                outcome.get("error"),
                TurnCancellationRequested,
            )
        assert shutdown_calls == [(False, True)]
    finally:
        release.set()
        worker.join(5)


def test_outer_shutdown_fatal_propagates_after_normal_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_fatal = CustomFatalSignal("custom fatal from normal shutdown")
    shutdown_calls: list[tuple[bool, bool]] = []
    _raise_after_outer_pool_shutdown(
        monkeypatch,
        cleanup_fatal,
        shutdown_calls,
    )
    tools = ToolRegistry(
        [
            ToolDefinition(
                name=name,
                description="completed cleanup fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context, output=name: ToolResult(
                    ok=True,
                    output=output,
                ),
            )
            for name in ("read_file", "list_files")
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {"id": "1", "toolName": "read_file", "input": {}},
                    {"id": "2", "toolName": "list_files", "input": {}},
                ],
            )
        ]
    )

    with pytest.raises(CustomFatalSignal) as captured:
        run_agent_turn(
            model=model,
            tools=tools,
            messages=[{"role": "system", "content": "safe"}],
            cwd=".",
            enable_work_chain=False,
        )

    assert captured.value is cleanup_fatal
    assert shutdown_calls == [(True, False)]


def test_concurrent_finalized_authority_outranks_fatal_signal() -> None:
    cancellation = TurnCancellationRequested(TURN_ID)
    authority = ToolResult(
        ok=True,
        output="changesCommitted=true",
        _must_deliver=True,
    )
    authority_future: concurrent.futures.Future[ToolResult] = (
        concurrent.futures.Future()
    )
    fatal_future: concurrent.futures.Future[ToolResult] = (
        concurrent.futures.Future()
    )
    authority_future.set_result(authority)
    fatal_future.set_exception(SystemExit("fatal worker signal"))

    decision = agent_loop_module._reduce_concurrent_batch_shutdown(
        future_to_call={
            authority_future: {
                "id": "authority",
                "toolName": "read_file",
                "input": {},
            },
            fatal_future: {"id": "fatal", "toolName": "list_files", "input": {}},
        },
        observed_results={},
        observed_errors={},
        cancellation=cancellation,
    )

    assert decision.control_error is None
    assert decision.dispositions[0].result is authority
    assert decision.dispositions[1].status == "failed"


def test_concurrent_finalized_authority_outranks_fatal_without_cancellation(
) -> None:
    authority_finished = threading.Event()
    authority_output = _finalized_authority_output()

    def finalized(_input, _context):
        authority_finished.set()
        return ToolResult(
            ok=True,
            output=authority_output,
            _must_deliver=True,
        )

    def raises_fatal(_input, _context):
        assert authority_finished.wait(5)
        raise SystemExit("fatal worker signal")

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="finalized authority fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=finalized,
            ),
            ToolDefinition(
                name="list_files",
                description="fatal fixture",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=raises_fatal,
            ),
        ]
    )
    model = ScriptedModel(
        [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": "authority",
                        "toolName": "read_file",
                        "input": {},
                    },
                    {
                        "id": "fatal",
                        "toolName": "list_files",
                        "input": {},
                    },
                ],
            ),
            AgentStep(type="assistant", content="forbidden"),
        ]
    )

    messages = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "system", "content": "safe"}],
        cwd=".",
        enable_work_chain=False,
    )

    assert model.calls == 1
    results = [
        message for message in messages if message.get("role") == "tool_result"
    ]
    assert len(results) == 2
    assert results[0]["toolUseId"] == "authority"
    assert results[0]["content"] == authority_output
    assert results[1]["toolUseId"] == "fatal"
    assert results[1]["isError"] is True


def test_concurrent_cancel_rechecks_future_that_completes_during_cancel() -> None:
    truthful = ToolResult(ok=True, output="completed during cancellation")

    class CompletesDuringCancel(concurrent.futures.Future[ToolResult]):
        def cancel(self) -> bool:
            self.set_result(truthful)
            return False

    future = CompletesDuringCancel()
    cancellation = TurnCancellationRequested(TURN_ID)

    decision = agent_loop_module._reduce_concurrent_batch_shutdown(
        future_to_call={
            future: {"id": "raced", "toolName": "read_file", "input": {}}
        },
        observed_results={},
        observed_errors={},
        cancellation=cancellation,
    )

    assert decision.control_error is cancellation
    assert len(decision.dispositions) == 1
    assert decision.dispositions[0].result is truthful
    assert decision.dispositions[0].status == "completed"


def test_concurrent_cancel_accounts_ordinary_exception_completed_during_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = ValueError("completed during cancellation")

    class FailsDuringCancel(concurrent.futures.Future[ToolResult]):
        def cancel(self) -> bool:
            self.set_exception(failure)
            return False

    future = FailsDuringCancel()
    cancellation = TurnCancellationRequested(TURN_ID)
    call = {"id": "raced", "toolName": "read_file", "input": {}}
    post_tools: list[str] = []
    store = create_app_store()

    def record_hook(event, **kwargs) -> None:
        if event is HookEvent.POST_TOOL_USE:
            post_tools.append(str(kwargs["tool_name"]))

    monkeypatch.setattr(agent_loop_module, "fire_hook_sync", record_hook)
    decision = agent_loop_module._reduce_concurrent_batch_shutdown(
        future_to_call={future: call},
        observed_results={},
        observed_errors={},
        cancellation=cancellation,
    )
    finalize_fatal = (
        agent_loop_module._finalize_completed_concurrent_dispositions(
            dispositions=decision.dispositions,
            store=store,
            task_ledger=None,
            recovery_guard=agent_loop_module.RecoveryGuard(),
            step=1,
        )
    )

    assert decision.control_error is cancellation
    assert decision.dispositions[0].status == "failed"
    assert decision.dispositions[0].completed_result is True
    assert "completed during cancellation" in decision.dispositions[0].result.output
    assert finalize_fatal is None
    assert post_tools == ["read_file"]
    assert store.get_state().tool_call_count == 1


def test_concurrent_cancel_distinguishes_queued_from_running_future() -> None:
    queued: concurrent.futures.Future[ToolResult] = concurrent.futures.Future()
    running: concurrent.futures.Future[ToolResult] = concurrent.futures.Future()
    assert running.set_running_or_notify_cancel() is True
    cancellation = TurnCancellationRequested(TURN_ID)

    decision = agent_loop_module._reduce_concurrent_batch_shutdown(
        future_to_call={
            queued: {"id": "queued", "toolName": "read_file", "input": {}},
            running: {"id": "running", "toolName": "list_files", "input": {}},
        },
        observed_results={},
        observed_errors={},
        cancellation=cancellation,
    )

    assert [item.status for item in decision.dispositions] == [
        "cancelled_before_start",
        "abandoned_running",
    ]
    assert [
        item.status == "abandoned_running" for item in decision.dispositions
    ] == [False, True]


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
