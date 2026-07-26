from __future__ import annotations

import pytest

from minicode.web.request_guard import GATEWAY_TOKEN_ENV, ensure_execution_authorized


class _Headers:
    def __init__(self, values: dict[str, list[str]]) -> None:
        self._values = {key.lower(): list(items) for key, items in values.items()}

    def get_all(self, name: str, default: list[str] | None = None) -> list[str]:
        return self._values.get(name.lower(), default if default is not None else [])


class _Server:
    def __init__(self, host: str, port: int = 8080) -> None:
        self.server_address = (host, port)


class _Handler:
    def __init__(self, bind_host: str, headers: dict[str, list[str]]) -> None:
        self.server = _Server(bind_host)
        self.headers = _Headers(headers)
        self.responses: list[tuple[dict, int]] = []

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self.responses.append((payload, status))


def test_loopback_bind_with_local_host_header_is_allowed() -> None:
    handler = _Handler("127.0.0.1", {"Host": ["127.0.0.1:8080"]})
    assert ensure_execution_authorized(handler) is True
    assert handler.responses == []


def test_loopback_bind_with_localhost_host_header_is_allowed() -> None:
    handler = _Handler("127.0.0.1", {"Host": ["localhost:8080"]})
    assert ensure_execution_authorized(handler) is True


def test_non_loopback_bind_without_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GATEWAY_TOKEN_ENV, raising=False)
    handler = _Handler("0.0.0.0", {"Host": ["10.1.2.3:8080"]})
    assert ensure_execution_authorized(handler) is False
    assert handler.responses[0][1] == 403


def test_non_loopback_bind_with_matching_token_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GATEWAY_TOKEN_ENV, "secret-token")
    handler = _Handler(
        "0.0.0.0",
        {"Host": ["10.1.2.3:8080"], "Authorization": ["Bearer secret-token"]},
    )
    assert ensure_execution_authorized(handler) is True


def test_non_loopback_bind_with_wrong_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GATEWAY_TOKEN_ENV, "secret-token")
    handler = _Handler(
        "0.0.0.0",
        {"Host": ["10.1.2.3:8080"], "Authorization": ["Bearer wrong"]},
    )
    assert ensure_execution_authorized(handler) is False
    assert handler.responses[0][1] == 403


def test_foreign_host_header_is_rejected_on_loopback_bind() -> None:
    """DNS-rebinding defence: the Host header must itself resolve locally."""
    handler = _Handler("127.0.0.1", {"Host": ["evil.example:8080"]})
    assert ensure_execution_authorized(handler) is False
    assert handler.responses[0][1] == 403


def test_missing_host_header_is_rejected_on_loopback_bind() -> None:
    handler = _Handler("127.0.0.1", {})
    assert ensure_execution_authorized(handler) is False


def test_cross_origin_request_is_rejected() -> None:
    handler = _Handler(
        "127.0.0.1",
        {"Host": ["127.0.0.1:8080"], "Origin": ["http://evil.example"]},
    )
    assert ensure_execution_authorized(handler) is False
    assert handler.responses[0][1] == 403


def test_same_origin_loopback_request_is_allowed() -> None:
    handler = _Handler(
        "127.0.0.1",
        {"Host": ["127.0.0.1:8080"], "Origin": ["http://127.0.0.1:8080"]},
    )
    assert ensure_execution_authorized(handler) is True


def test_null_origin_is_rejected() -> None:
    handler = _Handler(
        "127.0.0.1",
        {"Host": ["127.0.0.1:8080"], "Origin": ["null"]},
    )
    assert ensure_execution_authorized(handler) is False


def test_token_authorizes_even_on_loopback_with_foreign_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GATEWAY_TOKEN_ENV, "secret-token")
    handler = _Handler(
        "127.0.0.1",
        {"Host": ["evil.example:8080"], "Authorization": ["Bearer secret-token"]},
    )
    assert ensure_execution_authorized(handler) is True
