from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import socket
import time
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from minicode.tools import http_utils, network_safety
from minicode.tools.http_utils import SafeHttpResponse
from minicode.tools.network_safety import NetworkSafetyError
from minicode.tools.search_providers import (
    DEFAULT_PROVIDER_ORDER,
    SearchProviderOutcome,
    SearchProviderStatus,
    SearchResult,
    load_provider_order,
    parse_provider_html,
    project_result_url,
    search_provider,
)


FIXTURES = Path(__file__).parent / "fixtures" / "web_search"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _response(
    payload: str | bytes,
    *,
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
    content_encoding: str = "identity",
) -> SafeHttpResponse:
    return SafeHttpResponse(
        status=status,
        content_type=content_type,
        content_encoding=content_encoding,
        payload=payload.encode("utf-8") if isinstance(payload, str) else payload,
    )


def test_provider_types_are_immutable_and_enforce_outcome_invariants() -> None:
    result = SearchResult(
        title="title",
        url="https://example.test/",
        snippet="snippet",
        provider="baidu",
    )
    outcome = SearchProviderOutcome(
        "baidu",
        SearchProviderStatus.SUCCESS,
        (result,),
    )

    with pytest.raises(FrozenInstanceError):
        result.title = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        outcome.status = SearchProviderStatus.NO_RESULTS  # type: ignore[misc]
    with pytest.raises(ValueError):
        SearchProviderOutcome("baidu", SearchProviderStatus.SUCCESS)
    with pytest.raises(ValueError):
        SearchProviderOutcome(
            "baidu",
            SearchProviderStatus.NO_RESULTS,
            (result,),
        )


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, DEFAULT_PROVIDER_ORDER),
        (
            {"MINI_CODE_WEB_SEARCH_PROVIDERS": "baidu,duckduckgo"},
            ("baidu", "duckduckgo"),
        ),
        (
            {"MINI_CODE_WEB_SEARCH_PROVIDERS": "duckduckgo,baidu"},
            ("duckduckgo", "baidu"),
        ),
        ({"MINI_CODE_WEB_SEARCH_PROVIDERS": "baidu"}, ("baidu",)),
        (
            {"MINI_CODE_WEB_SEARCH_PROVIDERS": "duckduckgo"},
            ("duckduckgo",),
        ),
    ],
)
def test_provider_order_is_closed_and_deterministic(
    environment: dict[str, str],
    expected: tuple[str, ...],
) -> None:
    assert load_provider_order(environment) == expected


@pytest.mark.parametrize(
    "configured",
    [
        "",
        "unknown",
        "baidu,baidu",
        "baidu,duckduckgo,baidu",
        " baidu",
        "baidu ",
        "baidu, duckduckgo",
        "BAIDU",
        "Baidu",
        ",baidu",
        "baidu,",
    ],
)
def test_provider_order_rejects_invalid_values(configured: str) -> None:
    with pytest.raises(ValueError, match="provider_config_invalid"):
        load_provider_order(
            {"MINI_CODE_WEB_SEARCH_PROVIDERS": configured}
        )


@pytest.mark.parametrize(
    ("provider", "fixture_name", "query_key"),
    [
        ("baidu", "baidu_results.html", "wd"),
        ("duckduckgo", "duckduckgo_results.html", "q"),
        ("bing", "bing_results.html", "q"),
    ],
)
def test_provider_request_is_fixed_https_encoded_and_header_bounded(
    provider: str,
    fixture_name: str,
    query_key: str,
) -> None:
    requests = []
    query = "中文 & fixture?/secret"

    def transport(request, *, deadline: float) -> SafeHttpResponse:
        assert deadline > time.monotonic()
        requests.append(request)
        return _response(_fixture(fixture_name))

    outcome = search_provider(
        provider,
        query,
        2,
        deadline=time.monotonic() + 5,
        transport=transport,
    )

    assert outcome.status is SearchProviderStatus.SUCCESS
    assert len(requests) == 1
    request = requests[0]
    parsed = urlsplit(request.url)
    assert parsed.scheme == "https"
    assert parsed.hostname == {
        "baidu": "www.baidu.com",
        "duckduckgo": "html.duckduckgo.com",
        "bing": "www.bing.com",
    }[provider]
    assert parse_qs(parsed.query)[query_key] == [query]
    headers = {name.casefold(): value for name, value in request.headers}
    assert headers["accept-encoding"] == "identity"
    assert "text/html" in headers["accept"]
    assert headers["user-agent"].startswith("MiniCode-Python/")
    assert not {
        "authorization",
        "cookie",
        "referer",
        "x-api-key",
        "api-key",
    } & set(headers)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, SearchProviderStatus.SUCCESS),
        (403, SearchProviderStatus.FORBIDDEN),
        (404, SearchProviderStatus.FORBIDDEN),
        (418, SearchProviderStatus.FORBIDDEN),
        (429, SearchProviderStatus.RATE_LIMITED),
        (500, SearchProviderStatus.SERVER_ERROR),
        (502, SearchProviderStatus.SERVER_ERROR),
        (503, SearchProviderStatus.SERVER_ERROR),
    ],
)
def test_provider_projects_final_http_status_without_body_leakage(
    status: int,
    expected: SearchProviderStatus,
) -> None:
    body = (
        _fixture("baidu_results.html")
        if status == 200
        else "Bearer status-body-fixture-secret"
    )
    outcome = search_provider(
        "baidu",
        "query-fixture-secret",
        2,
        deadline=time.monotonic() + 5,
        transport=lambda _request, *, deadline: _response(
            body,
            status=status,
        ),
    )

    assert outcome.status is expected
    if status != 200:
        assert outcome.results == ()
        assert "fixture-secret" not in repr(outcome)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("timeout", SearchProviderStatus.TIMEOUT),
        ("dns_error", SearchProviderStatus.DNS_ERROR),
        ("network_unavailable", SearchProviderStatus.NETWORK_UNAVAILABLE),
        ("resolver_busy", SearchProviderStatus.NETWORK_UNAVAILABLE),
        ("response_too_large", SearchProviderStatus.RESPONSE_TOO_LARGE),
        ("redirect_blocked", SearchProviderStatus.REDIRECT_BLOCKED),
        ("destination_blocked", SearchProviderStatus.REDIRECT_BLOCKED),
        ("tls_error", SearchProviderStatus.TLS_ERROR),
        (
            "unsupported_response_type",
            SearchProviderStatus.RESPONSE_UNRECOGNIZED,
        ),
    ],
)
def test_provider_projects_safe_transport_errors(
    code: str,
    expected: SearchProviderStatus,
) -> None:
    def transport(_request, *, deadline: float) -> SafeHttpResponse:
        del deadline
        raise NetworkSafetyError(code)

    outcome = search_provider(
        "duckduckgo",
        "private-query",
        2,
        deadline=time.monotonic() + 5,
        transport=transport,
    )

    assert outcome.status is expected
    assert outcome.results == ()


@pytest.mark.parametrize(
    ("content_type", "content_encoding"),
    [
        ("application/octet-stream", "identity"),
        ("application/json", "identity"),
        ("text/plain", "identity"),
        ("text/html", "gzip"),
        ("text/html", "br"),
    ],
)
def test_provider_rejects_unrecognized_or_encoded_responses(
    content_type: str,
    content_encoding: str,
) -> None:
    outcome = search_provider(
        "baidu",
        "fixture",
        2,
        deadline=time.monotonic() + 5,
        transport=lambda _request, *, deadline: _response(
            "response-fixture-secret",
            content_type=content_type,
            content_encoding=content_encoding,
        ),
    )

    assert outcome.status is SearchProviderStatus.RESPONSE_UNRECOGNIZED
    assert outcome.results == ()


def test_provider_deadline_exhaustion_sends_nothing() -> None:
    sends = 0

    def transport(_request, *, deadline: float) -> SafeHttpResponse:
        nonlocal sends
        del deadline
        sends += 1
        return _response(_fixture("baidu_results.html"))

    outcome = search_provider(
        "baidu",
        "fixture",
        2,
        deadline=time.monotonic() - 0.01,
        transport=transport,
    )

    assert outcome.status is SearchProviderStatus.TIMEOUT
    assert sends == 0


@pytest.mark.parametrize(
    ("provider", "fixture_name", "expected"),
    [
        ("baidu", "baidu_results.html", SearchProviderStatus.SUCCESS),
        ("baidu", "baidu_empty.html", SearchProviderStatus.NO_RESULTS),
        ("baidu", "baidu_challenge.html", SearchProviderStatus.CHALLENGE),
        (
            "baidu",
            "baidu_changed.html",
            SearchProviderStatus.RESPONSE_UNRECOGNIZED,
        ),
        (
            "duckduckgo",
            "duckduckgo_results.html",
            SearchProviderStatus.SUCCESS,
        ),
        (
            "duckduckgo",
            "duckduckgo_empty.html",
            SearchProviderStatus.NO_RESULTS,
        ),
        (
            "duckduckgo",
            "duckduckgo_challenge.html",
            SearchProviderStatus.CHALLENGE,
        ),
        (
            "duckduckgo",
            "duckduckgo_changed.html",
            SearchProviderStatus.RESPONSE_UNRECOGNIZED,
        ),
        ("bing", "bing_results.html", SearchProviderStatus.SUCCESS),
        ("bing", "bing_empty.html", SearchProviderStatus.NO_RESULTS),
        ("bing", "bing_challenge.html", SearchProviderStatus.CHALLENGE),
        (
            "bing",
            "bing_changed.html",
            SearchProviderStatus.RESPONSE_UNRECOGNIZED,
        ),
    ],
)
def test_provider_specific_parser_classifies_page_shapes(
    provider: str,
    fixture_name: str,
    expected: SearchProviderStatus,
) -> None:
    outcome = parse_provider_html(provider, _fixture(fixture_name), 5)

    assert outcome.status is expected


def test_baidu_parser_normalizes_entities_fragments_and_provider_links() -> None:
    outcome = parse_provider_html(
        "baidu",
        _fixture("baidu_results.html"),
        5,
    )

    assert outcome.status is SearchProviderStatus.SUCCESS
    assert [result.title for result in outcome.results] == [
        "中文 & English",
        "Second result",
    ]
    assert outcome.results[0].snippet == "第一条 fixture 摘要"
    assert outcome.results[0].provider_link is True
    assert outcome.results[1].url == "https://example.test/direct"


def test_bing_parser_extracts_titles_urls_and_marks_provider_links() -> None:
    outcome = parse_provider_html(
        "bing",
        _fixture("bing_results.html"),
        5,
    )

    assert outcome.status is SearchProviderStatus.SUCCESS
    assert [result.title for result in outcome.results] == [
        "中文 & English",
        "Second result",
    ]
    assert outcome.results[0].url == "https://example.test/one"
    assert outcome.results[0].snippet == "第一条 fixture 摘要"
    assert outcome.results[0].provider_link is False
    assert outcome.results[1].provider_link is True


def test_duckduckgo_parser_decodes_redirect_and_normalizes_entities() -> None:
    outcome = parse_provider_html(
        "duckduckgo",
        _fixture("duckduckgo_results.html"),
        5,
    )

    assert outcome.status is SearchProviderStatus.SUCCESS
    assert [result.title for result in outcome.results] == [
        "One & entity",
        "Second result",
    ]
    assert outcome.results[0].url == "https://example.test/one"
    assert outcome.results[0].snippet == "First fixture snippet"


@pytest.mark.parametrize("provider", ["baidu", "duckduckgo"])
def test_parser_allows_missing_snippet(provider: str) -> None:
    if provider == "baidu":
        html = (
            '<div class="result"><h3><a href="https://example.test/">'
            "Title</a></h3></div>"
        )
    else:
        html = (
            '<div class="result"><a class="result__a" '
            'href="https://example.test/">Title</a></div>'
        )

    outcome = parse_provider_html(provider, html, 1)

    assert outcome.status is SearchProviderStatus.SUCCESS
    assert outcome.results[0].snippet == ""


@pytest.mark.parametrize("provider", ["baidu", "duckduckgo"])
def test_parser_deduplicates_urls_and_honors_result_limit(provider: str) -> None:
    if provider == "baidu":
        block = (
            '<div class="result"><h3><a href="{url}">{title}</a></h3>'
            '<div class="c-abstract">snippet</div></div>'
        )
    else:
        block = (
            '<div class="result"><a class="result__a" href="{url}">{title}</a>'
            '<a class="result__snippet">snippet</a></div>'
        )
    html = "".join(
        [
            block.format(url="https://example.test/one", title="One"),
            block.format(
                url="https://example.test/one#duplicate",
                title="Duplicate",
            ),
            block.format(url="https://example.test/two", title="Two"),
            block.format(url="https://example.test/three", title="Three"),
        ]
    )

    outcome = parse_provider_html(provider, html, 2)

    assert outcome.status is SearchProviderStatus.SUCCESS
    assert [result.title for result in outcome.results] == ["One", "Two"]


@pytest.mark.parametrize("provider", ["baidu", "duckduckgo"])
def test_parser_bounds_title_and_snippet_and_drops_controls(provider: str) -> None:
    title = "T" * 299 + "\x85" + "T" * 50 + "\x00"
    snippet = "S" * 599 + "\x85" + "S" * 100 + "\x07"
    if provider == "baidu":
        html = (
            '<div class="result"><h3><a href="https://example.test/">'
            f"{title}</a></h3><div class=\"c-abstract\">{snippet}</div></div>"
        )
    else:
        html = (
            '<div class="result"><a class="result__a" '
            f'href="https://example.test/">{title}</a>'
            f'<a class="result__snippet">{snippet}</a></div>'
        )

    outcome = parse_provider_html(provider, html, 1)

    assert outcome.status is SearchProviderStatus.SUCCESS
    assert 0 < len(outcome.results[0].title) <= 300
    assert 0 < len(outcome.results[0].snippet) <= 600
    assert "\x00" not in outcome.results[0].title
    assert "\x85" not in outcome.results[0].title
    assert "\x07" not in outcome.results[0].snippet
    assert "\x85" not in outcome.results[0].snippet


@pytest.mark.parametrize("provider", ["baidu", "duckduckgo"])
def test_parser_does_not_treat_unrelated_or_malformed_empty_html_as_no_results(
    provider: str,
) -> None:
    for html in (
        "<html><body>ordinary unrelated page</body></html>",
        "<div><span>malformed but unknown",
        "",
    ):
        outcome = parse_provider_html(provider, html, 3)
        assert outcome.status is SearchProviderStatus.RESPONSE_UNRECOGNIZED


@pytest.mark.parametrize(
    ("raw_url", "provider", "expected"),
    [
        (
            "HTTPS://BÜCHER.example/path#fragment",
            "baidu",
            "https://xn--bcher-kva.example/path",
        ),
        (
            "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fsafe%23x",
            "duckduckgo",
            "https://example.test/safe",
        ),
        (
            "https://www.baidu.com/link?url=opaque",
            "baidu",
            "https://www.baidu.com/link?url=opaque",
        ),
    ],
)
def test_result_url_projection_normalizes_safe_urls(
    raw_url: str,
    provider: str,
    expected: str,
) -> None:
    projected = project_result_url(raw_url, provider=provider)

    assert projected is not None
    assert projected[0] == expected


@pytest.mark.parametrize(
    "raw_url",
    [
        "javascript:alert(1)",
        "data:text/plain,secret",
        "file:///private/secret",
        "https://user:pass@example.test/",
        "https://localhost/",
        "https://sub.localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.8/",
        "http://169.254.1.1/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "https://example.test/\nsecret",
        "https://example.test/safe\n",
        "https://example.test/\x85secret",
        "https://example.test/" + "a" * 4_100,
    ],
)
def test_result_url_projection_rejects_unsafe_urls(raw_url: str) -> None:
    assert project_result_url(raw_url, provider="baidu") is None


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://duckduckgo.com/l/",
        "https://duckduckgo.com/l/?uddg=",
        "https://duckduckgo.com/l/?uddg=javascript%3Aalert%281%29",
        (
            "https://duckduckgo.com/l/?"
            "uddg=https%3A%2F%2Fexample.test%2Funsafe%C2%85tail"
        ),
        (
            "https://duckduckgo.com/l/?"
            "uddg=https%3A%2F%2Fexample.test%2Fa&"
            "uddg=https%3A%2F%2Fexample.test%2Fb"
        ),
    ],
)
def test_duckduckgo_redirect_without_one_safe_target_is_rejected(
    raw_url: str,
) -> None:
    assert project_result_url(raw_url, provider="duckduckgo") is None


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


class _StreamResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int,
        declared: bool = True,
    ) -> None:
        self.payload = payload
        self.offset = 0
        self.status = status
        self.read_sizes: list[int] = []
        self.headers = {
            "Content-Type": "text/html",
            "Content-Encoding": "identity",
        }
        if declared:
            self.headers["Content-Length"] = str(len(payload))

    def read(self, size: int) -> bytes:
        if size < 0:
            raise AssertionError("unbounded read")
        self.read_sizes.append(size)
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def __enter__(self) -> "_StreamResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.mark.parametrize("status", [403, 429, 503])
def test_get_only_status_seam_preserves_status_while_existing_wrapper_does_not(
    status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _PublicResolver())
    responses: list[_StreamResponse] = []

    def open_response(_request, *, timeout: float) -> _StreamResponse:
        assert timeout > 0
        response = _StreamResponse(b"status-body-secret", status=status)
        responses.append(response)
        return response

    monkeypatch.setattr(http_utils, "_open_no_redirect", open_response)
    request = network_safety.normalize_http_request(
        {
            "url": "https://public.example/search",
            "method": "GET",
            "timeout": 2,
        }
    )

    observed = http_utils.execute_safe_get_response(
        request,
        deadline=time.monotonic() + 2,
    )
    with pytest.raises(NetworkSafetyError, match="http_error"):
        http_utils.execute_safe_get(
            request,
            deadline=time.monotonic() + 2,
        )

    assert observed.status == status
    assert observed.payload == b"status-body-secret"
    assert all(
        max(response.read_sizes)
        <= network_safety.RESPONSE_READ_CHUNK_BYTES
        for response in responses
    )


def test_get_only_status_seam_bounds_http_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _PublicResolver())
    response = _StreamResponse(
        b"x" * (network_safety.MAX_RESPONSE_BYTES + 1),
        status=503,
        declared=False,
    )
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: response,
    )
    request = network_safety.normalize_http_request(
        {
            "url": "https://public.example/search",
            "method": "GET",
            "timeout": 2,
        }
    )

    with pytest.raises(NetworkSafetyError, match="response_too_large"):
        http_utils.execute_safe_get_response(
            request,
            deadline=time.monotonic() + 2,
        )

    assert response.read_sizes
    assert max(response.read_sizes) <= network_safety.RESPONSE_READ_CHUNK_BYTES


class _AddressResolver:
    def __init__(self, addresses: list[str]) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    def resolve(
        self,
        hostname: str,
        port: int,
        *,
        deadline: float,
    ) -> list[tuple[object, ...]]:
        assert deadline > time.monotonic()
        self.calls.append((hostname, port))
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port, 0, 0)
                if ":" in address
                else (address, port),
            )
            for address in self.addresses
        ]


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.8"],
        ["169.254.1.1"],
        ["::1"],
        ["fc00::1"],
        ["93.184.216.34", "10.0.0.8"],
    ],
)
def test_provider_private_or_mixed_dns_is_blocked_before_transport(
    addresses: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _AddressResolver(addresses)
    sends = 0

    def forbidden_open(_request, *, timeout: float):
        nonlocal sends
        del timeout
        sends += 1
        raise AssertionError("unsafe destination reached transport")

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    monkeypatch.setattr(http_utils, "_open_no_redirect", forbidden_open)

    outcome = search_provider(
        "baidu",
        "private-dns-fixture",
        2,
        deadline=time.monotonic() + 5,
    )

    assert outcome.status is SearchProviderStatus.REDIRECT_BLOCKED
    assert len(resolver.calls) == 1
    assert sends == 0


def test_provider_uses_one_resolution_and_passes_pinned_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _AddressResolver(["93.184.216.34"])
    observed = []

    def open_pinned(request, *, timeout: float) -> _StreamResponse:
        assert timeout > 0
        observed.append(getattr(request, "_minicode_destination", None))
        return _StreamResponse(
            _fixture("baidu_results.html").encode(),
            status=200,
        )

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    monkeypatch.setattr(http_utils, "_open_no_redirect", open_pinned)

    outcome = search_provider(
        "baidu",
        "pinning-fixture",
        2,
        deadline=time.monotonic() + 5,
    )

    assert outcome.status is SearchProviderStatus.SUCCESS
    assert resolver.calls == [("www.baidu.com", 443)]
    assert len(observed) == 1
    assert observed[0].hostname == "www.baidu.com"
    assert observed[0].addresses == ("93.184.216.34",)
    assert observed[0].scheme == "https"


def test_provider_private_redirect_target_is_blocked_without_target_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _AddressResolver(["93.184.216.34"])
    sends: list[str] = []

    def redirect_private(request, *, timeout: float) -> _StreamResponse:
        del timeout
        sends.append(request.full_url)
        response = _StreamResponse(b"", status=302)
        response.headers["Location"] = (
            "http://127.0.0.1/private?redirect-location-fixture-secret"
        )
        return response

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    monkeypatch.setattr(http_utils, "_open_no_redirect", redirect_private)

    outcome = search_provider(
        "duckduckgo",
        "redirect-query-fixture-secret",
        2,
        deadline=time.monotonic() + 5,
    )

    assert outcome.status is SearchProviderStatus.REDIRECT_BLOCKED
    assert len(sends) == 1
    assert sends[0].startswith("https://html.duckduckgo.com/")
    assert len(resolver.calls) == 1
    assert "fixture-secret" not in repr(outcome)


def test_provider_redirect_loop_is_bounded_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _AddressResolver(["93.184.216.34"])
    sends = 0

    def redirect_loop(request, *, timeout: float) -> _StreamResponse:
        nonlocal sends
        del timeout
        sends += 1
        response = _StreamResponse(b"", status=302)
        response.headers["Location"] = request.full_url
        return response

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    monkeypatch.setattr(http_utils, "_open_no_redirect", redirect_loop)

    outcome = search_provider(
        "baidu",
        "loop-fixture",
        2,
        deadline=time.monotonic() + 5,
    )

    assert outcome.status is SearchProviderStatus.REDIRECT_BLOCKED
    assert sends == 1
    assert len(resolver.calls) == 2


def test_provider_fourth_redirect_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _AddressResolver(["93.184.216.34"])
    sends: list[str] = []

    def redirect_chain(request, *, timeout: float) -> _StreamResponse:
        del timeout
        sends.append(request.full_url)
        response = _StreamResponse(b"", status=302)
        response.headers["Location"] = f"/hop-{len(sends)}"
        return response

    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    monkeypatch.setattr(http_utils, "_open_no_redirect", redirect_chain)

    outcome = search_provider(
        "duckduckgo",
        "redirect-limit",
        2,
        deadline=time.monotonic() + 5,
    )

    assert outcome.status is SearchProviderStatus.REDIRECT_BLOCKED
    assert len(sends) == 4
    assert len(resolver.calls) == 4


@pytest.mark.parametrize(
    ("payload_size", "declared", "expected"),
    [
        (
            network_safety.MAX_RESPONSE_BYTES,
            True,
            SearchProviderStatus.RESPONSE_UNRECOGNIZED,
        ),
        (
            network_safety.MAX_RESPONSE_BYTES + 1,
            True,
            SearchProviderStatus.RESPONSE_TOO_LARGE,
        ),
        (
            network_safety.MAX_RESPONSE_BYTES + 1,
            False,
            SearchProviderStatus.RESPONSE_TOO_LARGE,
        ),
    ],
)
def test_provider_response_wire_budget_and_read_size(
    payload_size: int,
    declared: bool,
    expected: SearchProviderStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _AddressResolver(["93.184.216.34"])
    response = _StreamResponse(
        b"x" * payload_size,
        status=200,
        declared=declared,
    )
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: response,
    )

    outcome = search_provider(
        "baidu",
        "response-budget",
        1,
        deadline=time.monotonic() + 5,
    )

    assert outcome.status is expected
    if declared and payload_size > network_safety.MAX_RESPONSE_BYTES:
        assert response.read_sizes == []
    else:
        assert response.read_sizes
        assert max(response.read_sizes) <= network_safety.RESPONSE_READ_CHUNK_BYTES


def test_final_status_seam_bounds_and_closes_urllib_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", _PublicResolver())
    reads: list[int] = []
    closed = False

    class StatusError(urllib.error.HTTPError):
        def __init__(self) -> None:
            super().__init__(
                "https://public.example/",
                429,
                "private-reason-fixture-secret",
                {
                    "Content-Type": "text/html",
                    "Content-Encoding": "identity",
                },
                None,
            )
            self.offset = 0
            self.payload = b"private-body-fixture-secret"

        def read(self, size: int = -1) -> bytes:
            if size < 0:
                raise AssertionError("unbounded HTTPError read")
            reads.append(size)
            chunk = self.payload[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(
        http_utils,
        "_open_no_redirect",
        lambda _request, *, timeout: (_ for _ in ()).throw(StatusError()),
    )
    request = network_safety.normalize_http_request(
        {
            "url": "https://public.example/",
            "method": "GET",
            "timeout": 2,
        }
    )

    response = http_utils.execute_safe_get_response(
        request,
        deadline=time.monotonic() + 2,
    )

    assert response.status == 429
    assert response.payload == b"private-body-fixture-secret"
    assert closed is True
    assert reads
    assert max(reads) <= network_safety.RESPONSE_READ_CHUNK_BYTES


def test_non_get_status_seam_fails_before_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _AddressResolver(["93.184.216.34"])
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)
    request = network_safety.normalize_http_request(
        {
            "url": "https://public.example/",
            "method": "POST",
            "body": "fixture",
            "timeout": 2,
        }
    )

    with pytest.raises(NetworkSafetyError, match="invalid_request"):
        http_utils.execute_safe_get_response(
            request,
            deadline=time.monotonic() + 2,
        )

    assert resolver.calls == []
