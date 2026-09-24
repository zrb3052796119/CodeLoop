"""Bounded, provider-specific HTML search behind MiniCode's safe GET seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from enum import Enum
from html.parser import HTMLParser
import ipaddress
import os
import re
import time
from typing import Protocol
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from minicode.tools.http_utils import (
    SafeHttpResponse,
    execute_safe_get_response,
)
from minicode.tools.network_safety import (
    NetworkSafetyError,
    normalize_http_request,
)


DEFAULT_PROVIDER_ORDER = ("bing", "baidu", "duckduckgo")
KNOWN_PROVIDERS = frozenset(DEFAULT_PROVIDER_ORDER)
MAX_PROVIDER_COUNT = 3
MAX_RESULT_TITLE_CHARS = 300
MAX_RESULT_SNIPPET_CHARS = 600
MAX_RESULT_URL_BYTES = 4_096
_MAX_SIGNAL_CHARS = 16_384
_MAX_CANDIDATE_MULTIPLIER = 4
_PROVIDER_HEADERS = {
    "User-Agent": "MiniCode-Python/0.5.0 (Terminal Coding Assistant)",
    "Accept": "text/html,application/xhtml+xml;q=0.9",
    "Accept-Encoding": "identity",
}
_WHITESPACE_RE = re.compile(r"\s+")
_CHALLENGE_TERMS = (
    "captcha",
    "verify you are human",
    "verification required",
    "just a moment",
    "安全验证",
    "验证码",
    "访问异常",
)


class SearchProviderStatus(str, Enum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    TIMEOUT = "timeout"
    DNS_ERROR = "dns_error"
    NETWORK_UNAVAILABLE = "network_unavailable"
    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    CHALLENGE = "challenge"
    RESPONSE_UNRECOGNIZED = "response_unrecognized"
    RESPONSE_TOO_LARGE = "response_too_large"
    REDIRECT_BLOCKED = "redirect_blocked"
    TLS_ERROR = "tls_error"


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str
    provider_link: bool = False


@dataclass(frozen=True, slots=True)
class SearchProviderOutcome:
    provider: str
    status: SearchProviderStatus
    results: tuple[SearchResult, ...] = ()

    def __post_init__(self) -> None:
        if self.provider not in KNOWN_PROVIDERS:
            raise ValueError("unknown provider")
        if (self.status is SearchProviderStatus.SUCCESS) != bool(self.results):
            raise ValueError("success must contain results and failures must not")
        if any(result.provider != self.provider for result in self.results):
            raise ValueError("result/provider mismatch")


class SearchProvider(Protocol):
    provider_id: str

    def search(
        self,
        query: str,
        num_results: int,
        *,
        deadline: float,
    ) -> SearchProviderOutcome: ...


@dataclass(slots=True)
class _Candidate:
    title: str = ""
    url: str = ""
    snippet: str = ""


def load_provider_order(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Load one closed, exact provider order or fail before network access."""
    source = os.environ if environment is None else environment
    raw = source.get("MINI_CODE_WEB_SEARCH_PROVIDERS")
    if raw is None:
        return DEFAULT_PROVIDER_ORDER
    if not raw or raw.strip() != raw:
        raise ValueError("provider_config_invalid")
    providers = tuple(raw.split(","))
    if (
        not providers
        or len(providers) > MAX_PROVIDER_COUNT
        or any(
            not provider
            or provider not in KNOWN_PROVIDERS
            or provider != provider.casefold()
            for provider in providers
        )
        or len(set(providers)) != len(providers)
    ):
        raise ValueError("provider_config_invalid")
    return providers


def _clean_text(value: str, limit: int) -> str:
    clean = "".join(
        " " if unicodedata.category(character) == "Cc" else character
        for character in value
    )
    return _WHITESPACE_RE.sub(" ", clean).strip()[:limit]


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _safe_url_text(value: str) -> bool:
    if not value or _has_control_characters(value):
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_RESULT_URL_BYTES
    except UnicodeEncodeError:
        return False


def _blocked_literal_hostname(hostname: str) -> bool:
    normalized = hostname.rstrip(".").casefold()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    )


def project_result_url(
    raw_url: str,
    *,
    provider: str,
) -> tuple[str, bool] | None:
    """Return a safe textual result URL without resolving or fetching it."""
    if _has_control_characters(raw_url):
        return None
    raw_url = raw_url.strip()
    if not _safe_url_text(raw_url):
        return None
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None

    parsed_hostname = parsed.hostname
    if (
        provider == "duckduckgo"
        and isinstance(parsed_hostname, str)
        and parsed_hostname.rstrip(".").casefold()
        in {"duckduckgo.com", "www.duckduckgo.com", "html.duckduckgo.com"}
        and parsed.path.rstrip("/") == "/l"
    ):
        targets = parse_qs(parsed.query, keep_blank_values=False).get("uddg", [])
        if len(targets) != 1 or not targets[0]:
            return None
        raw_url = targets[0]
        if not _safe_url_text(raw_url):
            return None
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            return None

    try:
        explicit_port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or not isinstance(parsed.hostname, str)
        or not parsed.hostname
        or (explicit_port is not None and not 1 <= explicit_port <= 65_535)
    ):
        return None
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return None
    if _blocked_literal_hostname(hostname):
        return None
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = display_host + (
        f":{explicit_port}" if explicit_port is not None else ""
    )
    projected = urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
    if len(projected.encode("utf-8")) > MAX_RESULT_URL_BYTES:
        return None
    provider_link = bool(
        provider == "baidu"
        and hostname in {"baidu.com", "www.baidu.com"}
        and parsed.path.startswith("/link")
    ) or bool(
        provider == "bing"
        and hostname in {"bing.com", "www.bing.com", "cn.bing.com"}
        and parsed.path.startswith("/ck/")
    )
    return projected, provider_link


class _ProviderHtmlParser(HTMLParser):
    def __init__(self, provider: str, max_results: int) -> None:
        super().__init__(convert_charrefs=True)
        self.provider = provider
        self.max_results = max_results
        self._candidate_limit = max(
            max_results,
            max_results * _MAX_CANDIDATE_MULTIPLIER,
        )
        self._candidates: list[_Candidate] = []
        self._signals: list[str] = []
        self._signal_chars = 0
        self._challenge_marker = False
        self._empty_marker = False
        self._recognized_results = False
        self._current: _Candidate | None = None
        self._capture: str | None = None
        self._capture_tag: str | None = None
        self._div_depth = 0
        self._result_div_depth: int | None = None
        self._h3_depth = 0
        self._skip_tags: list[str] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {
            key.casefold(): value or ""
            for key, value in attrs
            if isinstance(key, str)
        }

    @staticmethod
    def _classes(attributes: Mapping[str, str]) -> set[str]:
        return {
            token.casefold()
            for token in attributes.get("class", "").split()
            if token
        }

    def _start_result(self) -> None:
        self._recognized_results = True
        self._current = _Candidate()
        self._result_div_depth = self._div_depth

    def _start_capture(self, field: str, tag: str) -> None:
        self._capture = field
        self._capture_tag = tag

    def _finish_result(self) -> None:
        if self._current is not None and len(self._candidates) < self._candidate_limit:
            self._candidates.append(self._current)
        self._current = None
        self._capture = None
        self._capture_tag = None
        self._result_div_depth = None

    def _record_markers(
        self,
        attributes: Mapping[str, str],
        classes: set[str],
    ) -> None:
        marker = " ".join(
            (
                attributes.get("id", ""),
                attributes.get("class", ""),
                attributes.get("name", ""),
            )
        ).casefold()
        if any(term in marker for term in ("captcha", "verify", "challenge")):
            self._challenge_marker = True
        if (
            "no-results" in classes
            or "no_result" in classes
            or "content_none" in marker
            or "nors" in classes
        ):
            self._empty_marker = True

    def handle_data(self, data: str) -> None:
        if self._skip_tags:
            return
        if self._signal_chars < _MAX_SIGNAL_CHARS:
            remaining = _MAX_SIGNAL_CHARS - self._signal_chars
            piece = data[:remaining]
            self._signals.append(piece)
            self._signal_chars += len(piece)
        if self._current is None or self._capture is None:
            return
        current = getattr(self._current, self._capture)
        limit = (
            MAX_RESULT_TITLE_CHARS
            if self._capture == "title"
            else MAX_RESULT_SNIPPET_CHARS
        )
        if len(current) < limit:
            setattr(self._current, self._capture, current + data[: limit - len(current)])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._skip_tags:
            if tag == self._skip_tags[-1]:
                self._skip_tags.pop()
            return
        if self._capture_tag == tag:
            self._capture = None
            self._capture_tag = None
        if tag == "h3" and self._h3_depth:
            self._h3_depth -= 1
        if tag == "div":
            if self._result_div_depth == self._div_depth:
                self._finish_result()
            self._div_depth = max(0, self._div_depth - 1)

    def _project_candidates(self) -> tuple[SearchResult, ...]:
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for candidate in self._candidates:
            title = _clean_text(candidate.title, MAX_RESULT_TITLE_CHARS)
            snippet = _clean_text(candidate.snippet, MAX_RESULT_SNIPPET_CHARS)
            projected = project_result_url(candidate.url, provider=self.provider)
            if not title or projected is None:
                continue
            url, provider_link = projected
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    provider=self.provider,
                    provider_link=provider_link,
                )
            )
            if len(results) >= self.max_results:
                break
        return tuple(results)

    def outcome(self) -> SearchProviderOutcome:
        if self._current is not None:
            self._finish_result()
        results = self._project_candidates()
        if results:
            return SearchProviderOutcome(
                self.provider,
                SearchProviderStatus.SUCCESS,
                results,
            )
        signal = _clean_text(" ".join(self._signals), _MAX_SIGNAL_CHARS).casefold()
        if self._challenge_marker or any(term in signal for term in _CHALLENGE_TERMS):
            status = SearchProviderStatus.CHALLENGE
        elif self._empty_marker or self._is_explicit_empty(signal):
            status = SearchProviderStatus.NO_RESULTS
        else:
            status = SearchProviderStatus.RESPONSE_UNRECOGNIZED
        return SearchProviderOutcome(self.provider, status)

    def _is_explicit_empty(self, signal: str) -> bool:
        raise NotImplementedError


class BaiduHtmlParser(_ProviderHtmlParser):
    """Streaming parser for the bounded Baidu HTML result shapes we accept."""

    def __init__(self, max_results: int) -> None:
        super().__init__("baidu", max_results)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._skip_tags.append(tag)
            return
        if self._skip_tags:
            return
        attributes = self._attrs(attrs)
        classes = self._classes(attributes)
        self._record_markers(attributes, classes)
        if tag == "div":
            self._div_depth += 1
            if self._current is None and any(
                token == "result" or token.startswith("result-")
                for token in classes
            ):
                self._start_result()
            elif self._current is not None and (
                "c-abstract" in classes
                or "c-span-last" in classes
                or any(token.startswith("content-right") for token in classes)
            ):
                self._start_capture("snippet", "div")
        elif tag == "h3" and self._current is not None:
            self._h3_depth += 1
        elif (
            tag == "a"
            and self._current is not None
            and self._h3_depth
            and not self._current.url
        ):
            self._current.url = attributes.get("href", "")
            self._start_capture("title", "a")
        elif (
            tag == "span"
            and self._current is not None
            and ("c-abstract" in classes or "c-span-last" in classes)
        ):
            self._start_capture("snippet", "span")

    def _is_explicit_empty(self, signal: str) -> bool:
        return (
            "抱歉没有找到" in signal
            or "没有找到相关结果" in signal
            or "未找到相关结果" in signal
        )


class BingHtmlParser(_ProviderHtmlParser):
    """Streaming parser for the bounded Bing HTML result shape.

    Bing organic results are ``<li class="b_algo">`` items with the title in
    ``h2 > a`` and the snippet in a ``p`` inside the item — unlike the other
    providers, results are list items rather than divs.
    """

    def __init__(self, max_results: int) -> None:
        super().__init__("bing", max_results)
        self._li_depth = 0
        self._result_li_depth: int | None = None
        self._h2_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._skip_tags.append(tag)
            return
        if self._skip_tags:
            return
        attributes = self._attrs(attrs)
        classes = self._classes(attributes)
        self._record_markers(attributes, classes)
        if tag == "li":
            self._li_depth += 1
            if self._current is None and "b_algo" in classes:
                self._start_result()
                self._result_li_depth = self._li_depth
        elif tag == "div":
            self._div_depth += 1
        elif tag == "h2" and self._current is not None:
            self._h2_depth += 1
        elif (
            tag == "a"
            and self._current is not None
            and self._h2_depth
            and not self._current.url
        ):
            self._current.url = attributes.get("href", "")
            self._start_capture("title", "a")
        elif (
            tag == "p"
            and self._current is not None
            and self._capture is None
            and not self._current.snippet
        ):
            self._start_capture("snippet", "p")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._skip_tags:
            if tag == self._skip_tags[-1]:
                self._skip_tags.pop()
            return
        if self._capture_tag == tag:
            self._capture = None
            self._capture_tag = None
        if tag == "h2" and self._h2_depth:
            self._h2_depth -= 1
        if tag == "div":
            self._div_depth = max(0, self._div_depth - 1)
        if tag == "li":
            if self._result_li_depth == self._li_depth:
                self._finish_result()
                self._result_li_depth = None
            self._li_depth = max(0, self._li_depth - 1)

    def _is_explicit_empty(self, signal: str) -> bool:
        return (
            "there are no results for" in signal
            or "没有与此相关的结果" in signal
            or "did not match any documents" in signal
        )


class DuckDuckGoHtmlParser(_ProviderHtmlParser):
    """Streaming parser for the bounded DuckDuckGo HTML result shape."""

    def __init__(self, max_results: int) -> None:
        super().__init__("duckduckgo", max_results)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._skip_tags.append(tag)
            return
        if self._skip_tags:
            return
        attributes = self._attrs(attrs)
        classes = self._classes(attributes)
        self._record_markers(attributes, classes)
        if tag == "div":
            self._div_depth += 1
            if self._current is None and "result" in classes:
                self._start_result()
            elif self._current is not None and "result__snippet" in classes:
                self._start_capture("snippet", "div")
        elif (
            tag == "a"
            and self._current is not None
            and "result__a" in classes
            and not self._current.url
        ):
            self._current.url = attributes.get("href", "")
            self._start_capture("title", "a")
        elif (
            tag in {"a", "span"}
            and self._current is not None
            and "result__snippet" in classes
        ):
            self._start_capture("snippet", tag)

    def _is_explicit_empty(self, signal: str) -> bool:
        return (
            "no results." in signal
            or "no results found" in signal
            or "did not match any documents" in signal
        )


def parse_provider_html(
    provider: str,
    html_text: str,
    num_results: int,
) -> SearchProviderOutcome:
    parser: _ProviderHtmlParser
    if provider == "baidu":
        parser = BaiduHtmlParser(num_results)
    elif provider == "duckduckgo":
        parser = DuckDuckGoHtmlParser(num_results)
    elif provider == "bing":
        parser = BingHtmlParser(num_results)
    else:
        raise ValueError("unknown provider")
    try:
        parser.feed(html_text)
        parser.close()
        return parser.outcome()
    except Exception:
        return SearchProviderOutcome(
            provider,
            SearchProviderStatus.RESPONSE_UNRECOGNIZED,
        )


def _provider_url(provider: str, query: str) -> str:
    if provider == "baidu":
        return "https://www.baidu.com/s?" + urlencode({"wd": query})
    if provider == "duckduckgo":
        return "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    if provider == "bing":
        return "https://www.bing.com/search?" + urlencode({"q": query})
    raise ValueError("unknown provider")


def _network_status(code: str) -> SearchProviderStatus:
    return {
        "timeout": SearchProviderStatus.TIMEOUT,
        "dns_error": SearchProviderStatus.DNS_ERROR,
        "network_unavailable": SearchProviderStatus.NETWORK_UNAVAILABLE,
        "resolver_busy": SearchProviderStatus.NETWORK_UNAVAILABLE,
        "response_too_large": SearchProviderStatus.RESPONSE_TOO_LARGE,
        "redirect_blocked": SearchProviderStatus.REDIRECT_BLOCKED,
        "redirect_not_allowed": SearchProviderStatus.REDIRECT_BLOCKED,
        "destination_blocked": SearchProviderStatus.REDIRECT_BLOCKED,
        "tls_error": SearchProviderStatus.TLS_ERROR,
        "unsupported_response_type": SearchProviderStatus.RESPONSE_UNRECOGNIZED,
    }.get(code, SearchProviderStatus.NETWORK_UNAVAILABLE)


def _http_status(status: int) -> SearchProviderStatus | None:
    if 200 <= status < 300:
        return None
    if status == 403:
        return SearchProviderStatus.FORBIDDEN
    if status == 429:
        return SearchProviderStatus.RATE_LIMITED
    if 400 <= status < 500:
        return SearchProviderStatus.FORBIDDEN
    if 500 <= status < 600:
        return SearchProviderStatus.SERVER_ERROR
    return SearchProviderStatus.RESPONSE_UNRECOGNIZED


def _decode_html(response: SafeHttpResponse) -> str:
    if response.content_encoding not in {"", "identity"}:
        raise ValueError("response_unrecognized")
    content_type = Message()
    content_type["Content-Type"] = response.content_type
    media_type = content_type.get_content_type().casefold()
    if media_type not in {"text/html", "application/xhtml+xml"}:
        raise ValueError("response_unrecognized")
    charset = content_type.get_content_charset("utf-8") or "utf-8"
    try:
        return response.payload.decode(charset, errors="replace")
    except LookupError:
        return response.payload.decode("utf-8", errors="replace")


def search_provider(
    provider: str,
    query: str,
    num_results: int,
    *,
    deadline: float,
    transport: Callable[..., SafeHttpResponse] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> SearchProviderOutcome:
    """Execute one provider exactly once under the caller's monotonic deadline."""
    if provider not in KNOWN_PROVIDERS:
        raise ValueError("unknown provider")
    if monotonic() >= deadline:
        return SearchProviderOutcome(provider, SearchProviderStatus.TIMEOUT)
    try:
        request = normalize_http_request(
            {
                "url": _provider_url(provider, query),
                "method": "GET",
                "headers": _PROVIDER_HEADERS,
                "timeout": max(0.1, min(6.0, deadline - monotonic())),
            }
        )
        response = (transport or execute_safe_get_response)(
            request,
            deadline=deadline,
        )
    except NetworkSafetyError as error:
        return SearchProviderOutcome(provider, _network_status(error.code))
    except Exception:
        return SearchProviderOutcome(
            provider,
            SearchProviderStatus.NETWORK_UNAVAILABLE,
        )
    status = _http_status(response.status)
    if status is not None:
        return SearchProviderOutcome(provider, status)
    if monotonic() >= deadline:
        return SearchProviderOutcome(provider, SearchProviderStatus.TIMEOUT)
    try:
        html_text = _decode_html(response)
    except ValueError:
        return SearchProviderOutcome(
            provider,
            SearchProviderStatus.RESPONSE_UNRECOGNIZED,
        )
    outcome = parse_provider_html(provider, html_text, num_results)
    if monotonic() >= deadline:
        return SearchProviderOutcome(provider, SearchProviderStatus.TIMEOUT)
    return outcome


__all__ = [
    "BaiduHtmlParser",
    "BingHtmlParser",
    "DEFAULT_PROVIDER_ORDER",
    "DuckDuckGoHtmlParser",
    "SearchProvider",
    "SearchProviderOutcome",
    "SearchProviderStatus",
    "SearchResult",
    "load_provider_order",
    "parse_provider_html",
    "project_result_url",
    "search_provider",
]
