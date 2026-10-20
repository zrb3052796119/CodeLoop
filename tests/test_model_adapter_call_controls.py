from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
from email.message import Message
from types import SimpleNamespace
from typing import Any

import pytest

from minicode.anthropic_adapter import AnthropicModelAdapter
from minicode.model_call_control import ModelCallDeadlineExceeded
from minicode.openai_adapter import OpenAIModelAdapter
from minicode.tooling import ToolDefinition, ToolRegistry
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)


class _JsonResponse:
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _ChunkedResponse:
    status = 200

    def __init__(self, body: bytes, *, chunk_size: int = 17, on_read=None) -> None:
        self._body = body
        self._chunk_size = chunk_size
        self._offset = 0
        self._on_read = on_read
        self.read_sizes: list[int] = []
        self.read1_sizes: list[int] = []
        self.closed = False
        self.closed_event = threading.Event()

    def _read_chunk(self, amount: int) -> bytes:
        if self._on_read is not None:
            self._on_read()
        if self._offset >= len(self._body):
            return b""
        effective = self._chunk_size if amount < 0 else min(amount, self._chunk_size)
        result = self._body[self._offset : self._offset + effective]
        self._offset += len(result)
        return result

    def read(self, amount: int = -1) -> bytes:
        self.read_sizes.append(amount)
        return self._read_chunk(amount)

    def read1(self, amount: int = -1) -> bytes:
        self.read1_sizes.append(amount)
        return self._read_chunk(amount)

    def close(self) -> None:
        self.closed = True
        self.closed_event.set()


class _HeldOpenAfterTerminalResponse(_ChunkedResponse):
    def __init__(self, body: bytes) -> None:
        super().__init__(body, chunk_size=len(body))
        self.second_read_started = threading.Event()
        self.release = threading.Event()

    def read1(self, amount: int = -1) -> bytes:
        self.read1_sizes.append(amount)
        if self._offset < len(self._body):
            return self._read_chunk(amount)
        self.second_read_started.set()
        self.release.wait(timeout=5)
        return b""

    def close(self) -> None:
        super().close()
        self.release.set()


class _RecordingSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)


class _InterruptibleSocket(_RecordingSocket):
    def __init__(self) -> None:
        super().__init__()
        self.interrupted = threading.Event()

    def shutdown(self, _how: int) -> None:
        self.interrupted.set()

    def close(self) -> None:
        self.interrupted.set()


class _LockedHTTPResponse(http.client.HTTPResponse):
    def __init__(self) -> None:
        client_socket, self.peer_socket = socket.socketpair()
        self.read_started = threading.Event()
        self.closed_event = threading.Event()
        self.close_threads: list[str] = []
        super().__init__(client_socket)
        self.peer_socket.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: 1024\r\n\r\n"
        )
        self.begin()

    def read1(self, _amount: int = -1) -> bytes:
        self.read_started.set()
        return super().read1(_amount)

    def close(self) -> None:
        self.close_threads.append(threading.current_thread().name)
        try:
            super().close()
        finally:
            self.peer_socket.close()
            self.closed_event.set()


class _SlowDripResponse:
    status = 200

    def __init__(self, delay_seconds: float = 0.01) -> None:
        self.delay_seconds = delay_seconds
        self.read1_calls = 0
        self.read_calls = 0
        self.closed = False
        self.closed_event = threading.Event()
        self.socket = _RecordingSocket()
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.socket))

    def read1(self, _amount: int = -1) -> bytes:
        self.read1_calls += 1
        time.sleep(self.delay_seconds)
        return b"x"

    def read(self, _amount: int = -1) -> bytes:
        self.read_calls += 1
        raise AssertionError("read1 must be preferred for HTTP-style responses")

    def close(self) -> None:
        self.closed = True
        self.closed_event.set()


class _BlockingReadOnlyResponse:
    status = 200

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.closed = False
        self.read_calls = 0
        self.close_threads: list[str] = []

    def read(self, _amount: int = -1) -> bytes:
        self.read_calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        self.finished.set()
        return b'{"unused":"body"}'

    def close(self) -> None:
        self.closed = True
        self.close_threads.append(threading.current_thread().name)
        self.release.set()


class _IgnoringCloseReadOnlyResponse(_BlockingReadOnlyResponse):
    def __init__(self, release: threading.Event) -> None:
        super().__init__()
        self.release = release

    def close(self) -> None:
        self.closed = True


class _UnboundedProductionResponse(http.client.HTTPResponse):
    status = 200
    read1 = None

    def __init__(self) -> None:
        self.read_calls = 0
        self.was_closed = False

    def read(self) -> bytes:
        self.read_calls += 1
        return b"{}"

    def close(self) -> None:
        self.was_closed = True


def _tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition(
                name="read_file",
                description="Read one file.",
                input_schema={"type": "object"},
                validator=lambda value: value,
                run=lambda _value, _context: None,
            )
        ]
    )


def _openai_text_response(text: str = "done") -> _JsonResponse:
    return _JsonResponse(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }
    )


def _anthropic_text_response(*, include_thinking: bool = False) -> _JsonResponse:
    content: list[dict[str, Any]] = []
    if include_thinking:
        content.append(
            {
                "type": "thinking",
                "thinking": "provider-private-reasoning",
                "signature": "synthetic-signature",
            }
        )
    content.append({"type": "text", "text": "done"})
    return _JsonResponse(
        {
            "stop_reason": "end_turn",
            "content": content,
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
    )


def _provider_text_body(provider: str, *, metadata_size: int = 0) -> bytes:
    if provider == "openai":
        payload: dict[str, Any] = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "done"},
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }
    else:
        payload = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
    if metadata_size:
        payload["provider_metadata"] = "m" * metadata_size
    return json.dumps(payload).encode()


def _provider_stream_body(provider: str, text: str = "live") -> bytes:
    if provider == "openai":
        events: list[dict[str, Any] | str] = [
            {"choices": [{"delta": {"content": text}, "finish_reason": None}]},
            {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
            "[DONE]",
        ]
    else:
        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 3}},
            },
            {
                "type": "content_block_start",
                "content_block": {"type": "text"},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
    return b"".join(
        f"data: {event if isinstance(event, str) else json.dumps(event)}\n".encode()
        for event in events
    )


class _BlockingOpen:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.started = threading.Event()
        self.release = threading.Event()
        self.responses: list[_ChunkedResponse] = []
        self._lock = threading.Lock()

    def __call__(self, _request, *, timeout):
        del timeout
        response = _ChunkedResponse(_provider_text_body(self.provider))
        with self._lock:
            self.responses.append(response)
            self.started.set()
        self.release.wait(timeout=5)
        return response


class _IgnoringCloseResponseFactory:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.responses: list[_IgnoringCloseReadOnlyResponse] = []
        self._lock = threading.Lock()

    def __call__(self, _request, *, timeout):
        del timeout
        response = _IgnoringCloseReadOnlyResponse(self.release)
        with self._lock:
            self.responses.append(response)
        return response


def test_openai_per_call_controls_override_runtime_and_provider_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_open(request, *, timeout):
        del timeout
        captured.append(json.loads(request.data.decode("utf-8")))
        return _openai_text_response()

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    adapter = OpenAIModelAdapter(
        {
            "model": "gpt-test",
            "openaiBaseUrl": "https://openai.invalid/v1",
            "openaiApiKey": "test",
            "maxOutputTokens": 4_096,
            "temperature": 0.8,
            "enableThinking": False,
            "modelMaxRetries": 4,
            "_openrouter_params": {
                "tools": [{"type": "function", "function": {"name": "unsafe"}}],
                "tool_choice": "auto",
                "parallel_tool_calls": True,
                "max_tokens": 8_192,
                "temperature": 1.0,
            },
        },
        _tool_registry(),
    )

    step = adapter.next(
        [{"role": "user", "content": "classify"}],
        disable_tools=True,
        max_output_tokens_override=64,
        temperature_override=0,
        max_retries_override=0,
        isolated_reasoning_state=True,
    )

    assert step.content == "done"
    assert len(captured) == 1
    body = captured[0]
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "parallel_tool_calls" not in body
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.0
    assert body["enable_thinking"] is False


def test_openai_normal_call_retains_runtime_tools_and_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_open(request, *, timeout):
        del timeout
        captured.append(json.loads(request.data.decode("utf-8")))
        return _openai_text_response()

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    adapter = OpenAIModelAdapter(
        {
            "model": "gpt-test",
            "openaiBaseUrl": "https://openai.invalid/v1",
            "openaiApiKey": "test",
            "maxOutputTokens": 1_024,
            "temperature": 0.6,
            "modelMaxRetries": 0,
        },
        _tool_registry(),
    )

    adapter.next([{"role": "user", "content": "work"}])

    assert len(captured[0]["tools"]) == 1
    assert captured[0]["max_tokens"] == 1_024
    assert captured[0]["temperature"] == 0.6


def test_openai_zero_retry_override_attempts_transport_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    def fail_open(_request, *, timeout):
        del timeout
        attempts.append(1)
        raise urllib.error.URLError("synthetic outage")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fail_open)
    adapter = OpenAIModelAdapter(
        {
            "model": "gpt-test",
            "openaiBaseUrl": "https://openai.invalid/v1",
            "openaiApiKey": "test",
            "modelMaxRetries": 3,
        },
        None,
    )

    with pytest.raises(urllib.error.URLError):
        adapter.next(
            [{"role": "user", "content": "classify"}],
            max_retries_override=0,
        )

    assert len(attempts) == 1


def test_anthropic_isolated_call_neither_consumes_nor_mutates_thinking_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_open(request, *, timeout):
        del timeout
        captured.append(json.loads(request.data.decode("utf-8")))
        return _anthropic_text_response(include_thinking=True)

    monkeypatch.setattr("minicode.anthropic_adapter.open_verified_url", fake_open)
    adapter = AnthropicModelAdapter(
        {
            "model": "claude-test",
            "baseUrl": "https://anthropic.invalid",
            "authToken": "test",
            "maxOutputTokens": 4_096,
            "temperature": 0.8,
            "modelMaxRetries": 4,
        },
        _tool_registry(),
    )
    saved_thinking = [
        {
            "type": "thinking",
            "thinking": "main-agent-reasoning",
            "signature": "main-signature",
        }
    ]
    adapter._thinking_blocks = list(saved_thinking)

    step = adapter.next(
        [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "classify"},
        ],
        disable_tools=True,
        max_output_tokens_override=64,
        temperature_override=0,
        max_retries_override=0,
        isolated_reasoning_state=True,
    )

    assert step.content == "done"
    assert adapter._thinking_blocks == saved_thinking
    assert len(captured) == 1
    body = captured[0]
    assert "tools" not in body
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.0
    assistant = next(
        message for message in body["messages"] if message["role"] == "assistant"
    )
    assert all(block.get("type") != "thinking" for block in assistant["content"])


def test_anthropic_normal_call_retains_runtime_behavior_and_thinking_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_open(request, *, timeout):
        del timeout
        captured.append(json.loads(request.data.decode("utf-8")))
        return _anthropic_text_response(include_thinking=True)

    monkeypatch.setattr("minicode.anthropic_adapter.open_verified_url", fake_open)
    adapter = AnthropicModelAdapter(
        {
            "model": "claude-test",
            "baseUrl": "https://anthropic.invalid",
            "authToken": "test",
            "maxOutputTokens": 1_024,
            "temperature": 0.6,
            "modelMaxRetries": 0,
        },
        _tool_registry(),
    )
    saved = {
        "type": "thinking",
        "thinking": "main-agent-reasoning",
        "signature": "main-signature",
    }
    adapter._thinking_blocks = [saved]

    adapter.next(
        [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "continue"},
        ]
    )

    body = captured[0]
    assert len(body["tools"]) == 1
    assert body["max_tokens"] == 1_024
    assert body["temperature"] == 0.6
    assistant = next(
        message for message in body["messages"] if message["role"] == "assistant"
    )
    assert assistant["content"][0] == saved
    assert adapter._thinking_blocks == [
        {
            "type": "thinking",
            "thinking": "provider-private-reasoning",
            "signature": "synthetic-signature",
        }
    ]


def test_anthropic_zero_retry_override_attempts_transport_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    def fail_open(_request, *, timeout):
        del timeout
        attempts.append(1)
        raise urllib.error.URLError("synthetic outage")

    monkeypatch.setattr("minicode.anthropic_adapter.open_verified_url", fail_open)
    adapter = AnthropicModelAdapter(
        {
            "model": "claude-test",
            "baseUrl": "https://anthropic.invalid",
            "authToken": "test",
            "modelMaxRetries": 3,
        },
        None,
    )

    with pytest.raises(urllib.error.URLError):
        adapter.next(
            [{"role": "user", "content": "classify"}],
            max_retries_override=0,
        )

    assert len(attempts) == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_post_open_control_checkpoint_closes_owned_response(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    token = TurnCancellationToken("turn_" + "a" * 32)
    response = _ChunkedResponse(_provider_text_body(provider))

    def return_then_cancel(_request, **_kwargs):
        token.request()
        return response

    monkeypatch.setattr(
        f"minicode.{provider}_adapter._open_verified_url_controlled",
        return_then_cancel,
    )

    with pytest.raises(TurnCancellationRequested):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "classify"}],
            cancellation_token=token,
        )

    assert response.closed_event.wait(timeout=0.3)
    assert response.read1_sizes == []


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_retryable_http_error_is_closed_before_backoff_and_retry(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    error_body = _ChunkedResponse(b'{"error":{"message":"retry"}}')
    retryable = urllib.error.HTTPError(
        "https://provider.invalid",
        429,
        "rate limited",
        None if provider == "anthropic" else {},
        error_body,
    )
    success = _ChunkedResponse(_provider_text_body(provider))
    attempts = 0

    def fake_open(_request, *, timeout):
        nonlocal attempts
        del timeout
        attempts += 1
        if attempts == 1:
            raise retryable
        assert error_body.closed is True
        return success

    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        fake_open,
    )
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.calculate_backoff",
        lambda *_args, **_kwargs: 0.0,
    )
    adapter = _adapter_for_provider(provider)
    adapter.runtime["modelMaxRetries"] = 1

    step = adapter.next([{"role": "user", "content": "retry"}])

    assert attempts == 2
    assert error_body.closed is True
    assert success.closed_event.wait(timeout=0.3)
    assert step.content == "done"


def test_anthropic_retry_after_is_read_from_real_http_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Message()
    headers["Retry-After"] = "7"
    error_body = _ChunkedResponse(b'{"error":{"message":"retry"}}')
    retryable = urllib.error.HTTPError(
        "https://provider.invalid",
        429,
        "rate limited",
        headers,
        error_body,
    )
    success = _ChunkedResponse(_provider_text_body("anthropic"))
    responses: list[object] = [retryable, success]
    observed_retry_after: list[float | None] = []

    def fake_open(_request, *, timeout):
        del timeout
        outcome = responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def capture_backoff(_attempt: int, **kwargs: Any) -> float:
        observed_retry_after.append(kwargs.get("retry_after"))
        return 0.0

    monkeypatch.setattr("minicode.anthropic_adapter.open_verified_url", fake_open)
    monkeypatch.setattr(
        "minicode.anthropic_adapter.calculate_backoff",
        capture_backoff,
    )
    adapter = _adapter_for_provider("anthropic")
    adapter.runtime["modelMaxRetries"] = 1

    step = adapter.next([{"role": "user", "content": "retry"}])

    assert observed_retry_after == [7.0]
    assert error_body.closed_event.wait(timeout=0.3)
    assert success.closed_event.wait(timeout=0.3)
    assert step.content == "done"


def _adapter_for_provider(provider: str):
    if provider == "openai":
        return OpenAIModelAdapter(
            {
                "model": "gpt-test",
                "openaiBaseUrl": "https://openai.invalid/v1",
                "openaiApiKey": "test",
                "modelMaxRetries": 0,
            },
            None,
        )
    return AnthropicModelAdapter(
        {
            "model": "claude-test",
            "baseUrl": "https://anthropic.invalid",
            "authToken": "test",
            "modelMaxRetries": 0,
        },
        None,
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_successful_non_streaming_response_is_closed_and_reader_exits(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _ChunkedResponse(_provider_text_body(provider))
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    step = _adapter_for_provider(provider).next(
        [{"role": "user", "content": "normal agent call"}]
    )

    assert step.content == "done"
    assert response.closed_event.wait(timeout=0.3)
    assert response.read_sizes == []
    assert response.read1_sizes
    assert not any(
        thread.name == "minicode-model-response-reader" and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_controller_interrupts_http_socket_without_closing_under_reader_lock(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _LockedHTTPResponse()
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    started_at = time.monotonic()
    with pytest.raises(ModelCallDeadlineExceeded):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "classify"}],
            deadline_monotonic=started_at + 0.05,
            max_response_bytes_override=4096,
        )

    assert time.monotonic() - started_at < 0.3
    assert response.read_started.is_set()
    assert response.closed_event.wait(timeout=0.3)
    assert response.close_threads
    assert "minicode-model-response-reader" in response.close_threads
    assert "MainThread" not in response.close_threads


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_streaming_sse_is_incremental_bounded_and_closed(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _ChunkedResponse(
        _provider_stream_body(provider),
        chunk_size=7,
    )
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )
    observed: list[tuple[str, bool]] = []

    step = _adapter_for_provider(provider).next(
        [{"role": "user", "content": "stream"}],
        on_stream_chunk=lambda chunk: observed.append((chunk, response.closed)),
        max_response_bytes_override=4096,
    )

    assert observed == [("live", False)]
    assert step.content == "live"
    assert response.closed_event.wait(timeout=0.3)
    assert response.read_sizes == []
    assert len(response.read1_sizes) > 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_streaming_terminal_event_stops_without_waiting_for_eof(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _HeldOpenAfterTerminalResponse(_provider_stream_body(provider))
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )
    deadline = time.monotonic() + 0.3

    step = _adapter_for_provider(provider).next(
        [{"role": "user", "content": "stream"}],
        on_stream_chunk=lambda _chunk: None,
        deadline_monotonic=deadline,
        max_response_bytes_override=4096,
    )

    assert step.content == "live"
    assert time.monotonic() < deadline
    assert response.closed_event.wait(timeout=0.3)
    assert response.second_read_started.is_set() is False
    assert len(response.read1_sizes) == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_streaming_raw_bytes_over_limit_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    first_event = _provider_stream_body(provider, text="first")
    terminal = (
        b"data: [DONE]\n"
        if provider == "openai"
        else b'data: {"type": "message_stop"}\n'
    )
    assert first_event.endswith(terminal)
    first_event = first_event[: -len(terminal)]
    response = _ChunkedResponse(first_event + (b"x" * 512), chunk_size=11)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    with pytest.raises(RuntimeError, match="configured byte limit"):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "stream"}],
            on_stream_chunk=lambda _chunk: None,
            max_response_bytes_override=len(first_event) + 32,
        )

    assert response.closed_event.wait(timeout=0.3)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_stream_callback_failure_closes_response_and_releases_reader(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    failed_response = _ChunkedResponse(_provider_stream_body(provider), chunk_size=9)
    success_response = _ChunkedResponse(_provider_text_body(provider))
    responses = iter([failed_response, success_response])
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: next(responses),
    )

    def fail_callback(_chunk: str) -> None:
        raise ValueError("presentation callback failed")

    with pytest.raises(ValueError, match="presentation callback failed"):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "stream"}],
            on_stream_chunk=fail_callback,
            max_response_bytes_override=4096,
        )

    assert failed_response.closed_event.wait(timeout=0.3)
    step = _adapter_for_provider(provider).next(
        [{"role": "user", "content": "next call"}]
    )
    assert step.content == "done"
    assert success_response.closed_event.wait(timeout=0.3)
    reader_exit_deadline = time.monotonic() + 0.3
    while time.monotonic() < reader_exit_deadline and any(
        thread.name == "minicode-model-response-reader" and thread.is_alive()
        for thread in threading.enumerate()
    ):
        time.sleep(0.005)
    assert not any(
        thread.name == "minicode-model-response-reader" and thread.is_alive()
        for thread in threading.enumerate()
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_blocking_stream_read_obeys_deadline_and_async_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _BlockingReadOnlyResponse()
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    started_at = time.monotonic()
    with pytest.raises(ModelCallDeadlineExceeded):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "stream"}],
            on_stream_chunk=lambda _chunk: None,
            deadline_monotonic=started_at + 0.05,
            max_response_bytes_override=4096,
        )

    assert time.monotonic() - started_at < 0.3
    assert response.finished.wait(timeout=0.3)
    assert response.close_threads
    assert "MainThread" not in response.close_threads


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_blocking_stream_read_obeys_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _BlockingReadOnlyResponse()
    token = TurnCancellationToken("turn_" + "f" * 32)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )
    cancel = threading.Timer(0.04, token.request)
    cancel.start()

    started_at = time.monotonic()
    try:
        with pytest.raises(TurnCancellationRequested):
            _adapter_for_provider(provider).next(
                [{"role": "user", "content": "stream"}],
                on_stream_chunk=lambda _chunk: None,
                cancellation_token=token,
                max_response_bytes_override=4096,
            )
    finally:
        cancel.cancel()
        response.release.set()

    assert time.monotonic() - started_at < 0.3
    assert response.finished.wait(timeout=0.3)
    assert response.close_threads
    assert "MainThread" not in response.close_threads


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_small_content_budget_allows_bounded_provider_envelope_metadata(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    body = _provider_text_body(provider, metadata_size=2048)
    assert len(body) > 256
    response = _ChunkedResponse(body)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    step = _adapter_for_provider(provider).next(
        [{"role": "user", "content": "classify"}],
        max_output_tokens_override=32,
        max_response_bytes_override=4096,
    )

    assert step.content == "done"
    assert response.closed is True


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_production_http_response_without_bounded_reader_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _UnboundedProductionResponse()
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    with pytest.raises(TypeError, match="bounded reader"):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "classify"}],
            max_response_bytes_override=4096,
        )

    assert response.read_calls == 0
    assert response.was_closed is True


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_non_streaming_response_is_read_in_chunks_and_rejected_at_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    private_body = ("private-provider-body-" * 20).encode()
    response = _ChunkedResponse(private_body)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    with pytest.raises(RuntimeError) as raised:
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "classify"}],
            max_response_bytes_override=64,
        )

    assert "private-provider-body" not in str(raised.value)
    assert response.closed is True
    assert response.read_sizes == []
    assert len(response.read1_sizes) > 1
    assert all(0 < requested <= 65 for requested in response.read1_sizes)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_normal_non_streaming_call_also_has_a_default_response_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _ChunkedResponse(b"x" * 100)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.DEFAULT_MAX_RESPONSE_BYTES",
        64,
    )
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    with pytest.raises(RuntimeError, match="configured byte limit"):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "normal agent call"}]
        )

    assert response.closed is True


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_blocking_transport_open_obeys_absolute_deadline_and_closes_late_response(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    blocking_open = _BlockingOpen(provider)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        blocking_open,
    )

    started_at = time.monotonic()
    try:
        with pytest.raises(ModelCallDeadlineExceeded):
            _adapter_for_provider(provider).next(
                [{"role": "user", "content": "classify"}],
                deadline_monotonic=started_at + 0.05,
                max_response_bytes_override=4096,
            )
    finally:
        blocking_open.release.set()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.3
    assert blocking_open.started.is_set()
    assert len(blocking_open.responses) == 1
    assert blocking_open.responses[0].closed_event.wait(timeout=0.3)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_blocking_transport_open_obeys_cancellation_and_closes_late_response(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    blocking_open = _BlockingOpen(provider)
    token = TurnCancellationToken("turn_" + "e" * 32)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        blocking_open,
    )
    cancel = threading.Timer(0.04, token.request)
    cancel.start()

    started_at = time.monotonic()
    try:
        with pytest.raises(TurnCancellationRequested):
            _adapter_for_provider(provider).next(
                [{"role": "user", "content": "classify"}],
                cancellation_token=token,
                max_response_bytes_override=4096,
            )
    finally:
        cancel.cancel()
        blocking_open.release.set()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.3
    assert blocking_open.started.is_set()
    assert len(blocking_open.responses) == 1
    assert blocking_open.responses[0].closed_event.wait(timeout=0.3)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_abandoned_transport_workers_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    blocking_open = _BlockingOpen(provider)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        blocking_open,
    )

    try:
        for _ in range(5):
            deadline = time.monotonic() + 0.03
            with pytest.raises(ModelCallDeadlineExceeded):
                _adapter_for_provider(provider).next(
                    [{"role": "user", "content": "classify"}],
                    deadline_monotonic=deadline,
                    max_response_bytes_override=4096,
                )
    finally:
        blocking_open.release.set()

    assert len(blocking_open.responses) == 4
    assert all(
        response.closed_event.wait(timeout=0.3)
        for response in blocking_open.responses
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_non_streaming_chunk_read_rechecks_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _SlowDripResponse()
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    started_at = time.monotonic()
    with pytest.raises(ModelCallDeadlineExceeded):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "classify"}],
            deadline_monotonic=started_at + 0.12,
            max_response_bytes_override=4096,
        )
    elapsed = time.monotonic() - started_at

    assert 0.03 <= elapsed < 0.5
    assert response.read_calls == 0
    assert response.read1_calls >= 2
    assert len(response.socket.timeouts) >= 2
    assert response.socket.timeouts[-1] < response.socket.timeouts[0]
    assert response.closed_event.wait(timeout=0.3)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_non_streaming_chunk_read_rechecks_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    token = TurnCancellationToken("turn_" + "c" * 32)
    response = _ChunkedResponse(b'{"unused":"body"}', on_read=token.request)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    with pytest.raises(TurnCancellationRequested):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "classify"}],
            cancellation_token=token,
            max_response_bytes_override=4096,
        )

    assert response.read_sizes == []
    assert len(response.read1_sizes) == 1
    assert response.closed is True


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_blocking_read_only_fallback_is_stopped_at_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _BlockingReadOnlyResponse()
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )

    started_at = time.monotonic()
    with pytest.raises(ModelCallDeadlineExceeded):
        _adapter_for_provider(provider).next(
            [{"role": "user", "content": "classify"}],
            deadline_monotonic=started_at + 0.05,
            max_response_bytes_override=4096,
        )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.3
    assert response.started.is_set()
    assert response.closed is True
    assert response.finished.wait(timeout=0.3)
    assert response.read_calls == 1


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_blocking_read_only_fallback_is_stopped_by_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response = _BlockingReadOnlyResponse()
    token = TurnCancellationToken("turn_" + "d" * 32)
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        lambda _request, *, timeout: response,
    )
    cancel = threading.Timer(0.04, token.request)
    cancel.start()

    started_at = time.monotonic()
    try:
        with pytest.raises(TurnCancellationRequested):
            _adapter_for_provider(provider).next(
                [{"role": "user", "content": "classify"}],
                cancellation_token=token,
                max_response_bytes_override=4096,
            )
    finally:
        cancel.cancel()
        response.release.set()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.3
    assert response.started.is_set()
    assert response.closed is True
    assert response.finished.wait(timeout=0.3)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_abandoned_body_reader_workers_are_bounded_even_when_close_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    response_factory = _IgnoringCloseResponseFactory()
    monkeypatch.setattr(
        f"minicode.{provider}_adapter.open_verified_url",
        response_factory,
    )

    try:
        for _ in range(5):
            deadline = time.monotonic() + 0.03
            with pytest.raises(ModelCallDeadlineExceeded):
                _adapter_for_provider(provider).next(
                    [{"role": "user", "content": "classify"}],
                    deadline_monotonic=deadline,
                    max_response_bytes_override=4096,
                )
    finally:
        response_factory.release.set()

    assert len(response_factory.responses) == 4
    assert all(response.closed for response in response_factory.responses)
    assert all(
        response.finished.wait(timeout=0.3)
        for response in response_factory.responses
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_output_tokens_override": 0},
        {"max_response_bytes_override": 0},
        {"max_response_bytes_override": (16 * 1024 * 1024) + 1},
        {"temperature_override": float("nan")},
        {"max_retries_override": -1},
    ],
)
def test_provider_call_control_overrides_fail_closed_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, Any],
) -> None:
    def unexpected_open(_request, *, timeout):
        del timeout
        raise AssertionError("transport must not run for an invalid override")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", unexpected_open)
    adapter = OpenAIModelAdapter(
        {
            "model": "gpt-test",
            "openaiBaseUrl": "https://openai.invalid/v1",
            "openaiApiKey": "test",
        },
        None,
    )

    with pytest.raises(ValueError):
        adapter.next([{"role": "user", "content": "classify"}], **kwargs)
