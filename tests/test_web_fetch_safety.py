from __future__ import annotations

import math
import socket
import ssl

import pytest

from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import create_default_tool_registry
from minicode.tools import http_utils
from minicode.tools import network_safety
from minicode.tools.bounded_resolver import BoundedResolver, ResolverError
from minicode.tools.web_fetch import web_fetch_tool


class _SafeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Encoding": "identity",
            "Content-Length": str(len(payload)),
        }
        self._payload = payload
        self._offset = 0
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> "_SafeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_private_172_17_destination_is_blocked_before_transport(
    monkeypatch,
) -> None:
    transport_calls: list[object] = []

    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda request, *, timeout: transport_calls.append((request, timeout)),
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "http://172.17.0.1/private?credential=fixture-secret"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output == (
        "error[destination_blocked]: The request destination is not allowed."
    )
    assert transport_calls == []
    assert "fixture-secret" not in result.output


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        (None, "invalid_request"),
        ({}, "invalid_request"),
        ({"url": 7}, "invalid_request"),
        ({"url": ""}, "invalid_request"),
        (
            {
                "url": "http://93.184.216.34/",
                "unexpected": "fixture-secret",
            },
            "invalid_request",
        ),
        ({"url": "ftp://93.184.216.34/"}, "unsupported_scheme"),
        (
            {"url": "http://user:fixture-secret@93.184.216.34/"},
            "invalid_request",
        ),
        (
            {"url": "http://93.184.216.34/path\nfixture-secret"},
            "invalid_request",
        ),
        ({"url": "http://93.184.216.34:99999/"}, "invalid_request"),
        ({"url": "http://93.184.216.34/" + "x" * 4096}, "invalid_request"),
        (
            {"url": "http://93.184.216.34/", "max_chars": True},
            "invalid_request",
        ),
        (
            {"url": "http://93.184.216.34/", "max_chars": "100"},
            "invalid_request",
        ),
        (
            {"url": "http://93.184.216.34/", "max_chars": 100.5},
            "invalid_request",
        ),
        (
            {"url": "http://93.184.216.34/", "max_chars": math.nan},
            "invalid_request",
        ),
        (
            {"url": "http://93.184.216.34/", "max_chars": math.inf},
            "invalid_request",
        ),
        (
            {"url": "http://93.184.216.34/", "max_chars": 99},
            "invalid_request",
        ),
        (
            {"url": "http://93.184.216.34/", "max_chars": 50_001},
            "invalid_request",
        ),
    ],
)
def test_invalid_inputs_use_fixed_errors_without_transport_or_echo(
    payload: object,
    error_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_calls: list[object] = []

    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda request, *, timeout: transport_calls.append((request, timeout)),
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        payload,
        ToolContext(cwd="/private/fixture-secret-workspace"),
    )

    assert result.ok is False
    assert result.output.startswith(f"error[{error_code}]: ")
    assert "\n" not in result.output
    assert "fixture-secret" not in result.output
    assert "93.184.216.34" not in result.output
    assert transport_calls == []


def test_public_hostname_uses_validated_pinned_transport_without_second_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_calls: list[tuple[str, int]] = []
    pinned_calls: list[object] = []

    def resolve_public(
        hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        resolver_calls.append((hostname, port))
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=resolve_public,
    )
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)

    def open_pinned(request: object, *, timeout: float) -> _SafeResponse:
        pinned_calls.append((request, timeout))
        return _SafeResponse("固定连接成功".encode())

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_pinned)

    try:
        result = ToolRegistry([web_fetch_tool]).execute(
            "web_fetch",
            {
                "url": (
                    "https://Public.Example/docs?credential=fixture-secret"
                    "#ignored-fragment"
                )
            },
            ToolContext(cwd="."),
        )
    finally:
        resolver.close()

    assert result.ok is True
    assert resolver_calls == [("public.example", 443)]
    assert len(pinned_calls) == 1
    request, timeout = pinned_calls[0]
    destination = getattr(request, "_minicode_destination")
    assert destination.hostname == "public.example"
    assert destination.addresses == ("93.184.216.34",)
    assert request.full_url.endswith("/docs?credential=fixture-secret")
    assert "#ignored-fragment" not in request.full_url
    assert 0 < timeout <= 30
    request_headers = {
        name.casefold(): value for name, value in request.header_items()
    }
    assert request_headers["accept-encoding"] == "identity"
    assert "minicode-python" in request_headers["user-agent"].casefold()
    assert "fixture-secret" not in result.output


def test_safe_relative_redirect_is_revalidated_and_uses_pinned_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_calls: list[tuple[str, int]] = []
    transport_urls: list[str] = []

    def resolve_public(
        hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        resolver_calls.append((hostname, port))
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=resolve_public,
    )
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)

    def open_pinned(request: object, *, timeout: float) -> _SafeResponse:
        del timeout
        transport_urls.append(request.full_url)
        destination = getattr(request, "_minicode_destination")
        assert destination.addresses == ("93.184.216.34",)
        if len(transport_urls) == 1:
            return _SafeResponse(
                b"",
                status=302,
                headers={"Location": "/next?credential=redirect-secret"},
            )
        return _SafeResponse(
            "重定向成功".encode(),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_pinned)

    try:
        result = ToolRegistry([web_fetch_tool]).execute(
            "web_fetch",
            {"url": "https://public.example/start"},
            ToolContext(cwd="."),
        )
    finally:
        resolver.close()

    assert result.ok is True
    assert resolver_calls == [
        ("public.example", 443),
        ("public.example", 443),
    ]
    assert transport_urls == [
        "https://public.example/start",
        "https://public.example/next?credential=redirect-secret",
    ]
    assert "重定向成功" in result.output
    assert "redirect-secret" not in result.output


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.4.3.2/",
        "http://172.17.0.1/",
        "http://192.168.4.3/",
        "http://169.254.2.3/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://[ff00::1]/",
        "http://[2001:db8::1]/",
        "http://[::]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_all_direct_unsafe_address_classes_have_zero_transport(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_calls: list[object] = []

    def forbidden_open(request: object, *, timeout: float) -> _SafeResponse:
        transport_calls.append((request, timeout))
        return _SafeResponse(b"unexpected")

    monkeypatch.setattr(http_utils, "_open_no_redirect", forbidden_open)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": url},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output == (
        "error[destination_blocked]: The request destination is not allowed."
    )
    assert transport_calls == []
    assert url not in result.output


@pytest.mark.parametrize(
    ("answers", "error_code"),
    [
        (
            [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", 443),
                ),
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("10.0.0.8", 443),
                ),
            ],
            "destination_blocked",
        ),
        (
            [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("::ffff:127.0.0.1", 443, 0, 0),
                )
            ],
            "destination_blocked",
        ),
        ([("malformed",)], "dns_error"),
        ([], "dns_error"),
    ],
)
def test_unsafe_or_malformed_dns_answers_fail_closed_before_transport(
    answers: list[tuple[object, ...]],
    error_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_calls: list[object] = []

    def resolve(
        _hostname: str,
        _port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        return answers

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=resolve,
    )
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda request, *, timeout: transport_calls.append((request, timeout)),
    )

    try:
        result = ToolRegistry([web_fetch_tool]).execute(
            "web_fetch",
            {"url": "https://dns-fixture-secret.example/"},
            ToolContext(cwd="."),
        )
    finally:
        resolver.close()

    assert result.ok is False
    assert result.output.startswith(f"error[{error_code}]: ")
    assert transport_calls == []
    assert "dns-fixture-secret" not in result.output


@pytest.mark.parametrize(
    ("location", "target_behavior"),
    [
        ("http://10.0.0.8/private", "unused"),
        ("http://127.0.0.1/private", "unused"),
        ("https://mixed.example/private", "mixed"),
        ("https://timeout.example/private", "timeout"),
        ("https://busy.example/private", "resolver_busy"),
        ("https://dns-error.example/private", "dns_error"),
    ],
)
def test_unsafe_redirect_targets_are_blocked_with_zero_target_send(
    location: str,
    target_behavior: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_urls: list[str] = []

    class _ResolverByHost:
        def resolve(
            self,
            hostname: str,
            port: int,
            *,
            deadline: float,
        ) -> list[tuple[object, ...]]:
            del deadline
            if hostname == "public.example":
                addresses = ["93.184.216.34"]
            elif target_behavior == "mixed":
                addresses = ["93.184.216.34", "10.0.0.8"]
            elif target_behavior in {"timeout", "resolver_busy", "dns_error"}:
                raise ResolverError(target_behavior)
            else:
                raise AssertionError("unexpected resolver target")
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (address, port),
                )
                for address in addresses
            ]

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _ResolverByHost())

    def open_first(request: object, *, timeout: float) -> _SafeResponse:
        del timeout
        transport_urls.append(request.full_url)
        return _SafeResponse(
            b"",
            status=302,
            headers={"Location": location},
        )

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_first)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "https://public.example/start"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output == (
        "error[redirect_blocked]: The redirect target is not allowed."
    )
    assert transport_urls == ["https://public.example/start"]
    assert location not in result.output


@pytest.mark.parametrize(
    ("fourth_is_redirect", "expected_ok", "expected_urls"),
    [
        (
            False,
            True,
            [
                "https://public.example/0",
                "https://public.example/1",
                "https://public.example/2",
                "https://public.example/3",
            ],
        ),
        (
            True,
            False,
            [
                "https://public.example/0",
                "https://public.example/1",
                "https://public.example/2",
                "https://public.example/3",
            ],
        ),
    ],
)
def test_redirect_limit_allows_exactly_three_and_rejects_the_fourth(
    fourth_is_redirect: bool,
    expected_ok: bool,
    expected_urls: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_urls: list[str] = []

    class _PublicResolver:
        def resolve(
            self,
            _hostname: str,
            port: int,
            *,
            deadline: float,
        ) -> list[tuple[object, ...]]:
            del deadline
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", port),
                )
            ]

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _PublicResolver())

    def open_redirect_chain(
        request: object,
        *,
        timeout: float,
    ) -> _SafeResponse:
        del timeout
        transport_urls.append(request.full_url)
        index = int(request.full_url.rsplit("/", 1)[1])
        if index < 3 or fourth_is_redirect:
            return _SafeResponse(
                b"",
                status=302,
                headers={"Location": f"/{index + 1}"},
            )
        return _SafeResponse(
            b"done",
            headers={"Content-Type": "text/plain"},
        )

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_redirect_chain)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "https://public.example/0"},
        ToolContext(cwd="."),
    )

    assert result.ok is expected_ok
    assert transport_urls == expected_urls
    if expected_ok:
        assert result.output.endswith("done")
    else:
        assert result.output.startswith("error[redirect_blocked]: ")


def test_html_rendering_handles_charset_case_and_removes_script_and_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (
        "<HTML><HEAD>"
        "<SCRIPT>script-fixture-secret</SCRIPT>"
        "<STYLE>style-fixture-secret</STYLE>"
        "</HEAD><BODY><h1>中文标题</h1><p>Readable &amp; safe.</p></BODY></HTML>"
    ).encode()

    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: _SafeResponse(
            payload,
            headers={
                "Content-Type": "Text/HTML; Charset=UTF-8",
                "Content-Encoding": "identity",
                "Content-Length": str(len(payload)),
            },
        ),
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "http://93.184.216.34/page"},
        ToolContext(cwd="."),
    )

    assert result.ok is True
    assert "CONTENT_TYPE: text/html" in result.output
    assert "中文标题" in result.output
    assert "Readable & safe." in result.output
    assert "script-fixture-secret" not in result.output
    assert "style-fixture-secret" not in result.output
    assert "<" not in result.output


def test_declared_oversized_response_is_rejected_before_body_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _SafeResponse(
        b"body-fixture-secret",
        headers={
            "Content-Type": "text/plain",
            "Content-Length": str(network_safety.MAX_RESPONSE_BYTES + 1),
        },
    )
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: response,
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "http://93.184.216.34/oversized"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output == (
        "error[response_too_large]: The response exceeds the safe byte limit."
    )
    assert response.read_sizes == []
    assert "body-fixture-secret" not in result.output


@pytest.mark.parametrize(
    ("extra_byte", "expected_ok"),
    [(False, True), (True, False)],
)
def test_streaming_response_enforces_exact_one_mib_wire_boundary(
    extra_byte: bool,
    expected_ok: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"z" * (network_safety.MAX_RESPONSE_BYTES + int(extra_byte))
    response = _SafeResponse(
        payload,
        headers={"Content-Type": "text/plain"},
    )
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: response,
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {
            "url": "http://93.184.216.34/stream",
            "max_chars": 100,
        },
        ToolContext(cwd="."),
    )

    assert result.ok is expected_ok
    assert response.read_sizes
    assert max(response.read_sizes) <= network_safety.RESPONSE_READ_CHUNK_BYTES
    if expected_ok:
        assert f"WIRE_BYTES: {network_safety.MAX_RESPONSE_BYTES}" in result.output
        assert "TRUNCATED: yes" in result.output
    else:
        assert result.output.startswith("error[response_too_large]: ")
        assert "z" * 10 not in result.output


def test_declared_exact_one_mib_response_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"d" * network_safety.MAX_RESPONSE_BYTES
    response = _SafeResponse(
        payload,
        headers={
            "Content-Type": "text/plain",
            "Content-Length": str(network_safety.MAX_RESPONSE_BYTES),
        },
    )
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: response,
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {
            "url": "http://93.184.216.34/declared-boundary",
            "max_chars": 100,
        },
        ToolContext(cwd="."),
    )

    assert result.ok is True
    assert f"WIRE_BYTES: {network_safety.MAX_RESPONSE_BYTES}" in result.output
    assert max(response.read_sizes) <= network_safety.RESPONSE_READ_CHUNK_BYTES


def test_chunked_boundary_plus_one_fails_without_returning_partial_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"chunked-fixture-secret" + b"c" * (
        network_safety.MAX_RESPONSE_BYTES
    )
    response = _SafeResponse(
        payload,
        headers={
            "Content-Type": "text/plain",
            "Transfer-Encoding": "chunked",
        },
    )
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: response,
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {
            "url": "http://93.184.216.34/chunked-boundary",
            "max_chars": 100,
        },
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output.startswith("error[response_too_large]: ")
    assert max(response.read_sizes) <= network_safety.RESPONSE_READ_CHUNK_BYTES
    assert "chunked-fixture-secret" not in result.output


@pytest.mark.parametrize(
    ("content_type", "payload", "expected_ok", "expected_text"),
    [
        ("text/plain; charset=utf-8", "中文正文".encode(), True, "中文正文"),
        (
            "application/json",
            b'{"message":"json-readable"}',
            True,
            "json-readable",
        ),
        (
            "application/problem+json",
            b'{"message":"problem-readable"}',
            True,
            "problem-readable",
        ),
        (
            "application/json",
            b"{invalid-json-safe-text",
            True,
            "invalid-json-safe-text",
        ),
        (
            "text/plain; charset=unknown-fixture-codec",
            "安全回退".encode(),
            True,
            "安全回退",
        ),
        ("application/octet-stream", b"binary-fixture-secret", False, ""),
    ],
)
def test_text_and_json_types_render_while_binary_fails_closed(
    content_type: str,
    payload: bytes,
    expected_ok: bool,
    expected_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: _SafeResponse(
            payload,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(payload)),
            },
        ),
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "http://93.184.216.34/content"},
        ToolContext(cwd="."),
    )

    assert result.ok is expected_ok
    if expected_ok:
        assert expected_text in result.output
    else:
        assert result.output.startswith("error[unsupported_response_type]: ")
        assert "binary-fixture-secret" not in result.output


def test_non_identity_content_encoding_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: _SafeResponse(
            b"compressed-fixture-secret",
            headers={
                "Content-Type": "text/plain",
                "Content-Encoding": "gzip",
            },
        ),
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "http://93.184.216.34/compressed"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output.startswith("error[unsupported_response_type]: ")
    assert "compressed-fixture-secret" not in result.output


@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (ssl.SSLError("certificate-fixture-secret"), "tls_error"),
        (socket.timeout("timeout-fixture-secret"), "timeout"),
        (OSError("network-fixture-secret"), "network_unavailable"),
        (RuntimeError("runtime-fixture-secret"), "request_failed"),
    ],
)
def test_transport_exceptions_use_low_cardinality_content_free_errors(
    failure: Exception,
    error_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_transport(_request: object, *, timeout: float) -> _SafeResponse:
        del timeout
        raise failure

    monkeypatch.setattr(http_utils, "_open_no_redirect", fail_transport)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {
            "url": (
                "http://93.184.216.34/error"
                "?credential=url-fixture-secret"
            )
        },
        ToolContext(cwd="/private/path-fixture-secret"),
    )

    assert result.ok is False
    assert result.output.startswith(f"error[{error_code}]: ")
    assert "\n" not in result.output
    assert "fixture-secret" not in result.output
    assert "93.184.216.34" not in result.output


def test_http_error_body_is_bounded_and_never_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _SafeResponse(
        b"http-error-body-fixture-secret",
        status=503,
        headers={"Content-Type": "text/plain"},
    )
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: response,
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "http://93.184.216.34/error"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output == "error[http_error]: The server returned an HTTP error."
    assert response.read_sizes
    assert max(response.read_sizes) <= network_safety.RESPONSE_READ_CHUNK_BYTES
    assert "http-error-body-fixture-secret" not in result.output


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Location": ""},
        {"Location": "\nlocation-fixture-secret"},
        {"Location": "https://public.example/start"},
    ],
)
def test_malformed_missing_or_looping_redirect_is_content_free(
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_urls: list[str] = []

    class _PublicResolver:
        def resolve(
            self,
            _hostname: str,
            port: int,
            *,
            deadline: float,
        ) -> list[tuple[object, ...]]:
            del deadline
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", port),
                )
            ]

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _PublicResolver())

    def open_redirect(request: object, *, timeout: float) -> _SafeResponse:
        del timeout
        transport_urls.append(request.full_url)
        return _SafeResponse(b"", status=302, headers=headers)

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_redirect)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "https://public.example/start"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output.startswith("error[redirect_blocked]: ")
    assert "\n" not in result.output
    assert "fixture-secret" not in result.output
    assert transport_urls == ["https://public.example/start"]


def test_same_hostname_rebinding_to_private_is_blocked_before_second_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_calls = 0
    transport_urls: list[str] = []

    class _RebindingResolver:
        def resolve(
            self,
            _hostname: str,
            port: int,
            *,
            deadline: float,
        ) -> list[tuple[object, ...]]:
            nonlocal resolver_calls
            del deadline
            resolver_calls += 1
            address = "93.184.216.34" if resolver_calls == 1 else "10.0.0.8"
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (address, port),
                )
            ]

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _RebindingResolver())

    def open_redirect(request: object, *, timeout: float) -> _SafeResponse:
        del timeout
        transport_urls.append(request.full_url)
        return _SafeResponse(b"", status=302, headers={"Location": "/next"})

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_redirect)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "https://public.example/start"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output.startswith("error[redirect_blocked]: ")
    assert resolver_calls == 2
    assert transport_urls == ["https://public.example/start"]


def test_all_redirect_hops_share_one_monotonic_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100.0}
    transport_timeouts: list[float] = []

    monkeypatch.setattr(
        http_utils.time,
        "monotonic",
        lambda: clock["now"],
    )

    def open_chain(request: object, *, timeout: float) -> _SafeResponse:
        transport_timeouts.append(timeout)
        index = int(request.full_url.rsplit("/", 1)[1])
        clock["now"] += 7
        if index < 3:
            return _SafeResponse(
                b"",
                status=302,
                headers={"Location": f"/{index + 1}"},
            )
        return _SafeResponse(
            b"deadline-shared",
            headers={"Content-Type": "text/plain", "Content-Length": "15"},
        )

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_chain)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "http://93.184.216.34/0"},
        ToolContext(cwd="."),
    )

    assert result.ok is True
    assert transport_timeouts == pytest.approx([30.0, 23.0, 16.0, 9.0])
    assert "deadline-shared" in result.output


def test_slow_body_read_cannot_overrun_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 200.0}

    monkeypatch.setattr(
        http_utils.time,
        "monotonic",
        lambda: clock["now"],
    )

    class _SlowResponse(_SafeResponse):
        def read(self, size: int) -> bytes:
            clock["now"] += 31
            return super().read(size)

    response = _SlowResponse(
        b"slow-body-fixture-secret",
        headers={"Content-Type": "text/plain", "Content-Length": "24"},
    )
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: response,
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "http://93.184.216.34/slow"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output.startswith("error[timeout]: ")
    assert "slow-body-fixture-secret" not in result.output


@pytest.mark.parametrize(
    "error_code",
    ["dns_error", "timeout", "resolver_busy", "network_unavailable"],
)
def test_initial_resolver_failures_have_zero_transport(
    error_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_calls: list[object] = []

    class _FailingResolver:
        def resolve(
            self,
            _hostname: str,
            _port: int,
            *,
            deadline: float,
        ) -> list[tuple[object, ...]]:
            del deadline
            raise ResolverError(error_code)

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _FailingResolver())
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda request, *, timeout: transport_calls.append((request, timeout)),
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "https://resolver-fixture-secret.example/"},
        ToolContext(cwd="."),
    )

    assert result.ok is False
    assert result.output.startswith(f"error[{error_code}]: ")
    assert "fixture-secret" not in result.output
    assert transport_calls == []


def test_safe_cross_host_redirect_revalidates_and_repins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_destinations: list[tuple[str, tuple[str, ...]]] = []

    class _CrossHostResolver:
        def resolve(
            self,
            hostname: str,
            port: int,
            *,
            deadline: float,
        ) -> list[tuple[object, ...]]:
            del deadline
            address = {
                "public.example": "93.184.216.34",
                "other.example": "93.184.216.35",
            }[hostname]
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (address, port),
                )
            ]

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _CrossHostResolver())

    def open_cross_host(request: object, *, timeout: float) -> _SafeResponse:
        del timeout
        destination = getattr(request, "_minicode_destination")
        transport_destinations.append(
            (destination.hostname, destination.addresses)
        )
        if len(transport_destinations) == 1:
            return _SafeResponse(
                b"",
                status=302,
                headers={"Location": "https://other.example/final"},
            )
        return _SafeResponse(
            b"cross-host-ok",
            headers={"Content-Type": "text/plain"},
        )

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_cross_host)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "https://public.example/start"},
        ToolContext(cwd="."),
    )

    assert result.ok is True
    assert transport_destinations == [
        ("public.example", ("93.184.216.34",)),
        ("other.example", ("93.184.216.35",)),
    ]
    assert result.output.endswith("cross-host-ok")


def test_public_ipv6_dns_answer_is_preserved_as_the_pinned_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned_addresses: list[tuple[str, ...]] = []

    class _Ipv6Resolver:
        def resolve(
            self,
            _hostname: str,
            port: int,
            *,
            deadline: float,
        ) -> list[tuple[object, ...]]:
            del deadline
            return [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("2606:2800:220:1:248:1893:25c8:1946", port, 0, 0),
                )
            ]

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _Ipv6Resolver())

    def open_ipv6(request: object, *, timeout: float) -> _SafeResponse:
        del timeout
        destination = getattr(request, "_minicode_destination")
        pinned_addresses.append(destination.addresses)
        return _SafeResponse(
            b"ipv6-ok",
            headers={"Content-Type": "text/plain"},
        )

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_ipv6)

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {"url": "https://ipv6.example/"},
        ToolContext(cwd="."),
    )

    assert result.ok is True
    assert pinned_addresses == [
        ("2606:2800:220:1:248:1893:25c8:1946",)
    ]


def test_max_chars_applies_after_rendering_with_truthful_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = ("a" * 100 + "truncated-fixture-secret").encode()
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: _SafeResponse(
            payload,
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Length": str(len(payload)),
            },
        ),
    )

    result = ToolRegistry([web_fetch_tool]).execute(
        "web_fetch",
        {
            "url": "http://93.184.216.34/truncate",
            "max_chars": 100,
        },
        ToolContext(cwd="."),
    )

    assert result.ok is True
    assert f"WIRE_BYTES: {len(payload)}" in result.output
    assert f"RENDERED_CHARS: {len(payload)}" in result.output
    assert "TRUNCATED: yes" in result.output
    assert "Content truncated at 100 chars" in result.output
    assert "truncated-fixture-secret" not in result.output


def test_web_fetch_remains_the_read_only_core_profile_tool(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINI_CODE_TOOL_PROFILE", raising=False)
    registry = create_default_tool_registry(
        str(tmp_path),
        runtime={"toolProfile": "core"},
    )
    try:
        registered = registry.find("web_fetch")
        assert registered is web_fetch_tool
        assert registered.is_read_only is True
        assert registered.input_schema["additionalProperties"] is False
        assert registered.input_schema["properties"]["max_chars"] == {
            "type": "integer",
            "minimum": 100,
            "maximum": 50_000,
            "description": "Maximum characters to return (default: 10000)",
        }
        assert registry.find("http_request") is None
    finally:
        registry.dispose()
