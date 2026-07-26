from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from minicode.tooling import ToolContext, ToolRegistry
from minicode.tools import search_providers
from minicode.tools.http_utils import SafeHttpResponse
from minicode.tools.network_safety import NetworkSafetyError
from minicode.tools.search_providers import (
    SearchProviderOutcome,
    SearchProviderStatus,
    SearchResult,
)
from minicode.tools import web_search as web_search_module
from minicode.tools.web_search import (
    MAX_QUERY_BYTES,
    MAX_QUERY_CHARS,
    MAX_RESULTS,
    web_search_tool,
)


def _execute(input_data: object):
    return ToolRegistry([web_search_tool]).execute(
        "web_search",
        input_data,
        ToolContext(cwd="/private/workspace-fixture-secret"),
    )


def _success(provider: str, count: int = 1) -> SearchProviderOutcome:
    return SearchProviderOutcome(
        provider,
        SearchProviderStatus.SUCCESS,
        tuple(
            SearchResult(
                title=f"Title {index}",
                url=f"https://example.test/{index}",
                snippet=f"Snippet {index}",
                provider=provider,
            )
            for index in range(1, count + 1)
        ),
    )


def test_web_search_falls_back_once_without_echoing_query(
    monkeypatch,
) -> None:
    calls: list[str] = []
    private_query = "fallback-fixture-secret"
    duckduckgo_html = b"""
    <html><body>
      <div class="result">
        <h2><a class="result__a"
          href="https://example.test/result">Fixture title</a></h2>
        <a class="result__snippet">Fixture snippet</a>
      </div>
    </body></html>
    """

    def fake_transport(request, *, deadline: float) -> SafeHttpResponse:
        del deadline
        hostname = urlsplit(request.url).hostname
        calls.append(str(hostname))
        if hostname == "www.baidu.com":
            raise NetworkSafetyError("timeout")
        return SafeHttpResponse(
            status=200,
            content_type="text/html; charset=utf-8",
            content_encoding="identity",
            payload=duckduckgo_html,
        )

    monkeypatch.setenv(
        "MINI_CODE_WEB_SEARCH_PROVIDERS",
        "baidu,duckduckgo",
    )
    monkeypatch.setattr(
        search_providers,
        "execute_safe_get_response",
        fake_transport,
    )

    result = ToolRegistry([web_search_tool]).execute(
        "web_search",
        {"query": private_query, "num_results": 3},
        ToolContext(cwd="."),
    )

    assert calls == ["www.baidu.com", "html.duckduckgo.com"]
    assert result.ok is True
    assert "PROVIDER: duckduckgo" in result.output
    assert "Fixture title" in result.output
    assert private_query not in result.output
    assert "private upstream detail" not in result.output


@pytest.mark.parametrize(
    "input_data",
    [
        None,
        [],
        "query",
        {},
        {"query": None},
        {"query": ""},
        {"query": "   "},
        {"query": "\x00query"},
        {"query": "query\x85"},
        {"query": "query\x7f"},
        {"query": "\nquery"},
        {"query": "query\t"},
        {"query": "x" * (MAX_QUERY_CHARS + 1)},
        {"query": "\ud800"},
        {"query": "secret-query", "num_results": 0},
        {"query": "secret-query", "num_results": MAX_RESULTS + 1},
        {"query": "secret-query", "num_results": True},
        {"query": "secret-query", "num_results": False},
        {"query": "secret-query", "num_results": 1.0},
        {"query": "secret-query", "num_results": 2.5},
        {"query": "secret-query", "num_results": "5"},
        {"query": "secret-query", "num_results": float("nan")},
        {"query": "secret-query", "num_results": float("inf")},
        {"query": "secret-query", "extra": "Bearer fixture-secret"},
    ],
)
def test_invalid_input_is_fixed_and_never_echoed(input_data: object) -> None:
    result = _execute(input_data)

    assert result.ok is False
    assert result.output == (
        "error[invalid_search_request]: The web search request is invalid."
    )
    assert "\n" not in result.output
    assert "secret" not in result.output
    assert "Bearer" not in result.output
    assert "/private/" not in result.output


@pytest.mark.parametrize(
    ("input_data", "expected_query", "expected_count"),
    [
        ({"query": " fixture "}, "fixture", 5),
        ({"query": "x" * MAX_QUERY_CHARS}, "x" * MAX_QUERY_CHARS, 5),
        ({"query": "🙂" * 512}, "🙂" * 512, 5),
        ({"query": "fixture", "num_results": 1}, "fixture", 1),
        (
            {"query": "fixture", "num_results": MAX_RESULTS},
            "fixture",
            MAX_RESULTS,
        ),
    ],
)
def test_valid_input_boundaries_are_normalized(
    input_data: dict[str, object],
    expected_query: str,
    expected_count: int,
) -> None:
    normalized = web_search_tool.validator(input_data)

    assert normalized.query == expected_query
    assert len(normalized.query.encode("utf-8")) <= MAX_QUERY_BYTES
    assert normalized.num_results == expected_count


def test_schema_is_closed_and_uses_integer_count() -> None:
    schema = web_search_tool.input_schema

    assert schema["additionalProperties"] is False
    assert schema["properties"]["num_results"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10,
        "description": "Maximum results to return (default: 5).",
    }
    assert schema["required"] == ["query"]


@pytest.mark.parametrize(
    "configured",
    [
        "",
        "unknown",
        "baidu,baidu",
        "baidu,duckduckgo,baidu",
        " BAIDU",
    ],
)
def test_invalid_provider_config_is_fixed_and_zero_send(
    configured: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sends = 0

    def forbidden_send(*_args, **_kwargs):
        nonlocal sends
        sends += 1
        raise AssertionError("invalid config reached provider")

    monkeypatch.setenv("MINI_CODE_WEB_SEARCH_PROVIDERS", configured)
    monkeypatch.setattr(search_providers, "search_provider", forbidden_send)

    result = _execute({"query": "config-query-fixture-secret"})

    assert sends == 0
    assert result.ok is False
    assert result.output == (
        "error[provider_config_invalid]: "
        "The web search provider configuration is invalid."
    )
    assert "fixture-secret" not in result.output


def test_first_success_stops_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def provider_call(provider: str, *_args, **_kwargs):
        calls.append(provider)
        return _success(provider, 1)

    monkeypatch.delenv("MINI_CODE_WEB_SEARCH_PROVIDERS", raising=False)
    monkeypatch.setattr(search_providers, "search_provider", provider_call)

    result = _execute({"query": "fixture", "num_results": 10})

    assert result.ok is True
    assert calls == ["bing"]
    assert "RESULT_COUNT: 1" in result.output
    assert "baidu" not in result.output
    assert "duckduckgo" not in result.output


@pytest.mark.parametrize(
    "first_status",
    [
        SearchProviderStatus.NO_RESULTS,
        SearchProviderStatus.TIMEOUT,
        SearchProviderStatus.DNS_ERROR,
        SearchProviderStatus.NETWORK_UNAVAILABLE,
        SearchProviderStatus.FORBIDDEN,
        SearchProviderStatus.RATE_LIMITED,
        SearchProviderStatus.SERVER_ERROR,
        SearchProviderStatus.CHALLENGE,
        SearchProviderStatus.RESPONSE_UNRECOGNIZED,
        SearchProviderStatus.RESPONSE_TOO_LARGE,
        SearchProviderStatus.REDIRECT_BLOCKED,
        SearchProviderStatus.TLS_ERROR,
    ],
)
def test_each_first_provider_non_success_falls_back_once(
    first_status: SearchProviderStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def provider_call(provider: str, *_args, **_kwargs):
        calls.append(provider)
        if provider == "bing":
            return SearchProviderOutcome(provider, first_status)
        return _success(provider, 2)

    monkeypatch.delenv("MINI_CODE_WEB_SEARCH_PROVIDERS", raising=False)
    monkeypatch.setattr(search_providers, "search_provider", provider_call)

    result = _execute({"query": "fallback-private-query", "num_results": 5})

    assert result.ok is True
    assert calls == ["bing", "baidu"]
    assert "PROVIDER: baidu" in result.output
    assert "RESULT_COUNT: 2" in result.output
    assert first_status.value not in result.output
    assert "fallback-private-query" not in result.output


@pytest.mark.parametrize(
    ("statuses", "expected_code"),
    [
        (
            [
                SearchProviderStatus.NO_RESULTS,
                SearchProviderStatus.NO_RESULTS,
                SearchProviderStatus.NO_RESULTS,
            ],
            "no_results",
        ),
        (
            [
                SearchProviderStatus.NO_RESULTS,
                SearchProviderStatus.TIMEOUT,
                SearchProviderStatus.TIMEOUT,
            ],
            "search_incomplete",
        ),
        (
            [
                SearchProviderStatus.CHALLENGE,
                SearchProviderStatus.RESPONSE_UNRECOGNIZED,
                SearchProviderStatus.TIMEOUT,
            ],
            "search_unavailable",
        ),
        (
            [
                SearchProviderStatus.FORBIDDEN,
                SearchProviderStatus.RATE_LIMITED,
                SearchProviderStatus.SERVER_ERROR,
            ],
            "search_unavailable",
        ),
    ],
)
def test_terminal_failure_taxonomy_is_truthful_and_redacted(
    statuses: list[SearchProviderStatus],
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def provider_call(provider: str, *_args, **_kwargs):
        index = len(calls)
        calls.append(provider)
        return SearchProviderOutcome(provider, statuses[index])

    monkeypatch.delenv("MINI_CODE_WEB_SEARCH_PROVIDERS", raising=False)
    monkeypatch.setattr(search_providers, "search_provider", provider_call)

    result = _execute({"query": "Bearer query-fixture-secret"})

    assert result.ok is False
    assert result.output.startswith(f"error[{expected_code}]: ")
    assert calls == ["bing", "baidu", "duckduckgo"]
    assert (
        f"bing={statuses[0].value}, baidu={statuses[1].value}, "
        f"duckduckgo={statuses[2].value}"
        in result.output
    )
    assert "fixture-secret" not in result.output
    assert "Bearer" not in result.output
    assert "/private/" not in result.output


def test_total_deadline_stops_before_second_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    clock_values = iter([0.0, 9.0, 15.0])

    def clock() -> float:
        return next(clock_values)

    def provider_call(provider: str, *_args, **_kwargs):
        calls.append(provider)
        return SearchProviderOutcome(provider, SearchProviderStatus.TIMEOUT)

    monkeypatch.delenv("MINI_CODE_WEB_SEARCH_PROVIDERS", raising=False)
    monkeypatch.setattr(web_search_module.time, "monotonic", clock)
    monkeypatch.setattr(search_providers, "search_provider", provider_call)

    result = _execute({"query": "deadline-private-query"})

    assert result.ok is False
    assert result.output.startswith("error[search_timeout]: ")
    assert calls == ["bing"]
    assert "deadline-private-query" not in result.output


def test_each_provider_receives_at_most_six_seconds_of_shared_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float]] = []
    clock_values = iter([100.0, 100.0, 101.0, 101.0, 102.0, 102.0, 103.0])

    def clock() -> float:
        return next(clock_values)

    def provider_call(
        provider: str,
        _query: str,
        _count: int,
        *,
        deadline: float,
    ) -> SearchProviderOutcome:
        calls.append((provider, deadline))
        return SearchProviderOutcome(
            provider,
            SearchProviderStatus.NO_RESULTS,
        )

    monkeypatch.delenv("MINI_CODE_WEB_SEARCH_PROVIDERS", raising=False)
    monkeypatch.setattr(web_search_module.time, "monotonic", clock)
    monkeypatch.setattr(search_providers, "search_provider", provider_call)

    result = _execute({"query": "budget-fixture"})

    assert result.output.startswith("error[no_results]: ")
    assert calls == [("bing", 106.0), ("baidu", 107.0), ("duckduckgo", 108.0)]
    assert all(deadline <= 115.0 for _, deadline in calls)


def test_configured_order_and_single_provider_are_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def provider_call(provider: str, *_args, **_kwargs):
        calls.append(provider)
        return SearchProviderOutcome(provider, SearchProviderStatus.NO_RESULTS)

    monkeypatch.setenv("MINI_CODE_WEB_SEARCH_PROVIDERS", "duckduckgo")
    monkeypatch.setattr(search_providers, "search_provider", provider_call)

    result = _execute({"query": "fixture"})

    assert result.ok is False
    assert result.output.startswith("error[no_results]: ")
    assert calls == ["duckduckgo"]


def test_core_and_full_profiles_register_the_same_tool_once(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minicode.tools import create_default_tool_registry

    monkeypatch.setattr("minicode.tools.discover_skills", lambda _cwd: [])
    monkeypatch.setattr(
        "minicode.tools.create_mcp_backed_tools",
        lambda **_kwargs: {"tools": [], "servers": [], "dispose": lambda: None},
    )

    for profile in ("core", "full"):
        registry = create_default_tool_registry(
            str(tmp_path),
            runtime={"toolProfile": profile},
        )
        try:
            registered = [
                tool for tool in registry.list() if tool.name == "web_search"
            ]
            assert registered == [web_search_tool]
        finally:
            registry.dispose()
