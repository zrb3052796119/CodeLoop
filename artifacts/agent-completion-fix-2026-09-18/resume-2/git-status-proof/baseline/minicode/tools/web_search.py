"""Built-in bounded web search Tool over the fixed HTML provider chain."""

from __future__ import annotations

from dataclasses import dataclass
import time
import unicodedata

from minicode.tooling import ToolDefinition, ToolResult
from minicode.tools import search_providers
from minicode.tools.search_providers import SearchProviderStatus, SearchResult


MAX_RESULTS = 10
MAX_QUERY_CHARS = 512
MAX_QUERY_BYTES = 2_048
SEARCH_TIMEOUT_SECONDS = 15.0
PROVIDER_TIMEOUT_SECONDS = 6.0
_ERROR_MESSAGES = {
    "invalid_search_request": "The web search request is invalid.",
    "provider_config_invalid": "The web search provider configuration is invalid.",
    "no_results": "No web search results were found.",
    "search_timeout": "The web search deadline expired.",
    "search_unavailable": "Web search is unavailable.",
    "search_incomplete": "Web search was incomplete.",
}


class SearchError(ValueError):
    """Low-cardinality search error that never stores request content."""

    _model_safe_tool_output = True

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_MESSAGES else "invalid_search_request"
        super().__init__(self.code)

    def tool_output(self) -> str:
        return f"error[{self.code}]: {_ERROR_MESSAGES[self.code]}"


@dataclass(frozen=True, slots=True)
class NormalizedSearchRequest:
    query: str
    num_results: int


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _validate(input_data: object) -> NormalizedSearchRequest:
    if not isinstance(input_data, dict) or not set(input_data) <= {
        "query",
        "num_results",
    }:
        raise SearchError("invalid_search_request")
    query = input_data.get("query")
    if not isinstance(query, str):
        raise SearchError("invalid_search_request")
    if _has_control_characters(query):
        raise SearchError("invalid_search_request")
    query = query.strip()
    try:
        query_bytes = query.encode("utf-8")
    except UnicodeEncodeError:
        raise SearchError("invalid_search_request") from None
    if (
        not query
        or len(query) > MAX_QUERY_CHARS
        or len(query_bytes) > MAX_QUERY_BYTES
    ):
        raise SearchError("invalid_search_request")
    num_results = input_data.get("num_results", 5)
    if (
        isinstance(num_results, bool)
        or not isinstance(num_results, int)
        or not 1 <= num_results <= MAX_RESULTS
    ):
        raise SearchError("invalid_search_request")
    return NormalizedSearchRequest(query=query, num_results=num_results)


def _format_results(provider: str, results: tuple[SearchResult, ...]) -> str:
    lines = [
        f"PROVIDER: {provider}",
        f"RESULT_COUNT: {len(results)}",
        "",
    ]
    for index, result in enumerate(results, 1):
        link_label = "URL (provider link)" if result.provider_link else "URL"
        lines.extend(
            [
                f"{index}. {result.title}",
                f"   {link_label}: {result.url}",
                f"   SNIPPET: {result.snippet}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _format_failure(
    code: str,
    outcomes: list[tuple[str, SearchProviderStatus]],
) -> ToolResult:
    details = ", ".join(
        f"{provider}={status.value}" for provider, status in outcomes
    )
    message = _ERROR_MESSAGES[code]
    suffix = f" Providers: {details}." if details else ""
    return ToolResult(ok=False, output=f"error[{code}]: {message}{suffix}")


def _run(input_data: NormalizedSearchRequest, context) -> ToolResult:
    del context
    try:
        providers = search_providers.load_provider_order()
    except ValueError:
        return ToolResult(
            ok=False,
            output=SearchError("provider_config_invalid").tool_output(),
        )

    total_deadline = time.monotonic() + SEARCH_TIMEOUT_SECONDS
    outcomes: list[tuple[str, SearchProviderStatus]] = []
    for provider in providers:
        now = time.monotonic()
        if now >= total_deadline:
            return _format_failure("search_timeout", outcomes)
        outcome = search_providers.search_provider(
            provider,
            input_data.query,
            input_data.num_results,
            deadline=min(total_deadline, now + PROVIDER_TIMEOUT_SECONDS),
        )
        outcomes.append((provider, outcome.status))
        if outcome.status is SearchProviderStatus.SUCCESS:
            return ToolResult(
                ok=True,
                output=_format_results(provider, outcome.results),
            )
        if time.monotonic() >= total_deadline:
            return _format_failure("search_timeout", outcomes)

    if outcomes and all(
        status is SearchProviderStatus.NO_RESULTS for _, status in outcomes
    ):
        return _format_failure("no_results", outcomes)
    if any(status is SearchProviderStatus.NO_RESULTS for _, status in outcomes):
        return _format_failure("search_incomplete", outcomes)
    return _format_failure("search_unavailable", outcomes)


web_search_tool = ToolDefinition(
    name="web_search",
    description=(
        "Search the public web through a bounded built-in provider chain. "
        "Returns titles, safe result URLs, and snippets without API keys."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": MAX_QUERY_CHARS,
                "description": "The web search query.",
            },
            "num_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_RESULTS,
                "description": "Maximum results to return (default: 5).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    validator=_validate,
    run=_run,
)


__all__ = [
    "NormalizedSearchRequest",
    "SearchError",
    "web_search_tool",
]
