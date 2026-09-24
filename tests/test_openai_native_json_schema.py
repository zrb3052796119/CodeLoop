from __future__ import annotations

import json
from typing import ClassVar

import pytest

from minicode.model_registry import create_model_adapter
from minicode.openai_adapter import OpenAIModelAdapter


class _Response:
    status = 200

    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self._offset = 0

    def read1(self, amount: int = -1) -> bytes:
        if self._offset >= len(self._body):
            return b""
        end = len(self._body) if amount < 0 else self._offset + amount
        chunk = self._body[self._offset : end]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class _Tool:
    name = "run_command"
    description = "Run a command"
    input_schema: ClassVar[dict[str, str]] = {"type": "object"}


class _Tools:
    def list(self):
        return [_Tool()]


def _schema() -> dict:
    return {
        "name": "skill_candidate_rerank",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    }


def test_official_openai_native_schema_is_on_wire_and_forces_tools_off(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
    captured: dict = {}

    def fake_open(request, *, timeout):
        del timeout
        captured.update(json.loads(request.data.decode("utf-8")))
        return _Response(
            {
                "choices": [
                    {"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
        )

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", fake_open)
    adapter = create_model_adapter(
        "gpt-4o-mini",
        _Tools(),
        {
            "provider": "openai",
            "openaiBaseUrl": "https://api.openai.com/v1",
            "openaiApiKey": "synthetic",
            "modelMaxRetries": 0,
        },
    )

    result = adapter.next(
        [{"role": "user", "content": "return JSON"}],
        response_schema_override=_schema(),
    )

    assert adapter.supports_native_json_schema is True
    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": _schema(),
    }
    assert "tools" not in captured
    assert result.content == '{"ok":true}'


@pytest.mark.parametrize(
    "runtime",
    [
        {
            "provider": "openrouter",
            "openrouterBaseUrl": "https://openrouter.ai/api",
            "openrouterApiKey": "synthetic",
        },
        {
            "provider": "custom",
            "customBaseUrl": "https://custom.invalid/v1",
            "customApiKey": "synthetic",
        },
    ],
)
def test_compatible_non_openai_providers_cannot_claim_native_schema(
    monkeypatch,
    runtime,
) -> None:
    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
    called = False

    def forbidden_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unsupported schema must fail before transport")

    monkeypatch.setattr("minicode.openai_adapter.open_verified_url", forbidden_open)
    model = "openrouter/auto" if runtime["provider"] == "openrouter" else "custom"
    adapter = create_model_adapter(model, None, runtime)

    assert isinstance(adapter, OpenAIModelAdapter)
    assert adapter.supports_native_json_schema is False
    with pytest.raises(RuntimeError, match="native_json_schema_unsupported"):
        adapter.next(
            [{"role": "user", "content": "return JSON"}],
            response_schema_override=_schema(),
        )
    assert called is False


def test_anthropic_factory_explicitly_disables_native_schema(monkeypatch) -> None:
    monkeypatch.delenv("MINI_CODE_MODEL_MODE", raising=False)
    adapter = create_model_adapter(
        "claude-sonnet-4-20250514",
        None,
        {
            "provider": "anthropic",
            "baseUrl": "https://api.anthropic.com",
            "apiKey": "synthetic",
        },
    )

    assert adapter.supports_native_json_schema is False
