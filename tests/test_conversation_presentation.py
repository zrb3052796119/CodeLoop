from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from minicode.anthropic_adapter import AnthropicModelAdapter
from minicode.agent_runtime import AgentTurnRuntime
from minicode.conversation import ConversationTurnService
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.run_journal import RunJournal
from minicode.session import load_session
from minicode.tooling import ToolRegistry
from minicode.types import AgentStep
from minicode.web.chat_stream import ChatStreamWriter


class DeltaModel:
    def next(self, _messages, on_stream_chunk=None):
        assert on_stream_chunk is not None
        on_stream_chunk("真实")
        on_stream_chunk(" delta")
        return AgentStep(type="assistant", content="真实 delta")


class RecordingPresentation:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def assistant_delta(self, text: str) -> None:
        self.events.append(("delta", text))

    def tool_started(self, tool_name: str) -> None:
        self.events.append(("started", tool_name))

    def tool_finished(self, tool_name: str, *, is_error: bool) -> None:
        self.events.append(("finished", tool_name, is_error))


class Observation:
    def emit(self, *_args, **_kwargs) -> None:
        pass

    def tool_started(self, _tool_name: str) -> None:
        pass

    def tool_finished(self, _tool_name: str, *, is_error: bool) -> None:
        pass


class NoneOnlyModel:
    def __init__(self) -> None:
        self.calls = 0

    def next(self, _messages, on_stream_chunk=None):
        self.calls += 1
        assert on_stream_chunk is None
        return AgentStep(type="assistant", content="no extra observation")


class ExplodingPresentation:
    def assistant_delta(self, _text: str) -> None:
        raise SystemExit("presentation only")

    def tool_started(self, _tool_name: str) -> None:
        raise KeyboardInterrupt("presentation only")

    def tool_finished(self, _tool_name: str, *, is_error: bool) -> None:
        raise BaseException("presentation only")


class StreamingResponse:
    status = 200

    def __init__(self, events: list[dict[str, object] | str]) -> None:
        self._events = events

    def __iter__(self):
        for event in self._events:
            payload = event if isinstance(event, str) else json.dumps(event)
            yield f"data: {payload}\n".encode()


def test_agent_runtime_forwards_only_real_provider_deltas_to_presentation() -> None:
    presentation = RecordingPresentation()
    runtime = AgentTurnRuntime(
        workspace=Path("."),
        runtime={},
        tools=ToolRegistry([]),
        permissions=None,
        memory_manager=None,
        model=DeltaModel(),
        skill_routing=None,
        system_prompt="system",
    )

    result = runtime.execute(
        [{"role": "system", "content": "system"}],
        Observation(),
        presentation=presentation,
    )

    assert result[-1] == {"role": "assistant", "content": "真实 delta"}
    assert presentation.events == [("delta", "真实"), ("delta", " delta")]


def test_agent_runtime_sink_none_does_not_enable_provider_streaming() -> None:
    model = NoneOnlyModel()
    runtime = AgentTurnRuntime(
        workspace=Path("."),
        runtime={},
        tools=ToolRegistry([]),
        permissions=None,
        memory_manager=None,
        model=model,
        skill_routing=None,
        system_prompt="system",
    )

    result = runtime.execute(
        [{"role": "system", "content": "system"}],
        Observation(),
    )

    assert model.calls == 1
    assert result[-1] == {
        "role": "assistant",
        "content": "no extra observation",
    }


def test_presentation_baseexceptions_cannot_change_agent_result() -> None:
    runtime = AgentTurnRuntime(
        workspace=Path("."),
        runtime={},
        tools=ToolRegistry([]),
        permissions=None,
        memory_manager=None,
        model=DeltaModel(),
        skill_routing=None,
        system_prompt="system",
    )

    result = runtime.execute(
        [{"role": "system", "content": "system"}],
        Observation(),
        presentation=ExplodingPresentation(),
    )

    assert result[-1] == {"role": "assistant", "content": "真实 delta"}


def test_openai_adapter_forwards_only_real_text_delta(monkeypatch) -> None:
    response = StreamingResponse(
        [
            {"choices": [{"delta": {"content": "Open"}, "finish_reason": None}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "provider-call-secret",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{\"path\":\"/private/secret\"}",
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {"content": "AI"}, "finish_reason": "tool_calls"}]},
            "[DONE]",
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout=120: response)
    chunks: list[str] = []
    adapter = OpenAIModelAdapter(
        {
            "model": "gpt-4o-mini",
            "openaiApiKey": "fixture-key",
            "modelMaxRetries": 0,
        },
        ToolRegistry([]),
    )

    step = adapter.next(
        [{"role": "user", "content": "hello"}],
        on_stream_chunk=chunks.append,
    )

    assert chunks == ["Open", "AI"]
    assert step.content == "OpenAI"
    assert step.calls[0]["input"] == {"path": "/private/secret"}
    assert all("private" not in chunk and "provider-call" not in chunk for chunk in chunks)


def test_anthropic_adapter_keeps_thinking_and_tool_input_out_of_text_delta(
    monkeypatch,
) -> None:
    response = StreamingResponse(
        [
            {
                "type": "content_block_start",
                "content_block": {"type": "thinking"},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "hidden reasoning"},
            },
            {"type": "content_block_stop"},
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "id": "provider-tool-secret",
                    "name": "read_file",
                },
            },
            {
                "type": "content_block_delta",
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": "{\"path\":\"/private/secret\"}",
                },
            },
            {"type": "content_block_stop"},
            {
                "type": "content_block_start",
                "content_block": {"type": "text"},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "visible"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 2},
            },
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout=60: response)
    text_chunks: list[str] = []
    thinking_chunks: list[str] = []
    adapter = AnthropicModelAdapter(
        {
            "model": "claude",
            "baseUrl": "https://api.anthropic.com",
            "authToken": "fixture-token",
            "modelMaxRetries": 0,
        },
        ToolRegistry([]),
    )

    step = adapter.next(
        [{"role": "user", "content": "hello"}],
        on_stream_chunk=text_chunks.append,
        on_thinking_delta=thinking_chunks.append,
    )

    assert text_chunks == ["visible"]
    assert thinking_chunks == ["hidden reasoning"]
    assert step.content == "visible"
    assert step.calls[0]["input"] == {"path": "/private/secret"}
    assert all("reasoning" not in chunk and "private" not in chunk for chunk in text_chunks)


def test_model_switcher_fallback_receives_the_same_real_stream_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PrimaryModel:
        model_id = "primary"

        def next(self, _messages, on_stream_chunk=None, **_kwargs):
            assert on_stream_chunk is not None
            raise ValueError("fixture provider failure")

    class FallbackModel:
        def next(self, _messages, on_stream_chunk=None, **_kwargs):
            assert on_stream_chunk is not None
            on_stream_chunk("fallback delta")
            return AgentStep(type="assistant", content="fallback final")

    class FallbackSwitcher:
        def __init__(self, **_kwargs) -> None:
            self.calls = 0

        def switch_to(self, target_model: str, reason: str):
            self.calls += 1
            assert target_model == ""
            assert "fixture provider failure" in reason
            return SimpleNamespace(
                success=True,
                adapter=FallbackModel(),
                new_model="fallback",
            )

        def get_switch_history(self):
            return []

    monkeypatch.setattr("minicode.model_switcher.ModelSwitcher", FallbackSwitcher)
    presentation = RecordingPresentation()
    runtime = AgentTurnRuntime(
        workspace=Path("."),
        runtime={},
        tools=ToolRegistry([]),
        permissions=None,
        memory_manager=None,
        model=PrimaryModel(),
        skill_routing=None,
        system_prompt="system",
    )

    result = runtime.execute(
        [{"role": "system", "content": "system"}],
        Observation(),
        presentation=presentation,
    )

    assert result[-1] == {"role": "assistant", "content": "fallback final"}
    assert presentation.events == [("delta", "fallback delta")]


def test_conversation_passes_optional_presentation_without_changing_commit(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    presentation = RecordingPresentation()

    class Permissions:
        def begin_turn(self):
            pass

        def end_turn(self):
            pass

        def get_summary(self):
            return []

    class Tools:
        def get_skills(self):
            return []

        def get_mcp_servers(self):
            return []

        def dispose(self):
            pass

    class Runtime:
        system_prompt = "system"
        permissions = Permissions()
        tools = Tools()
        skill_routing = None

        def execute(
            self,
            messages,
            _observation,
            *,
            cancellation_token=None,
            presentation=None,
        ):
            assert cancellation_token is not None
            assert presentation is not None
            presentation.assistant_delta("only connection")
            return [*messages, {"role": "assistant", "content": "committed"}]

        def dispose(self):
            pass

    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: Runtime(),
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    result = service.turn(
        message="hello",
        session_id=None,
        turn_id="turn_" + "9" * 32,
        presentation=presentation,
    )

    assert result.assistant == "committed"
    assert presentation.events == [("delta", "only connection")]


def test_detached_stream_does_not_change_session_commit_or_run_lifecycle(
    tmp_path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr("minicode.session.MINI_CODE_DIR", data_dir)
    monkeypatch.setattr("minicode.session.SESSIONS_DIR", data_dir / "sessions")
    writes = 0
    runtime_calls = 0

    def broken_write(_frame: bytes) -> None:
        nonlocal writes
        writes += 1
        raise BrokenPipeError("browser disconnected")

    stream = ChatStreamWriter("turn_" + "a" * 32, broken_write)
    stream.ready()
    assert stream.detached is True

    class Permissions:
        def begin_turn(self):
            pass

        def end_turn(self):
            pass

        def get_summary(self):
            return []

    class Tools:
        def get_skills(self):
            return []

        def get_mcp_servers(self):
            return []

        def dispose(self):
            pass

    class Runtime:
        system_prompt = "system"
        permissions = Permissions()
        tools = Tools()
        skill_routing = None

        def execute(
            self,
            messages,
            _observation,
            *,
            cancellation_token=None,
            presentation=None,
        ):
            nonlocal runtime_calls
            runtime_calls += 1
            assert cancellation_token is not None
            presentation.assistant_delta("provider text")
            presentation.tool_started("read_file")
            presentation.tool_finished("read_file", is_error=False)
            return [*messages, {"role": "assistant", "content": "committed"}]

        def dispose(self):
            pass

    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: Runtime(),
        journal_factory=lambda resolved: RunJournal(resolved, data_dir=data_dir),
    )

    result = service.turn(
        message="hello",
        session_id=None,
        turn_id="turn_" + "a" * 32,
        presentation=stream,
    )

    session = load_session(result.session_id)
    runs = RunJournal(workspace, data_dir=data_dir).list_runs().items
    assert writes == 1
    assert runtime_calls == 1
    assert result.assistant == "committed"
    assert session is not None
    assert session.messages[-1] == {"role": "assistant", "content": "committed"}
    assert len(runs) == 1
    assert runs[0].status == "completed"
