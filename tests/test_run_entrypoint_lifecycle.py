from __future__ import annotations

import json
import io
import sys
import threading
import urllib.error
import urllib.request
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.headless as headless_module
import minicode.main as main_module
import minicode.tui.input_handler as input_handler_module
from minicode.gateway import MiniCodeGatewayHandler
from minicode.agent_loop import run_agent_turn
from minicode.conversation import ConversationTurnService
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.run_lifecycle import observe_run
from minicode.tooling import ToolRegistry
from minicode.tui.state import ScreenState, TtyAppArgs
from minicode.types import AgentStep


class FakeTools:
    def __init__(self) -> None:
        self.dispose_calls = 0

    def get_skills(self):
        return []

    def get_mcp_servers(self):
        return []

    def dispose(self) -> None:
        self.dispose_calls += 1


class RecordingJournal:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.transitions: list[tuple[str, str, str | None]] = []

    def create_run(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id="run_" + "b" * 32)

    def transition(self, run_id: str, status: str, *, reason: str | None = None):
        self.transitions.append((run_id, status, reason))


class CountingPermissions(PermissionManager):
    def __init__(self, workspace: str) -> None:
        super().__init__(workspace)
        self.begin_calls = 0
        self.end_calls = 0

    def begin_turn(self) -> None:
        self.begin_calls += 1
        super().begin_turn()

    def end_turn(self) -> None:
        self.end_calls += 1
        PermissionManager.begin_turn(self)


class DirtyAutosave:
    def __init__(self) -> None:
        self.mark_calls = 0

    def mark_dirty(self) -> None:
        self.mark_calls += 1


def post_gateway(port: int, payload: bytes) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/run",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def post_gateway_declared_length(port: int, length: int | str) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest("POST", "/run")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(length))
        connection.endheaders()
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def patch_headless_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_result: object = "ok",
    emit_tool_trace: bool = False,
    emit_model_trace: bool = False,
    routing_result: object | None = None,
) -> FakeTools:
    tools = FakeTools()
    logger = SimpleNamespace(info=lambda *_args, **_kwargs: None, error=lambda *_args, **_kwargs: None)
    permission = SimpleNamespace(
        get_summary=lambda: [],
        begin_turn=lambda: None,
        end_turn=lambda: None,
    )
    routing = routing_result or SimpleNamespace(
        selected_skill_dicts=lambda: [],
        to_dict=lambda: {},
    )

    monkeypatch.setattr("minicode.logging_config.setup_logging", lambda **_kwargs: None)
    monkeypatch.setattr("minicode.logging_config.get_logger", lambda _name: logger)
    monkeypatch.setattr("minicode.config.load_runtime_config", lambda _cwd: {"model": "fake"})
    monkeypatch.setattr("minicode.tools.create_default_tool_registry", lambda *_args, **_kwargs: tools)
    monkeypatch.setattr("minicode.permissions.PermissionManager", lambda *_args, **_kwargs: permission)
    monkeypatch.setattr("minicode.memory.MemoryManager", lambda **_kwargs: object())
    monkeypatch.setattr("minicode.model_registry.create_model_adapter", lambda **_kwargs: object())
    monkeypatch.setattr("minicode.prompt.build_system_prompt", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr("minicode.capability_registry.get_registry", lambda: object())
    monkeypatch.setattr("minicode.capability_registry.register_tool_capabilities", lambda _tools: None)
    monkeypatch.setattr("minicode.intent_parser.parse_intent", lambda _prompt: object())
    monkeypatch.setattr(
        "minicode.skill_router.SkillRouter",
        lambda: SimpleNamespace(route=lambda *_args, **_kwargs: routing),
    )

    def fake_agent_turn(**kwargs):
        if isinstance(agent_result, BaseException):
            raise agent_result
        if emit_model_trace:
            operation_id = "modelop_" + "a" * 32
            kwargs["event_sink"].emit(
                "model.started",
                step=1,
                payload={"operationId": operation_id},
            )
            kwargs["event_sink"].emit(
                "model.completed",
                step=1,
                payload={
                    "operationId": operation_id,
                    "resultType": "assistant",
                    "contentPresent": True,
                    "toolCallCount": 0,
                },
            )
        if emit_tool_trace:
            kwargs["on_tool_start"](
                "read_file", {"path": "/private/password=tool-input-secret"}
            )
            kwargs["on_tool_result"](
                "read_file", "Bearer tool-output-secret", False
            )
        return [*kwargs["messages"], {"role": "assistant", "content": agent_result}]

    monkeypatch.setattr("minicode.agent_loop.run_agent_turn", fake_agent_turn)
    return tools


def test_headless_forwards_optional_current_state_registry_only_when_supplied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = patch_headless_runtime(monkeypatch, agent_result="ok")
    calls: list[dict[str, object]] = []

    def create_tools(*_args, **kwargs):
        calls.append(kwargs)
        return tools

    monkeypatch.setattr("minicode.tools.create_default_tool_registry", create_tools)
    monkeypatch.chdir(tmp_path)
    state_registry = object()

    assert headless_module.run_headless(
        "observed",
        run_observation_enabled=False,
        mcp_current_state_registry=state_registry,
    ) == "ok"
    assert headless_module.run_headless(
        "standalone",
        run_observation_enabled=False,
    ) == "ok"

    assert calls[0]["mcp_current_state_registry"] is state_registry
    assert "mcp_current_state_registry" not in calls[1]


def test_real_agent_working_memory_event_precedes_assistant_and_terminal(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    class Model:
        def next(self, _messages, on_stream_chunk=None):
            return AgentStep(type="assistant", content="final body secret")

    with observe_run(
        workspace=workspace,
        source="headless",
        title="Context observation",
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ) as observation:
        messages = run_agent_turn(
            model=Model(),
            tools=ToolRegistry([]),
            messages=[{"role": "user", "content": "go"}],
            cwd=str(workspace),
            enable_work_chain=False,
            event_sink=observation,
        )
        observation.assistant_completed(
            content_present=True,
            content_length=len(messages[-1]["content"]),
        )

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    events = journal.list_events(record.id).items

    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "model.started",
        "model.completed",
        "model.costed",
        "working_memory.observed",
        "task.outcome",
        "assistant.completed",
        "run.completed",
    ]
    assert events[5].payload["scope"] == "process"
    assert "final body secret" not in str([event.to_dict() for event in events])


def test_direct_headless_creates_one_completed_headless_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    tools = patch_headless_runtime(
        monkeypatch,
        agent_result="same response password=assistant-secret",
        emit_tool_trace=True,
        emit_model_trace=True,
        routing_result=SimpleNamespace(
            intent_type="code",
            action_type="read",
            total_skills=1,
            selected=[
                SimpleNamespace(
                    qualified_name="project/safe-skill",
                    name="safe-skill",
                    source="project",
                    directory="project",
                    score=4.5,
                )
            ],
            selected_skills=[],
            used_fallback=False,
            selected_skill_dicts=lambda: [],
            to_dict=lambda: {},
        ),
    )
    monkeypatch.chdir(workspace)

    response = headless_module.run_headless(
        "Inspect the workspace",
        run_journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    page = RunJournal(workspace, data_dir=data_dir).list_runs()
    assert response == "same response password=assistant-secret"
    assert tools.dispose_calls == 1
    assert len(page.items) == 1
    assert page.items[0].source == "headless"
    assert page.items[0].session_id is None
    assert page.items[0].status == "completed"
    events = RunJournal(workspace, data_dir=data_dir).list_events(
        page.items[0].id
    ).items
    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "skill.routed",
        "model.started",
        "model.completed",
        "tool.started",
        "tool.finished",
        "assistant.completed",
        "run.completed",
    ]
    assert events[2].payload["selectedCount"] == 1
    assert events[2].payload["selected"][0]["qualifiedName"] == "project/safe-skill"
    assert events[3].step == events[4].step == 1
    assert events[3].payload["operationId"] == events[4].payload["operationId"]
    assert events[5].payload["toolName"] == "read_file"
    assert events[6].payload["outcome"] == "success"
    assert events[6].payload["operationId"] == events[5].payload["operationId"]
    assert events[7].payload == {
        "contentPresent": True,
        "contentLength": len("same response password=assistant-secret"),
        "kind": "returned_assistant",
    }
    serialized = str([event.to_dict() for event in events])
    for forbidden in (
        "tool-input-secret",
        "tool-output-secret",
        "assistant-secret",
    ):
        assert forbidden not in serialized


def test_headless_normal_return_without_assistant_records_explicit_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    patch_headless_runtime(monkeypatch)
    monkeypatch.setattr(
        "minicode.agent_loop.run_agent_turn",
        lambda **kwargs: list(kwargs["messages"]),
    )
    monkeypatch.chdir(workspace)

    response = headless_module.run_headless(
        "No assistant",
        run_journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    events = journal.list_events(record.id).items
    assert response == "(no response)"
    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "assistant.completed",
        "run.completed",
    ]
    assert events[2].payload == {
        "contentPresent": False,
        "contentLength": 0,
        "kind": "returned_assistant",
    }


def test_headless_append_event_failure_does_not_change_response_or_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    patch_headless_runtime(
        monkeypatch,
        agent_result="same response",
        emit_tool_trace=True,
    )
    journal = RecordingJournal()
    monkeypatch.chdir(workspace)

    response = headless_module.run_headless(
        "Append fails",
        run_journal_factory=lambda _resolved: journal,
    )

    assert response == "same response"
    assert journal.transitions == [
        ("run_" + "b" * 32, "running", None),
        ("run_" + "b" * 32, "completed", None),
    ]


def test_headless_execution_exception_returns_same_error_and_records_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    error = RuntimeError("password=business-secret")
    tools = patch_headless_runtime(monkeypatch, agent_result=error)
    monkeypatch.chdir(workspace)

    response = headless_module.run_headless(
        "Fail safely",
        run_journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    events = journal.list_events(record.id).items
    assert response == "Error: password=business-secret"
    assert tools.dispose_calls == 1
    assert record.status == "failed"
    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "run.failed",
    ]
    assert events[-1].payload == {"summary": "execution_failed"}
    assert "business-secret" not in str([event.to_dict() for event in events])


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(9)])
def test_headless_interrupt_preserves_exception_and_records_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt: BaseException,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    tools = patch_headless_runtime(monkeypatch, agent_result=interrupt)
    monkeypatch.chdir(workspace)

    with pytest.raises(type(interrupt)) as raised:
        headless_module.run_headless(
            "Interrupt safely",
            run_journal_factory=lambda resolved: RunJournal(
                resolved, data_dir=data_dir
            ),
        )

    record = RunJournal(workspace, data_dir=data_dir).list_runs().items[0]
    assert raised.value is interrupt
    assert tools.dispose_calls == 1
    assert record.status == "interrupted"


def test_empty_headless_prompt_exits_without_creating_a_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    patch_headless_runtime(monkeypatch)
    monkeypatch.chdir(workspace)

    with pytest.raises(SystemExit):
        headless_module.run_headless(
            "   ",
            run_journal_factory=lambda resolved: RunJournal(
                resolved, data_dir=data_dir
            ),
        )

    assert not data_dir.exists()


def test_broken_headless_journal_does_not_change_response_or_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = patch_headless_runtime(monkeypatch, agent_result="same response")
    monkeypatch.chdir(workspace)

    response = headless_module.run_headless(
        "Observe best effort",
        run_journal_factory=lambda _resolved: (_ for _ in ()).throw(
            OSError("Bearer journal-secret")
        ),
    )

    assert response == "same response"
    assert tools.dispose_calls == 1


def test_headless_component_initialization_failure_propagates_and_records_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    tools = patch_headless_runtime(monkeypatch)
    initialization_error = RuntimeError("api_key=initialization-secret")
    monkeypatch.setattr(
        "minicode.model_registry.create_model_adapter",
        lambda **_kwargs: (_ for _ in ()).throw(initialization_error),
    )
    monkeypatch.chdir(workspace)

    with pytest.raises(RuntimeError) as raised:
        headless_module.run_headless(
            "Initialize task",
            run_journal_factory=lambda resolved: RunJournal(
                resolved, data_dir=data_dir
            ),
        )

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    assert raised.value is initialization_error
    assert record.status == "failed"
    assert tools.dispose_calls == 1
    assert "initialization-secret" not in str(
        [event.to_dict() for event in journal.list_events(record.id).items]
    )


def test_gateway_real_composition_creates_exactly_one_gateway_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    patch_headless_runtime(
        monkeypatch,
        agent_result="gateway response",
        emit_tool_trace=True,
        emit_model_trace=True,
        routing_result=SimpleNamespace(
            intent_type="code",
            action_type="read",
            total_skills=0,
            selected=[],
            selected_skills=[],
            used_fallback=True,
            selected_skill_dicts=lambda: [],
            to_dict=lambda: {},
        ),
    )
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "minicode.run_lifecycle._default_journal_factory",
        lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = post_gateway(
            server.server_address[1], json.dumps({"prompt": "Gateway task"}).encode()
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    page = RunJournal(workspace, data_dir=data_dir).list_runs(limit=100)
    assert (status, payload) == (
        200,
        {"ok": True, "response": "gateway response"},
    )
    assert len(page.items) == 1
    assert page.items[0].source == "gateway"
    assert page.items[0].status == "completed"
    assert not any(record.source == "headless" for record in page.items)
    events = RunJournal(workspace, data_dir=data_dir).list_events(
        page.items[0].id
    ).items
    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "skill.routed",
        "model.started",
        "model.completed",
        "tool.started",
        "tool.finished",
        "assistant.completed",
        "run.completed",
    ]


def test_dashboard_chat_real_composition_creates_one_session_linked_gateway_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    patch_headless_runtime(
        monkeypatch,
        agent_result="chat response",
        emit_tool_trace=True,
        emit_model_trace=True,
        routing_result=SimpleNamespace(
            intent_type="code",
            action_type="read",
            total_skills=0,
            selected=[],
            selected_skills=[],
            used_fallback=True,
            selected_skill_dicts=lambda: [],
            to_dict=lambda: {},
        ),
    )
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")

    result = ConversationTurnService(
        workspace,
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    ).turn(message="Dashboard Chat task", session_id=None)

    runs = RunJournal(workspace, data_dir=data_dir).list_runs(limit=100).items
    assert len(runs) == 1
    assert result.run_id == runs[0].id
    assert runs[0].source == "gateway"
    assert runs[0].session_id == result.session_id
    assert runs[0].status == "completed"
    assert [
        event.type
        for event in RunJournal(workspace, data_dir=data_dir).list_events(
            runs[0].id
        ).items
    ] == [
        "run.queued",
        "run.started",
        "skill.routed",
        "model.started",
        "model.completed",
        "tool.started",
        "tool.finished",
        "assistant.completed",
        "run.completed",
    ]


@pytest.mark.parametrize(
    ("agent_result", "http_status", "run_status"),
    [
        (RuntimeError("execution failed"), 200, "failed"),
        (SystemExit("execution interrupted"), 500, "interrupted"),
    ],
)
def test_gateway_real_composition_preserves_response_semantics_and_terminal_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_result: BaseException,
    http_status: int,
    run_status: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    patch_headless_runtime(monkeypatch, agent_result=agent_result)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "minicode.run_lifecycle._default_journal_factory",
        lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = post_gateway(
            server.server_address[1], json.dumps({"prompt": "Gateway terminal"}).encode()
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    record = RunJournal(workspace, data_dir=data_dir).list_runs().items[0]
    assert status == http_status
    assert record.source == "gateway"
    assert record.status == run_status
    if run_status == "failed":
        assert payload == {"ok": True, "response": "Error: execution failed"}
    else:
        assert payload["ok"] is False
        assert "execution interrupted" in str(payload["error"])


def test_broken_gateway_journal_does_not_change_success_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = patch_headless_runtime(monkeypatch, agent_result="gateway response")
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "minicode.run_lifecycle._default_journal_factory",
        lambda _resolved: (_ for _ in ()).throw(
            OSError("password=journal-secret")
        ),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = post_gateway(
            server.server_address[1], json.dumps({"prompt": "Gateway task"}).encode()
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert (status, payload) == (
        200,
        {"ok": True, "response": "gateway response"},
    )
    assert tools.dispose_calls == 1


def test_invalid_gateway_requests_and_read_routes_do_not_create_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    patch_headless_runtime(monkeypatch)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        "minicode.run_lifecycle._default_journal_factory",
        lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        empty_status, _ = post_gateway(
            server.server_address[1], json.dumps({"prompt": "   "}).encode()
        )
        invalid_status, _ = post_gateway(server.server_address[1], b"{not-json")
        invalid_length_status = post_gateway_declared_length(
            server.server_address[1], "invalid"
        )
        oversized_status = post_gateway_declared_length(
            server.server_address[1], 1_048_577
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/health", timeout=5
        ) as response:
            assert response.status == 200
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/api/v1/runs", timeout=5
        ) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert empty_status == 400
    assert invalid_status == 500
    assert invalid_length_status == 400
    assert oversized_status == 413
    assert RunJournal(workspace, data_dir=data_dir).list_runs().items == ()


def test_event_driven_tui_turn_uses_real_session_id_and_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"

    callback_calls: list[tuple[object, ...]] = []

    def fake_agent_turn(**kwargs):
        operation_id = "modelop_" + "b" * 32
        kwargs["event_sink"].emit(
            "model.started", step=1, payload={"operationId": operation_id}
        )
        kwargs["event_sink"].emit(
            "model.completed",
            step=1,
            payload={
                "operationId": operation_id,
                "resultType": "tool_calls",
                "contentPresent": False,
                "toolCallCount": 1,
            },
        )
        kwargs["on_tool_start"]("read_file", {"path": "visible-to-ui-only"})
        callback_calls.append(("start", "read_file", {"path": "visible-to-ui-only"}))
        kwargs["on_tool_result"]("read_file", "visible UI result", False)
        callback_calls.append(("result", "read_file", "visible UI result", False))
        return [*kwargs["messages"], {"role": "assistant", "content": "done"}]

    monkeypatch.setattr(input_handler_module, "run_agent_turn", fake_agent_turn)
    state = ScreenState(
        input="Inspect the project",
        session=SimpleNamespace(session_id="session_01"),
    )
    args = TtyAppArgs(
        runtime={"model": "fake"},
        tools=ToolRegistry([]),
        model=object(),
        messages=[{"role": "system", "content": "system"}],
        cwd=str(workspace),
        permissions=PermissionManager(str(workspace)),
        run_journal_factory=lambda resolved: RunJournal(
            resolved, data_dir=data_dir
        ),
    )

    assert input_handler_module._handle_input(args, state, lambda: None) is False
    state.agent_thread.join(timeout=5)

    page = RunJournal(workspace, data_dir=data_dir).list_runs()
    assert state.agent_result["messages"][-1] == {
        "role": "assistant",
        "content": "done",
    }
    assert state.is_busy is False
    assert len(page.items) == 1
    assert page.items[0].source == "tui"
    assert page.items[0].session_id == "session_01"
    assert page.items[0].status == "completed"
    assert callback_calls == [
        ("start", "read_file", {"path": "visible-to-ui-only"}),
        ("result", "read_file", "visible UI result", False),
    ]
    events = RunJournal(workspace, data_dir=data_dir).list_events(
        page.items[0].id
    ).items
    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "skill.routed",
        "model.started",
        "model.completed",
        "tool.started",
        "tool.finished",
        "assistant.completed",
        "run.completed",
    ]
    assert "visible-to-ui-only" not in str([event.to_dict() for event in events])
    assert "visible UI result" not in str([event.to_dict() for event in events])


def test_event_driven_tui_exception_records_failed_and_restores_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    permissions = CountingPermissions(str(workspace))
    autosave = DirtyAutosave()
    error = RuntimeError("Authorization=business-secret")

    def fail_agent_turn(**_kwargs):
        raise error

    monkeypatch.setattr(input_handler_module, "run_agent_turn", fail_agent_turn)
    state = ScreenState(
        input="Fail in TUI",
        session=SimpleNamespace(session_id="session_02"),
        autosave=autosave,  # type: ignore[arg-type]
    )
    args = TtyAppArgs(
        runtime={"model": "fake"},
        tools=ToolRegistry([]),
        model=object(),
        messages=[{"role": "system", "content": "system"}],
        cwd=str(workspace),
        permissions=permissions,
        run_journal_factory=lambda resolved: RunJournal(
            resolved, data_dir=data_dir
        ),
    )

    input_handler_module._handle_input(args, state, lambda: None)
    state.agent_thread.join(timeout=5)

    journal = RunJournal(workspace, data_dir=data_dir)
    record = journal.list_runs().items[0]
    assert record.status == "failed"
    assert permissions.begin_calls == 1
    assert permissions.end_calls == 1
    assert autosave.mark_calls == 1
    assert state.is_busy is False
    assert state.active_tool is None
    assert state.status is None
    assert "business-secret" not in str(
        [event.to_dict() for event in journal.list_events(record.id).items]
    )


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(5)])
def test_event_driven_tui_interrupt_records_interrupted_and_preserves_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt: BaseException,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    permissions = CountingPermissions(str(workspace))
    thread_errors: list[BaseException] = []

    def interrupt_agent_turn(**_kwargs):
        raise interrupt

    monkeypatch.setattr(input_handler_module, "run_agent_turn", interrupt_agent_turn)
    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: thread_errors.append(args.exc_value),
    )
    state = ScreenState(
        input="Interrupt in TUI",
        session=SimpleNamespace(session_id="session_03"),
    )
    args = TtyAppArgs(
        runtime={"model": "fake"},
        tools=ToolRegistry([]),
        model=object(),
        messages=[{"role": "system", "content": "system"}],
        cwd=str(workspace),
        permissions=permissions,
        run_journal_factory=lambda resolved: RunJournal(
            resolved, data_dir=data_dir
        ),
    )

    input_handler_module._handle_input(args, state, lambda: None)
    state.agent_thread.join(timeout=5)

    record = RunJournal(workspace, data_dir=data_dir).list_runs().items[0]
    assert thread_errors == [interrupt]
    assert record.status == "interrupted"
    assert permissions.end_calls == 1
    assert state.is_busy is False


def test_tui_local_command_does_not_create_a_run(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    state = ScreenState(input="/tools")
    args = TtyAppArgs(
        runtime=None,
        tools=ToolRegistry([]),
        model=object(),
        messages=[{"role": "system", "content": "system"}],
        cwd=str(workspace),
        permissions=PermissionManager(str(workspace)),
        run_journal_factory=lambda resolved: RunJournal(
            resolved, data_dir=data_dir
        ),
    )

    assert input_handler_module._handle_input(args, state, lambda: None) is False
    assert RunJournal(workspace, data_dir=data_dir).list_runs().items == ()


def test_tui_business_state_is_equivalent_for_healthy_broken_and_disabled_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_agent_turn(**kwargs):
        return [*kwargs["messages"], {"role": "assistant", "content": "same"}]

    monkeypatch.setattr(input_handler_module, "run_agent_turn", fake_agent_turn)

    def run_case(name: str, factory, enabled: bool):
        workspace = tmp_path / name
        workspace.mkdir()
        permissions = CountingPermissions(str(workspace))
        autosave = DirtyAutosave()
        state = ScreenState(
            input="Equivalent task",
            session=SimpleNamespace(session_id="session_same"),
            autosave=autosave,  # type: ignore[arg-type]
        )
        args = TtyAppArgs(
            runtime={"model": "fake"},
            tools=ToolRegistry([]),
            model=object(),
            messages=[{"role": "system", "content": "system"}],
            cwd=str(workspace),
            permissions=permissions,
            run_journal_factory=factory,
            run_observation_enabled=enabled,
        )
        input_handler_module._handle_input(args, state, lambda: None)
        state.agent_thread.join(timeout=5)
        return {
            "messages": [
                message
                for message in state.agent_result["messages"]
                if message["role"] != "system"
            ],
            "transcript": [
                (entry.kind, entry.body, entry.status) for entry in state.transcript
            ],
            "busy": state.is_busy,
            "status": state.status,
            "permissions": (permissions.begin_calls, permissions.end_calls),
            "autosave": autosave.mark_calls,
        }

    healthy_journal = RecordingJournal()
    healthy = run_case("healthy", lambda _workspace: healthy_journal, True)
    broken = run_case(
        "broken",
        lambda _workspace: (_ for _ in ()).throw(
            OSError("Bearer journal-secret")
        ),
        True,
    )
    disabled = run_case(
        "disabled",
        lambda _workspace: (_ for _ in ()).throw(AssertionError("not called")),
        False,
    )

    assert healthy == broken == disabled
    assert len(healthy_journal.created) == 1
    assert healthy_journal.transitions[-1][1] == "completed"


def test_classic_non_tty_agent_turn_creates_one_tui_run_without_fake_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    permissions = CountingPermissions(str(workspace))
    tools = FakeTools()
    tools.list = lambda: []  # type: ignore[attr-defined]
    memory = SimpleNamespace(handle_user_memory_input=lambda _value: None)
    profile = SimpleNamespace(
        load_merged=lambda: None,
        global_path=SimpleNamespace(exists=lambda: False),
        project_path=SimpleNamespace(exists=lambda: False),
    )
    logger = SimpleNamespace(
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        debug=lambda *_args, **_kwargs: None,
    )

    class NonTtyInput(io.StringIO):
        def isatty(self) -> bool:
            return False

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(sys, "argv", ["minicode"])
    monkeypatch.setattr(sys, "stdin", NonTtyInput("Classic task\n"))
    monkeypatch.setattr(main_module, "maybe_handle_management_command", lambda *_args: False)
    monkeypatch.setattr(main_module, "load_runtime_config", lambda _cwd: {})
    monkeypatch.setattr(main_module, "create_default_tool_registry", lambda *_args, **_kwargs: tools)
    monkeypatch.setattr(main_module, "PermissionManager", lambda *_args, **_kwargs: permissions)
    monkeypatch.setattr(main_module, "create_model_adapter", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "build_system_prompt", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr(main_module, "load_history_entries", lambda: [])
    monkeypatch.setattr(main_module, "save_history_entries", lambda _history: None)
    skill_routing = SimpleNamespace(
        intent_type="code",
        action_type="read",
        total_skills=0,
        selected=[],
        selected_skills=[],
        used_fallback=True,
    )
    monkeypatch.setattr(
        main_module,
        "_route_skills_for_prompt",
        lambda *_args: ([], skill_routing),
    )
    def classic_agent_turn(**kwargs):
        operation_id = "modelop_" + "c" * 32
        kwargs["event_sink"].emit(
            "model.started", step=1, payload={"operationId": operation_id}
        )
        kwargs["event_sink"].emit(
            "model.completed",
            step=1,
            payload={
                "operationId": operation_id,
                "resultType": "tool_calls",
                "contentPresent": False,
                "toolCallCount": 1,
            },
        )
        kwargs["on_tool_start"]("read_file", {"path": "classic-input-secret"})
        kwargs["on_tool_result"]("read_file", "classic-output-secret", False)
        return [
            *kwargs["messages"],
            {"role": "assistant", "content": "classic done"},
        ]

    monkeypatch.setattr(main_module, "run_agent_turn", classic_agent_turn)
    monkeypatch.setattr("minicode.logging_config.setup_logging", lambda **_kwargs: None)
    monkeypatch.setattr("minicode.logging_config.get_logger", lambda _name: logger)
    monkeypatch.setattr("minicode.memory.MemoryManager", lambda **_kwargs: memory)
    monkeypatch.setattr("minicode.user_profile.UserProfileManager", lambda **_kwargs: profile)
    monkeypatch.setattr(
        "minicode.state.create_app_store",
        lambda **_kwargs: SimpleNamespace(
            get_state=lambda: SimpleNamespace(session_id="new")
        ),
    )
    monkeypatch.setattr(
        "minicode.run_lifecycle._default_journal_factory",
        lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    main_module.main()

    page = RunJournal(workspace, data_dir=data_dir).list_runs()
    assert len(page.items) == 1
    assert page.items[0].source == "tui"
    assert page.items[0].session_id is None
    assert page.items[0].status == "completed"
    events = RunJournal(workspace, data_dir=data_dir).list_events(
        page.items[0].id
    ).items
    assert [event.type for event in events] == [
        "run.queued",
        "run.started",
        "skill.routed",
        "model.started",
        "model.completed",
        "tool.started",
        "tool.finished",
        "assistant.completed",
        "run.completed",
    ]
    serialized = str([event.to_dict() for event in events])
    assert "classic-input-secret" not in serialized
    assert "classic-output-secret" not in serialized
    assert permissions.begin_calls == 1
    assert permissions.end_calls == 1
    assert tools.dispose_calls == 1
