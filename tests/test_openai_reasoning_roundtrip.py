from __future__ import annotations

import json
from typing import Any

from minicode.agent_loop import run_agent_turn
from minicode.openai_adapter import OpenAIModelAdapter, _to_openai_messages
from minicode.tooling import ToolDefinition, ToolRegistry, ToolResult


class _JsonResponse:
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _StreamResponse:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def __iter__(self):
        for event in self._events:
            yield f"data: {json.dumps(event)}\n".encode("utf-8")
        yield b"data: [DONE]\n"


def test_reasoning_content_survives_tool_call_history_round_trip(
    monkeypatch,
    tmp_path,
) -> None:
    """Thinking-mode tool calls must send their reasoning back next request."""
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "Inspect the synthetic config first.",
                            "tool_calls": [
                                {
                                    "id": "call_inspect",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_config",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "Verify the synthetic config.",
                            "tool_calls": [
                                {
                                    "id": "call_verify",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_config",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 1},
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 1},
            },
        ]
    )

    def fake_open(request, *, timeout):
        del timeout
        requests.append(json.loads(request.data.decode("utf-8")))
        return _JsonResponse(next(responses))

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)

    tools = ToolRegistry(
        [
            ToolDefinition(
                name="inspect_config",
                description="Inspect a synthetic configuration.",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(
                    ok=True,
                    output="synthetic config",
                ),
            )
        ]
    )
    model = OpenAIModelAdapter(
        {
            "model": "deepseek-chat",
            "openaiBaseUrl": "https://api.deepseek.synthetic/v1",
            "openaiApiKey": "synthetic-key",
            "modelMaxRetries": 0,
        },
        tools,
    )

    result = run_agent_turn(
        model=model,
        tools=tools,
        messages=[
            {"role": "system", "content": "Use tools when needed."},
            {"role": "user", "content": "Inspect the config."},
        ],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=3,
    )

    assert result[-1] == {"role": "assistant", "content": "done"}
    assert len(requests) == 3
    assistant_history = next(
        message
        for message in requests[1]["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert assistant_history["reasoning_content"] == (
        "Inspect the synthetic config first."
    )
    all_later_reasoning = [
        message["reasoning_content"]
        for message in requests[2]["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert all_later_reasoning == [
        "Inspect the synthetic config first.",
        "Verify the synthetic config.",
    ]


def test_tool_call_text_reasoning_and_calls_replay_as_one_assistant_turn(
    monkeypatch,
    tmp_path,
) -> None:
    """DeepSeek requires all fields from one thinking tool turn together."""
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "I will inspect the synthetic config.",
                            "reasoning_content": "Inspect before answering.",
                            "tool_calls": [
                                {
                                    "id": "call_inspect",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_config",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "done"},
                    }
                ]
            },
        ]
    )

    def fake_open(request, *, timeout):
        del timeout
        requests.append(json.loads(request.data.decode("utf-8")))
        return _JsonResponse(next(responses))

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="inspect_config",
                description="Inspect a synthetic configuration.",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(ok=True, output="config"),
            )
        ]
    )
    model = OpenAIModelAdapter(
        {
            "model": "deepseek-v4-pro",
            "openaiBaseUrl": "https://api.deepseek.com",
            "openaiApiKey": "synthetic-key",
            "modelMaxRetries": 0,
        },
        tools,
    )

    run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "user", "content": "Inspect the config."}],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=3,
    )

    replayed_assistants = [
        message
        for message in requests[1]["messages"]
        if message.get("role") == "assistant"
    ]
    assert replayed_assistants == [
        {
            "role": "assistant",
            "content": "I will inspect the synthetic config.",
            "reasoning_content": "Inspect before answering.",
            "tool_calls": [
                {
                    "id": "call_inspect",
                    "type": "function",
                    "function": {
                        "name": "inspect_config",
                        "arguments": "{}",
                    },
                }
            ],
        }
    ]


def test_deepseek_endpoint_backfills_missing_tool_turn_reasoning(
    monkeypatch,
    tmp_path,
) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "<progress>I will inspect it.</progress>",
                            "tool_calls": [
                                {
                                    "id": "call_inspect",
                                    "type": "function",
                                    "function": {
                                        "name": "inspect_config",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "done"},
                    }
                ]
            },
        ]
    )

    def fake_open(request, *, timeout):
        del timeout
        request_body = json.loads(request.data.decode("utf-8"))
        requests.append(request_body)
        if len(requests) == 2:
            tool_turn = next(
                message
                for message in request_body["messages"]
                if message.get("role") == "assistant" and message.get("tool_calls")
            )
            assert "reasoning_content" in tool_turn
            assert tool_turn["reasoning_content"] == ""
            assert tool_turn["content"] == "I will inspect it."
            assert sum(
                message.get("role") == "assistant"
                for message in request_body["messages"]
            ) == 1
        return _JsonResponse(next(responses))

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="inspect_config",
                description="Inspect a synthetic configuration.",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(ok=True, output="config"),
            )
        ]
    )
    model = OpenAIModelAdapter(
        {
            "model": "deepseek-v4-pro",
            "openaiBaseUrl": "https://api.deepseek.com",
            "openaiApiKey": "synthetic-key",
            "modelMaxRetries": 0,
        },
        tools,
    )

    result = run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "user", "content": "Inspect the config."}],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=3,
    )

    assert result[-1] == {"role": "assistant", "content": "done"}


def test_generic_openai_conversion_does_not_add_deepseek_reasoning_field() -> None:
    _system, converted = _to_openai_messages(
        [
            {
                "role": "assistant_tool_call",
                "toolUseId": "call_inspect",
                "toolName": "inspect_config",
                "input": {},
                "assistantTurnId": "turn-1",
            }
        ]
    )

    assert "reasoning_content" not in converted[0]


def test_streaming_reasoning_content_survives_tool_call_history_round_trip(
    monkeypatch,
    tmp_path,
) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            _StreamResponse(
                [
                    {
                        "choices": [
                            {
                                "delta": {"content": "I will inspect it. "},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {"reasoning_content": "Inspect the "},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {"reasoning_content": "config first."},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_inspect",
                                            "function": {
                                                "name": "inspect_config",
                                                "arguments": "{}",
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                ]
            ),
            _StreamResponse(
                [
                    {
                        "choices": [
                            {
                                "delta": {"content": "done"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ]
            ),
        ]
    )

    def fake_open(request, *, timeout):
        del timeout
        requests.append(json.loads(request.data.decode("utf-8")))
        return next(responses)

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="inspect_config",
                description="Inspect a synthetic configuration.",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(ok=True, output="config"),
            )
        ]
    )
    model = OpenAIModelAdapter(
        {
            "model": "deepseek-chat",
            "openaiBaseUrl": "https://api.deepseek.synthetic/v1",
            "openaiApiKey": "synthetic-key",
            "modelMaxRetries": 0,
        },
        tools,
    )
    thinking_chunks: list[str] = []

    run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "user", "content": "Inspect the config."}],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=3,
        on_assistant_stream_chunk=lambda _chunk: None,
        on_thinking_chunk=thinking_chunks.append,
    )

    assistant_history = next(
        message
        for message in requests[1]["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert assistant_history["reasoning_content"] == "Inspect the config first."
    assert assistant_history["content"] == "I will inspect it."
    assert sum(
        message.get("role") == "assistant"
        for message in requests[1]["messages"]
    ) == 1
    assert thinking_chunks == ["Inspect the ", "config first."]


def test_streaming_empty_reasoning_field_is_still_replayed_for_tool_call(
    monkeypatch,
    tmp_path,
) -> None:
    """An explicit empty field is different from a missing reasoning field."""
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            _StreamResponse(
                [
                    {
                        "choices": [
                            {
                                "delta": {"reasoning_content": ""},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_inspect",
                                            "function": {
                                                "name": "inspect_config",
                                                "arguments": "{}",
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                ]
            ),
            _StreamResponse(
                [
                    {
                        "choices": [
                            {
                                "delta": {"content": "done"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ]
            ),
        ]
    )

    def fake_open(request, *, timeout):
        del timeout
        requests.append(json.loads(request.data.decode("utf-8")))
        return next(responses)

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    tools = ToolRegistry(
        [
            ToolDefinition(
                name="inspect_config",
                description="Inspect a synthetic configuration.",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(ok=True, output="config"),
            )
        ]
    )
    model = OpenAIModelAdapter(
        {
            "model": "deepseek-chat",
            "openaiBaseUrl": "https://api.deepseek.synthetic/v1",
            "openaiApiKey": "synthetic-key",
            "modelMaxRetries": 0,
        },
        tools,
    )

    run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "user", "content": "Inspect the config."}],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=3,
        on_assistant_stream_chunk=lambda _chunk: None,
    )

    assistant_history = next(
        message
        for message in requests[1]["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert "reasoning_content" in assistant_history
    assert assistant_history["reasoning_content"] == ""


def test_multiple_tool_calls_share_one_reasoning_assistant_turn(
    monkeypatch,
    tmp_path,
) -> None:
    requests: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "I will inspect both inputs.",
                            "reasoning_content": "Inspect both synthetic inputs.",
                            "tool_calls": [
                                {
                                    "id": "call_a",
                                    "type": "function",
                                    "function": {"name": "inspect_a", "arguments": "{}"},
                                },
                                {
                                    "id": "call_b",
                                    "type": "function",
                                    "function": {"name": "inspect_b", "arguments": "{}"},
                                },
                            ],
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "done"},
                    }
                ]
            },
        ]
    )

    def fake_open(request, *, timeout):
        del timeout
        requests.append(json.loads(request.data.decode("utf-8")))
        return _JsonResponse(next(responses))

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    tools = ToolRegistry(
        [
            ToolDefinition(
                name=name,
                description=name,
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: ToolResult(ok=True, output="ok"),
            )
            for name in ("inspect_a", "inspect_b")
        ]
    )
    model = OpenAIModelAdapter(
        {
            "model": "deepseek-chat",
            "openaiBaseUrl": "https://api.deepseek.synthetic/v1",
            "openaiApiKey": "synthetic-key",
            "modelMaxRetries": 0,
        },
        tools,
    )

    run_agent_turn(
        model=model,
        tools=tools,
        messages=[{"role": "user", "content": "Inspect both."}],
        cwd=str(tmp_path),
        enable_work_chain=False,
        max_steps=3,
    )

    second_messages = requests[1]["messages"]
    tool_assistants = [
        message
        for message in second_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert len(tool_assistants) == 1
    assert tool_assistants[0]["reasoning_content"] == (
        "Inspect both synthetic inputs."
    )
    assert tool_assistants[0]["content"] == "I will inspect both inputs."
    assert sum(
        message.get("role") == "assistant"
        for message in second_messages
    ) == 1
    assert [call["id"] for call in tool_assistants[0]["tool_calls"]] == [
        "call_a",
        "call_b",
    ]
    assistant_index = second_messages.index(tool_assistants[0])
    tool_result_indices = [
        index
        for index, message in enumerate(second_messages)
        if message.get("role") == "tool"
    ]
    assert tool_result_indices
    assert all(assistant_index < index for index in tool_result_indices)
