"""Fail-closed network safety primitives used only by ``http_request``."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from minicode.tools.bounded_resolver import (
    BoundedResolver,
    ResolverError,
    ResolverSnapshot,
)


_ERROR_MESSAGES = {
    "destination_blocked": "The request destination is not allowed.",
    "dns_error": "The request destination could not be resolved.",
    "http_error": "The server returned an HTTP error.",
    "invalid_request": "The HTTP request is invalid.",
    "network_unavailable": "The network destination is unavailable.",
    "permission_cancelled": "The network approval was cancelled.",
    "permission_denied": "The network request was denied.",
    "permission_expired": "The network approval expired.",
    "permission_required": "The network request requires approval.",
    "permission_unavailable": "Network approval is unavailable.",
    "request_body_too_large": "The request body exceeds the safe limit.",
    "request_cancelled": "The network request was cancelled.",
    "request_failed": "The network request failed.",
    "redirect_blocked": "The redirect target is not allowed.",
    "redirect_not_allowed": "Redirects are not allowed for this method.",
    "resolver_busy": "The DNS resolver is temporarily busy.",
    "response_too_large": "The response exceeds the safe byte limit.",
    "unsupported_response_type": "The response content type is not supported.",
    "unsupported_scheme": "Only HTTP and HTTPS requests are supported.",
    "timeout": "The network request timed out.",
    "tls_error": "The secure connection could not be established.",
}

MAX_URL_BYTES = 4_096
MAX_HEADER_COUNT = 32
MAX_HEADER_NAME_BYTES = 128
MAX_HEADER_VALUE_BYTES = 4_096
MAX_HEADER_BYTES = 16 * 1024
MAX_REQUEST_BODY_BYTES = 64 * 1024
MIN_TIMEOUT_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 30.0
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 1024 * 1024
RESPONSE_READ_CHUNK_BYTES = 64 * 1024
MAX_RENDERED_OUTPUT_CHARS = 15_000

_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"})
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}
)
_FORBIDDEN_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization"}
)
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_DNS_RESOLVER = BoundedResolver()


class NetworkSafetyError(ValueError):
    """Low-cardinality network error that never contains request content."""

    _model_safe_tool_output = True

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_MESSAGES else "invalid_request"
        super().__init__(self.code)

    def tool_output(self) -> str:
        return f"error[{self.code}]: {_ERROR_MESSAGES[self.code]}"


@dataclass(frozen=True, slots=True)
class ValidatedDestination:
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NormalizedHttpRequest:
    url: str
    method: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    timeout: float

    @property
    def fingerprint(self) -> str:
        parsed = urlsplit(self.url)
        canonical = {
            "method": self.method,
            "scheme": parsed.scheme,
            "hostname": parsed.hostname,
            "port": parsed.port or (443 if parsed.scheme == "https" else 80),
            "target": parsed.path or "/",
            "query": parsed.query,
            "headers": [
                [
                    name.casefold(),
                    hashlib.sha256(value.encode("utf-8")).hexdigest(),
                ]
                for name, value in self.headers
            ],
            "body": hashlib.sha256(self.body).hexdigest(),
        }
        return "networkreq_" + hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    def review(self) -> dict[str, object]:
        parsed = urlsplit(self.url)
        path_summary = parsed.path or "/"
        if len(path_summary.encode("utf-8")) > 256:
            path_summary = path_summary.encode("utf-8")[:253].decode(
                "utf-8", errors="ignore"
            ) + "…"
        return {
            "reviewVersion": 1,
            "method": self.method,
            "scheme": parsed.scheme,
            "hostname": parsed.hostname or "",
            "port": parsed.port or (443 if parsed.scheme == "https" else 80),
            "pathSummary": path_summary,
            "hasBody": bool(self.body),
            "hasSensitiveHeaders": any(
                name.casefold() in _SENSITIVE_HEADERS for name, _ in self.headers
            ),
            "requestFingerprint": self.fingerprint,
        }


def _invalid() -> NetworkSafetyError:
    return NetworkSafetyError("invalid_request")


def _encode_bounded_json_body(value: object) -> bytes:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    encoded_parts: list[bytes] = []
    character_count = 0
    byte_count = 0
    try:
        for part in encoder.iterencode(value):
            character_count += len(part)
            if character_count > MAX_REQUEST_BODY_BYTES:
                raise NetworkSafetyError("request_body_too_large")
            encoded = part.encode("utf-8")
            byte_count += len(encoded)
            if byte_count > MAX_REQUEST_BODY_BYTES:
                raise NetworkSafetyError("request_body_too_large")
            encoded_parts.append(encoded)
    except NetworkSafetyError:
        raise
    except (TypeError, ValueError) as error:
        raise _invalid() from error
    return b"".join(encoded_parts)


def normalize_http_request(input_data: object) -> NormalizedHttpRequest:
    """Return one immutable bounded request or a safe validation error."""
    if not isinstance(input_data, dict) or not set(input_data) <= {
        "url",
        "method",
        "headers",
        "body",
        "timeout",
    }:
        raise _invalid()

    raw_url = input_data.get("url")
    if not isinstance(raw_url, str):
        raise _invalid()
    raw_url = raw_url.strip()
    if (
        not raw_url
        or len(raw_url.encode("utf-8")) > MAX_URL_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_url)
    ):
        raise _invalid()
    try:
        parsed = urlsplit(raw_url)
        explicit_port = parsed.port
    except ValueError as error:
        raise _invalid() from error
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise NetworkSafetyError("unsupported_scheme")
    if (
        parsed.username is not None
        or parsed.password is not None
        or not isinstance(parsed.hostname, str)
        or not parsed.hostname
    ):
        raise _invalid()
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise _invalid() from error
    if explicit_port is not None and not 1 <= explicit_port <= 65535:
        raise _invalid()
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = display_host + (f":{explicit_port}" if explicit_port is not None else "")
    normalized_url = urlunsplit(
        (scheme, netloc, parsed.path or "/", parsed.query, "")
    )
    if len(normalized_url.encode("utf-8")) > MAX_URL_BYTES:
        raise _invalid()

    raw_method = input_data.get("method", "GET")
    if not isinstance(raw_method, str):
        raise _invalid()
    method = raw_method.strip().upper()
    if method not in _METHODS:
        raise _invalid()

    raw_headers = input_data.get("headers", {})
    if not isinstance(raw_headers, Mapping) or len(raw_headers) > MAX_HEADER_COUNT:
        raise _invalid()
    normalized_headers: list[tuple[str, str]] = []
    seen_names: set[str] = set()
    aggregate = 0
    for name, value in raw_headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise _invalid()
        folded = name.casefold()
        name_bytes = len(name.encode("ascii", errors="ignore"))
        value_bytes = len(value.encode("utf-8"))
        if (
            not name
            or _HEADER_NAME_RE.fullmatch(name) is None
            or name_bytes != len(name)
            or name_bytes > MAX_HEADER_NAME_BYTES
            or value_bytes > MAX_HEADER_VALUE_BYTES
            or "\r" in value
            or "\n" in value
            or "\x00" in value
            or folded in seen_names
            or folded in _FORBIDDEN_HEADERS
        ):
            raise _invalid()
        aggregate += name_bytes + value_bytes + 4
        if aggregate > MAX_HEADER_BYTES:
            raise _invalid()
        seen_names.add(folded)
        normalized_headers.append((name, value))

    raw_body = input_data.get("body", "")
    body_is_json = isinstance(raw_body, (dict, list))
    if raw_body is None or raw_body == "":
        body = b""
    elif isinstance(raw_body, str):
        if len(raw_body) > MAX_REQUEST_BODY_BYTES:
            raise NetworkSafetyError("request_body_too_large")
        body = raw_body.encode("utf-8")
    elif body_is_json:
        body = _encode_bounded_json_body(raw_body)
    else:
        raise _invalid()
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise NetworkSafetyError("request_body_too_large")
    if body and method not in _BODY_METHODS:
        raise _invalid()
    if body_is_json and "content-type" not in seen_names:
        generated_name = "Content-Type"
        generated_value = "application/json"
        if (
            len(normalized_headers) >= MAX_HEADER_COUNT
            or aggregate + len(generated_name) + len(generated_value) + 4
            > MAX_HEADER_BYTES
        ):
            raise _invalid()
        normalized_headers.append((generated_name, generated_value))

    raw_timeout = input_data.get("timeout", 30)
    if (
        isinstance(raw_timeout, bool)
        or not isinstance(raw_timeout, (int, float))
        or not math.isfinite(raw_timeout)
        or not MIN_TIMEOUT_SECONDS <= float(raw_timeout) <= MAX_TIMEOUT_SECONDS
    ):
        raise _invalid()
    timeout = float(raw_timeout)

    if scheme == "http" and (
        method in _MUTATION_METHODS
        or method == "OPTIONS"
        or any(name.casefold() in _SENSITIVE_HEADERS for name, _ in normalized_headers)
    ):
        raise NetworkSafetyError("destination_blocked")

    return NormalizedHttpRequest(
        normalized_url,
        method,
        tuple(normalized_headers),
        body,
        timeout,
    )


def _public_address(value: str) -> str:
    candidate = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as error:
        raise NetworkSafetyError("dns_error") from error
    mapped = getattr(address, "ipv4_mapped", None)
    if (
        not address.is_global
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or mapped is not None
        and (
            not mapped.is_global
            or mapped.is_loopback
            or mapped.is_private
            or mapped.is_link_local
            or mapped.is_multicast
            or mapped.is_reserved
            or mapped.is_unspecified
        )
    ):
        raise NetworkSafetyError("destination_blocked")
    return address.compressed


def _resolve_with_deadline(
    hostname: str,
    port: int,
    *,
    deadline: float | None,
) -> list[tuple[object, ...]]:
    effective_deadline = (
        deadline
        if deadline is not None
        else time.monotonic() + MAX_TIMEOUT_SECONDS
    )
    try:
        return _DNS_RESOLVER.resolve(
            hostname,
            port,
            deadline=effective_deadline,
        )
    except ResolverError as error:
        raise NetworkSafetyError(error.code) from None


def resolver_snapshot() -> ResolverSnapshot:
    """Return low-cardinality state for deterministic diagnostics."""
    return _DNS_RESOLVER.snapshot()


def validate_destination(
    url: str,
    *,
    deadline: float | None = None,
) -> ValidatedDestination:
    """Resolve and reject a destination unless every address is public."""
    try:
        parsed = urlsplit(url)
        explicit_port = parsed.port
    except ValueError as error:
        raise NetworkSafetyError("invalid_request") from error
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise NetworkSafetyError("unsupported_scheme")
    hostname = parsed.hostname
    if (
        parsed.username is not None
        or parsed.password is not None
        or not isinstance(hostname, str)
        or not hostname
        or explicit_port is not None and not 1 <= explicit_port <= 65535
    ):
        raise NetworkSafetyError("invalid_request")
    try:
        hostname = hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise NetworkSafetyError("invalid_request") from error
    port = explicit_port or (443 if scheme == "https" else 80)
    if deadline is not None and time.monotonic() >= deadline:
        raise NetworkSafetyError("timeout")
    try:
        direct = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        direct = None
    if direct is not None:
        addresses = (_public_address(hostname),)
    else:
        try:
            answers = _resolve_with_deadline(
                hostname,
                port,
                deadline=deadline,
            )
        except NetworkSafetyError:
            raise
        except (OSError, socket.gaierror) as error:
            raise NetworkSafetyError("dns_error") from error
        try:
            addresses = tuple(
                sorted({_public_address(str(answer[4][0])) for answer in answers})
            )
        except NetworkSafetyError:
            raise
        except (IndexError, TypeError, ValueError) as error:
            raise NetworkSafetyError("dns_error") from error
        if not addresses:
            raise NetworkSafetyError("dns_error")
    return ValidatedDestination(scheme, hostname, port, addresses)


def build_network_review(
    request: NormalizedHttpRequest,
) -> dict[str, object]:
    """Build a content-free review and opaque binding for one request."""
    return request.review()


def read_bounded_response(
    response: object,
    *,
    method: str,
    deadline: float | None = None,
) -> bytes:
    """Read one response without any unbounded stream reads."""
    if method == "HEAD":
        return b""

    headers = getattr(response, "headers", {})
    raw_length = headers.get("Content-Length") if hasattr(headers, "get") else None
    declared_length: int | None = None
    if raw_length is not None:
        try:
            declared_length = int(raw_length)
        except (TypeError, ValueError) as error:
            raise NetworkSafetyError("invalid_request") from error
        if declared_length < 0:
            raise NetworkSafetyError("invalid_request")
        if declared_length > MAX_RESPONSE_BYTES:
            raise NetworkSafetyError("response_too_large")

    chunks: list[bytes] = []
    total = 0
    while declared_length is None or total < declared_length:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkSafetyError("timeout")
            timeout_setter = getattr(response, "set_read_timeout", None)
            if callable(timeout_setter):
                timeout_setter(remaining)
        remaining_budget = MAX_RESPONSE_BYTES - total
        read_size = min(RESPONSE_READ_CHUNK_BYTES, remaining_budget + 1)
        chunk = response.read(read_size)
        if deadline is not None and time.monotonic() >= deadline:
            raise NetworkSafetyError("timeout")
        if not isinstance(chunk, bytes):
            raise NetworkSafetyError("invalid_request")
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise NetworkSafetyError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def render_safe_response(
    *, status: int, headers: object, payload: bytes
) -> str:
    """Render a bounded text/JSON response with a minimal header projection."""
    raw_content_type = (
        headers.get("Content-Type", "text/plain")
        if hasattr(headers, "get")
        else "text/plain"
    )
    if (
        not isinstance(raw_content_type, str)
        or "\r" in raw_content_type
        or "\n" in raw_content_type
    ):
        raise NetworkSafetyError("unsupported_response_type")
    media_type = raw_content_type.split(";", 1)[0].strip().casefold()
    is_json = media_type == "application/json" or media_type.endswith("+json")
    if not is_json and not media_type.startswith("text/"):
        raise NetworkSafetyError("unsupported_response_type")

    content = payload.decode("utf-8", errors="replace")
    if is_json:
        try:
            content = json.dumps(
                json.loads(content),
                indent=2,
                ensure_ascii=False,
            )
        except json.JSONDecodeError:
            pass

    safe_headers = {
        "Content-Type": media_type,
        "Content-Length": str(len(payload)),
    }
    prefix = "\n".join(
        [
            "--- Response ---",
            f"Status: {int(status)}",
            f"Headers: {json.dumps(safe_headers, indent=2)}",
            "",
            "Body:",
        ]
    )
    available = max(0, MAX_RENDERED_OUTPUT_CHARS - len(prefix) - 1)
    return f"{prefix}\n{content[:available]}"


__all__ = [
    "NetworkSafetyError",
    "NormalizedHttpRequest",
    "ValidatedDestination",
    "build_network_review",
    "normalize_http_request",
    "read_bounded_response",
    "render_safe_response",
    "resolver_snapshot",
    "validate_destination",
]
