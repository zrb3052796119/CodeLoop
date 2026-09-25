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


# ---------------------------------------------------------------------------
# Message-level cache breakpoints (growing-conversation caching)
# ---------------------------------------------------------------------------


def _count_markers(body: dict) -> int:
    count = 0
    if isinstance(body.get("system"), list):
        count += sum(
            1
            for block in body["system"]
            if isinstance(block, dict) and "cache_control" in block
        )
    for tool in body.get("tools", []):
        if "cache_control" in tool:
            count += 1
    for message in body.get("messages", []):
        for block in message.get("content", []):
            if isinstance(block, dict) and "cache_control" in block:
                count += 1
    return count


def test_message_marker_lands_on_last_block(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capturing_urlopen(_END_TURN_PAYLOAD, captured),
    )
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _tool_registry(),
    )

    adapter.next(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "one more thing"},
        ]
    )

    body = captured[0]
    last_message = body["messages"][-1]
    assert last_message["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # Only the final block of the final message carries the marker.
    flat_blocks = [
        block
        for message in body["messages"]
        for block in message["content"]
    ]
    marked = [b for b in flat_blocks if "cache_control" in b]
    assert len(marked) == 1
    assert _count_markers(body) == 3  # system + last tool + last message


def test_two_step_growth_keeps_prefix_bytes_stable(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capturing_urlopen(_END_TURN_PAYLOAD, captured),
    )
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _tool_registry(),
    )

    first_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "find the tests"},
        {"role": "assistant", "content": "looking now"},
    ]
    adapter.next(first_messages)

    second_messages = [
        *first_messages,
        {"role": "user", "content": "tool result: found 3 files"},
        {"role": "assistant", "content": "summary ready"},
    ]
    adapter.next(second_messages)

    first_body, second_body = captured
    # Hard prerequisite for a cache hit: the previous request's message
    # prefix is content-identical inside the next request. cache_control is
    # breakpoint metadata, not content — the provider excludes it from the
    # prefix match — so compare with markers stripped.
    def _strip_markers(messages):
        return [
            {
                **message,
                "content": [
                    {
                        key: value
                        for key, value in block.items()
                        if key != "cache_control"
                    }
                    for block in message["content"]
                ],
            }
            for message in messages
        ]

    assert (
        _strip_markers(second_body["messages"][: len(first_body["messages"])])
        == _strip_markers(first_body["messages"])
    )
    # The marker moved forward to the NEW last message.
    marked_first = [
        block
        for message in first_body["messages"]
        for block in message["content"]
        if "cache_control" in block
    ]
    marked_second = [
        block
        for message in second_body["messages"]
        for block in message["content"]
        if "cache_control" in block
    ]
    assert marked_first[0]["text"] == "looking now"
    assert marked_second[0]["text"] == "summary ready"


def test_tool_result_last_block_gets_marker(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _capturing_urlopen(_END_TURN_PAYLOAD, captured),
    )
    adapter = AnthropicModelAdapter(
        {"model": "claude", "baseUrl": "https://api.anthropic.com", "authToken": "x"},
        _tool_registry(),
    )

    adapter.next(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "run it"},
            {
                "role": "assistant_tool_call",
                "toolUseId": "tu-1",
                "toolName": "read_file",
                "input": {"path": "a.py"},
                "content": "",
                "assistantTurnId": "t1",
            },
            {
                "role": "user_tool_result",
                "toolUseId": "tu-1",
                "content": "file body",
                "isError": False,
            },
        ]
    )

    body = captured[0]
    last_block = body["messages"][-1]["content"][-1]
    assert last_block["type"] == "tool_result"
    assert last_block["cache_control"] == {"type": "ephemeral"}
