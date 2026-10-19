"""OpenAI transport timeout policy for long codegen streams."""

from __future__ import annotations

import json
import time

import pytest

from minicode.openai_adapter import (
    DEFAULT_MODEL_TRANSPORT_TIMEOUT_SECONDS,
    OpenAIModelAdapter,
    model_transport_timeout_seconds,
)


def _adapter(**runtime):
    base = {
        "model": "gpt-test",
        "openaiBaseUrl": "https://openai.invalid/v1",
        "openaiApiKey": "test",
        "modelMaxRetries": 0,
    }
    base.update(runtime)
    return OpenAIModelAdapter(base, None)


class _GappedBody:
    status = 200

    def __init__(self, chunks: list[bytes], gap: float) -> None:
        self._chunks = list(chunks)
        self._gap = gap

    def read1(self, _amount: int) -> bytes:
        if not self._chunks:
            return b""
        time.sleep(self._gap)
        return self._chunks.pop(0)

    def close(self) -> None:
        return None


def _sse(text: str, finish: str | None = None) -> bytes:
    payload = {"choices": [{"delta": {"content": text}, "finish_reason": finish}]}
    return f"data: {json.dumps(payload)}\n".encode()


def test_timeout_policy_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINICODE_MODEL_TIMEOUT", raising=False)
    assert model_transport_timeout_seconds({}) == DEFAULT_MODEL_TRANSPORT_TIMEOUT_SECONDS
    assert DEFAULT_MODEL_TRANSPORT_TIMEOUT_SECONDS == 120.0
    monkeypatch.setenv("MINICODE_MODEL_TIMEOUT", "33")
    assert model_transport_timeout_seconds({}) == 33.0
    assert model_transport_timeout_seconds({"modelTimeoutSeconds": 45}) == 45.0

    seen: dict[str, object] = {}

    def open_url(request, *, timeout):
        seen["timeout"] = timeout
        seen["body"] = json.loads(request.data.decode())
        raise RuntimeError("stop")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", open_url)
    with pytest.raises(RuntimeError, match="stop"):
        _adapter().next(
            [{"role": "user", "content": "codegen"}],
            on_stream_chunk=lambda _chunk: None,
        )
    assert seen["timeout"] == 33.0
    assert seen["body"]["stream"] is True

    with pytest.raises(RuntimeError, match="stop"):
        _adapter().next([{"role": "user", "content": "codegen"}])
    assert "stream" not in seen["body"]


def test_streaming_read_uses_idle_timeout_and_nonstreaming_stays_cumulative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gap = 0.3
    timeout = 0.8
    responses: list[_GappedBody] = []

    def open_url(_request, *, timeout):
        del timeout
        return responses.pop(0)

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", open_url)
    adapter = _adapter(modelTimeoutSeconds=timeout)
    stream = [
        _sse("a"),
        _sse("b"),
        _sse("c"),
        _sse("d", "stop"),
        b"data: [DONE]\n",
    ]
    responses.append(_GappedBody(stream, gap))
    started = time.monotonic()
    step = adapter.next(
        [{"role": "user", "content": "codegen"}],
        on_stream_chunk=lambda _chunk: None,
    )
    elapsed = time.monotonic() - started
    assert step.content == "abcd"
    assert elapsed > timeout

    responses.append(_GappedBody([b"{"] * 6, gap))
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="timed out"):
        adapter.next([{"role": "user", "content": "codegen"}])
    assert time.monotonic() - started < gap * 6
