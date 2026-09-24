import io
import json
import urllib.error

from minicode.anthropic_adapter import AnthropicModelAdapter, _to_anthropic_messages
from minicode.tooling import ToolDefinition, ToolRegistry


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="Read file",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: None,
            )
        ]
    )


def _two_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="Read file",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: None,
            ),
            ToolDefinition(
                name="list_files",
                description="List files",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _input, _context: None,
            ),
        ]
    )


_END_TURN_PAYLOAD = {
    "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "<final>ok</final>"}],
}


def _capturing_urlopen(payload, captured):
    def fake(request, timeout=60):
        captured.append(json.loads(request.data.decode("utf-8")))
        return DummyResponse(payload)

    return fake


def test_prompt_cache_marks_system_and_last_tool(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capturing_urlopen(_END_TURN_PAYLOAD, captured),
    )
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _two_tool_registry(),
    )

    adapter.next(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
    )

    body = captured[0]
    assert body["system"] == [
        {
            "type": "text",
            "text": "sys",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0].get("cache_control") is None


def test_prompt_cache_disabled_by_runtime_flag(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capturing_urlopen(_END_TURN_PAYLOAD, captured),
    )
    adapter = AnthropicModelAdapter(
        {
            "model": "claude",
            "baseUrl": "https://api.anthropic.com",
            "authToken": "x",
            "promptCache": False,
        },
        _tool_registry(),
    )

    adapter.next(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
    )

    body = captured[0]
    assert body["system"] == "sys"
    assert all("cache_control" not in tool for tool in body["tools"])


def test_prompt_cache_env_kill_switch(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capturing_urlopen(_END_TURN_PAYLOAD, captured),
    )
    monkeypatch.setenv("MINI_CODE_PROMPT_CACHE", "0")
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _tool_registry(),
    )

    adapter.next(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
    )

    body = captured[0]
    assert body["system"] == "sys"
    assert all("cache_control" not in tool for tool in body["tools"])


def test_empty_system_keeps_plain_shape(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capturing_urlopen(_END_TURN_PAYLOAD, captured),
    )
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _tool_registry(),
    )

    adapter.next([{"role": "user", "content": "hi"}])

    body = captured[0]
    assert body["system"] in ("", None) or body["system"] == []


def test_cache_control_400_falls_back_to_plain(monkeypatch) -> None:
    captured: list[dict] = []

    def fake(request, timeout=60):
        captured.append(json.loads(request.data.decode("utf-8")))
        if len(captured) == 1:
            error = urllib.error.HTTPError(
                url=request.full_url,
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(
                    json.dumps(
                        {"error": {"message": "cache_control is not supported"}}
                    ).encode("utf-8")
                ),
            )
            raise error
        return DummyResponse(_END_TURN_PAYLOAD)

    monkeypatch.setattr("urllib.request.urlopen", fake)
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _two_tool_registry(),
    )

    step = adapter.next(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
    )

    assert step.type == "assistant"
    assert captured[0]["system"][0].get("cache_control") is not None
    assert all(
        "cache_control" not in json.dumps(part)
        for part in [captured[1]["system"], *captured[1]["tools"]]
    )


def test_cache_usage_fields_flow_to_step(monkeypatch) -> None:
    payload = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "<final>ok</final>"}],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 1200,
            "cache_creation_input_tokens": 300,
        },
    }
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capturing_urlopen(payload, captured),
    )
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _tool_registry(),
    )

    step = adapter.next(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
    )

    assert step.usage is not None
    assert step.usage.cache_read_tokens == 1200
    assert step.usage.cache_creation_tokens == 300
    assert step.usage.input_tokens == 100
    assert step.usage.source == "provider"


def test_anthropic_replay_keeps_multi_tool_turn_atomic() -> None:
    system, converted = _to_anthropic_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "inspect both"},
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-a",
                "toolName": "read_file",
                "input": {"path": "a.py"},
                "content": "I will inspect both files.",
                "assistantTurnId": "turn-shared",
            },
            {
                "role": "tool_result",
                "toolUseId": "call-a",
                "content": "a-result",
                "isError": False,
            },
            {
                "role": "assistant_tool_call",
                "toolUseId": "call-b",
                "toolName": "read_file",
                "input": {"path": "b.py"},
                "assistantTurnId": "turn-shared",
            },
            {
                "role": "tool_result",
                "toolUseId": "call-b",
                "content": "b-result",
                "isError": False,
            },
        ]
    )

    assert system == "system"
    assert converted == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "inspect both"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will inspect both files."},
                {
                    "type": "tool_use",
                    "id": "call-a",
                    "name": "read_file",
                    "input": {"path": "a.py"},
                },
                {
                    "type": "tool_use",
                    "id": "call-b",
                    "name": "read_file",
                    "input": {"path": "b.py"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-a",
                    "content": "a-result",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call-b",
                    "content": "b-result",
                    "is_error": False,
                },
            ],
        },
    ]


def test_anthropic_adapter_parses_tool_use(monkeypatch) -> None:
    payload = {
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "<progress>thinking</progress>"},
            {"type": "tool_use", "id": "tool-1", "name": "read_file", "input": {"path": "README.md"}},
        ],
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=60: DummyResponse(payload))
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _tool_registry(),
    )

    step = adapter.next([{"role": "system", "content": "sys"}, {"role": "user", "content": "read me"}])

    assert step.type == "tool_calls"
    assert step.content == "thinking"
    assert step.contentKind == "progress"
    assert step.calls[0]["toolName"] == "read_file"


def test_anthropic_adapter_parses_final_text(monkeypatch) -> None:
    payload = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "<final>done</final>"}],
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=60: DummyResponse(payload))
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _tool_registry(),
    )

    step = adapter.next([{"role": "system", "content": "sys"}, {"role": "user", "content": "finish"}])

    assert step.type == "assistant"
    assert step.content == "done"
    assert step.kind == "final"


def test_anthropic_adapter_retries_retryable_http_error(monkeypatch) -> None:
    """A 429 followed by a success must be retried, not aborted mid-loop."""
    payload = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "<final>after retry</final>"}],
    }
    attempts: list[int] = []

    def fake_urlopen(request, timeout=60):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                url="https://api.anthropic.com/v1/messages",
                code=429,
                msg="rate limited",
                hdrs={},
                fp=io.BytesIO(b"{}"),
            )
        return DummyResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x", "modelMaxRetries": 2},
        _tool_registry(),
    )

    step = adapter.next([{"role": "system", "content": "sys"}, {"role": "user", "content": "retry"}])

    assert len(attempts) == 2
    assert step.type == "assistant"
    assert step.content == "after retry"
