from __future__ import annotations

from dataclasses import dataclass
import http.client
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit, urlunsplit

from minicode.permissions import NetworkPermissionError
from minicode.tooling import ToolDefinition, ToolContext, ToolResult
from minicode.tools.network_safety import (
    MAX_REDIRECTS,
    NetworkSafetyError,
    NormalizedHttpRequest,
    ValidatedDestination,
    build_network_review,
    normalize_http_request,
    read_bounded_response,
    render_safe_response,
    validate_destination,
)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(
        self,
        hostname: str,
        port: int,
        *,
        address: str,
        timeout: float,
    ) -> None:
        self._pinned_address = address
        super().__init__(hostname, port=port, timeout=timeout)

    def connect(self) -> None:
        self.sock = self._create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        hostname: str,
        port: int,
        *,
        address: str,
        timeout: float,
    ) -> None:
        self._pinned_address = address
        super().__init__(hostname, port=port, timeout=timeout)

    def connect(self) -> None:
        self.sock = self._create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=self.host,
        )


class _OwnedResponse:
    def __init__(
        self,
        response: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
    ) -> None:
        self._response = response
        self._connection = connection
        self.status = response.status
        self.headers = response.headers

    def read(self, size: int) -> bytes:
        return self._response.read(size)

    def set_read_timeout(self, timeout: float) -> None:
        sock = getattr(self._connection, "sock", None)
        setter = getattr(sock, "settimeout", None)
        if callable(setter):
            setter(timeout)

    def __enter__(self) -> "_OwnedResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self._response.close()
        self._connection.close()


@dataclass(frozen=True, slots=True)
class SafeHttpResponse:
    """Bounded response facts returned by the shared safe transport seam."""

    status: int
    content_type: str
    content_encoding: str
    payload: bytes


def _open_no_redirect(
    request: urllib.request.Request,
    *,
    timeout: float,
) -> _OwnedResponse:
    destination = getattr(request, "_minicode_destination", None)
    if not isinstance(destination, ValidatedDestination):
        raise NetworkSafetyError("request_failed")
    connection_class = (
        _PinnedHTTPSConnection
        if destination.scheme == "https"
        else _PinnedHTTPConnection
    )
    connection = connection_class(
        destination.hostname,
        destination.port,
        address=destination.addresses[0],
        timeout=timeout,
    )
    parsed = urlsplit(request.full_url)
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    headers = dict(request.header_items())
    try:
        connection.request(
            request.get_method(),
            target,
            body=request.data,
            headers=headers,
        )
        return _OwnedResponse(connection.getresponse(), connection)
    except Exception:
        connection.close()
        raise


def _safe_header_value(
    headers: object,
    name: str,
    *,
    default: str,
) -> str:
    value = headers.get(name, default) if hasattr(headers, "get") else default
    if (
        not isinstance(value, str)
        or "\r" in value
        or "\n" in value
        or "\x00" in value
    ):
        raise NetworkSafetyError("unsupported_response_type")
    return value.strip()


def _execute_safe_http_response(
    request: NormalizedHttpRequest,
    *,
    deadline: float,
    destination: ValidatedDestination | None = None,
    observe_http_status: bool,
) -> SafeHttpResponse:
    """Return one bounded safe response, including final HTTP error statuses."""
    try:
        current_request = request
        if destination is None:
            destination = validate_destination(request.url, deadline=deadline)
        visited_urls = {request.url}
        redirects = 0
        while True:
            outgoing = urllib.request.Request(
                current_request.url,
                method=current_request.method,
            )
            for name, value in current_request.headers:
                outgoing.add_header(name, value)
            if current_request.body:
                outgoing.data = current_request.body
            setattr(outgoing, "_minicode_destination", destination)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetworkSafetyError("timeout")
            with _open_no_redirect(outgoing, timeout=remaining) as response:
                status = int(response.status)
                if 300 <= status < 400:
                    if current_request.method not in {"GET", "HEAD"}:
                        raise NetworkSafetyError("redirect_not_allowed")
                    location = (
                        response.headers.get("Location")
                        if hasattr(response.headers, "get")
                        else None
                    )
                    if (
                        not isinstance(location, str)
                        or not location
                        or "\r" in location
                        or "\n" in location
                        or "\x00" in location
                        or redirects >= MAX_REDIRECTS
                    ):
                        raise NetworkSafetyError("redirect_blocked")
                    try:
                        target = urljoin(current_request.url, location)
                        current_origin = urlsplit(current_request.url)
                        target_origin = urlsplit(target)
                        current_port = current_origin.port or (
                            443
                            if current_origin.scheme.casefold() == "https"
                            else 80
                        )
                        target_port = target_origin.port or (
                            443
                            if target_origin.scheme.casefold() == "https"
                            else 80
                        )
                        cross_origin = (
                            current_origin.scheme.casefold(),
                            current_origin.hostname,
                            current_port,
                        ) != (
                            target_origin.scheme.casefold(),
                            target_origin.hostname,
                            target_port,
                        )
                        sensitive = {
                            "authorization",
                            "cookie",
                            "proxy-authorization",
                            "x-api-key",
                            "api-key",
                        }
                        candidate_headers = {
                            name: value
                            for name, value in current_request.headers
                            if not cross_origin
                            or name.casefold() not in sensitive
                        }
                        normalized = normalize_http_request(
                            {
                                "url": target,
                                "method": current_request.method,
                                "headers": candidate_headers,
                                "timeout": current_request.timeout,
                            }
                        )
                        target_destination = validate_destination(
                            normalized.url,
                            deadline=deadline,
                        )
                    except (NetworkSafetyError, ValueError):
                        raise NetworkSafetyError("redirect_blocked") from None
                    if normalized.url in visited_urls:
                        raise NetworkSafetyError("redirect_blocked")
                    current_request = normalized
                    destination = target_destination
                    visited_urls.add(normalized.url)
                    redirects += 1
                    continue
                payload = read_bounded_response(
                    response,
                    method=current_request.method,
                    deadline=deadline,
                )
                return SafeHttpResponse(
                    status=status,
                    content_type=_safe_header_value(
                        response.headers,
                        "Content-Type",
                        default="text/plain",
                    ),
                    content_encoding=_safe_header_value(
                        response.headers,
                        "Content-Encoding",
                        default="identity",
                    ).casefold(),
                    payload=payload,
                )
    except NetworkSafetyError:
        raise
    except urllib.error.HTTPError as error:
        try:
            payload = read_bounded_response(
                error,
                method=request.method,
                deadline=deadline,
            )
            if not observe_http_status:
                raise NetworkSafetyError("http_error")
            return SafeHttpResponse(
                status=int(error.code),
                content_type=_safe_header_value(
                    error.headers,
                    "Content-Type",
                    default="text/plain",
                ),
                content_encoding=_safe_header_value(
                    error.headers,
                    "Content-Encoding",
                    default="identity",
                ).casefold(),
                payload=payload,
            )
        except NetworkSafetyError:
            if observe_http_status:
                raise
            raise NetworkSafetyError("http_error") from None
        finally:
            close = getattr(error, "close", None)
            if callable(close):
                close()
    except (ssl.SSLError, ssl.CertificateError) as error:
        raise NetworkSafetyError("tls_error") from error
    except (TimeoutError, socket.timeout) as error:
        raise NetworkSafetyError("timeout") from error
    except (urllib.error.URLError, ConnectionError, OSError) as error:
        raise NetworkSafetyError("network_unavailable") from error
    except Exception as error:
        raise NetworkSafetyError("request_failed") from error


def execute_safe_http(
    request: NormalizedHttpRequest,
    *,
    deadline: float,
    destination: ValidatedDestination | None = None,
) -> SafeHttpResponse:
    """Execute one bounded request with the established >=400 error mapping."""
    response = _execute_safe_http_response(
        request,
        deadline=deadline,
        destination=destination,
        observe_http_status=False,
    )
    if response.status >= 400:
        raise NetworkSafetyError("http_error")
    return response


def execute_safe_get_response(
    request: NormalizedHttpRequest,
    *,
    deadline: float,
) -> SafeHttpResponse:
    """Return a bounded safe GET response while preserving its final status."""
    if request.method != "GET":
        raise NetworkSafetyError("invalid_request")
    return _execute_safe_http_response(
        request,
        deadline=deadline,
        observe_http_status=True,
    )


def execute_safe_get(
    request: NormalizedHttpRequest,
    *,
    deadline: float,
) -> SafeHttpResponse:
    """Execute a bounded safe GET through the shared transport seam."""
    if request.method != "GET":
        raise NetworkSafetyError("invalid_request")
    return execute_safe_http(request, deadline=deadline)


def _validate_http_request(input_data: dict):
    """Validate input for http_request tool."""
    return normalize_http_request(input_data)


def _run_http_request(
    input_data: NormalizedHttpRequest,
    context: ToolContext,
) -> ToolResult:
    """Make an HTTP request."""
    url = input_data.url
    method = input_data.method
    timeout = input_data.timeout
    deadline = time.monotonic() + timeout

    try:
        destination = validate_destination(url, deadline=deadline)
    except NetworkSafetyError as error:
        return ToolResult(ok=False, output=error.tool_output())

    if method in {"POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        review = build_network_review(input_data)
        if context.permissions is None:
            return ToolResult(
                ok=False,
                output=NetworkSafetyError("permission_required").tool_output(),
            )
        try:
            authorized = context.permissions.ensure_network(review)
        except NetworkPermissionError as error:
            return ToolResult(
                ok=False,
                output=NetworkSafetyError(error.code).tool_output(),
            )
        if authorized != review["requestFingerprint"]:
            return ToolResult(
                ok=False,
                output=NetworkSafetyError("permission_denied").tool_output(),
            )
        try:
            destination = validate_destination(url, deadline=deadline)
        except NetworkSafetyError as error:
            return ToolResult(ok=False, output=error.tool_output())
        context.permissions.ensure_operation_active()
    
    try:
        response = execute_safe_http(
            input_data,
            deadline=deadline,
            destination=destination,
        )
        return ToolResult(
            ok=True,
            output=render_safe_response(
                status=response.status,
                headers={"Content-Type": response.content_type},
                payload=response.payload,
            ),
        )
    except NetworkSafetyError as error:
        return ToolResult(ok=False, output=error.tool_output())


http_request_tool = ToolDefinition(
    name="http_request",
    description="Make HTTP requests (GET, POST, PUT, DELETE, etc.). Supports custom headers and JSON body.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Request URL"},
            "method": {"type": "string", "description": "HTTP method: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"},
            "headers": {"type": "object", "description": "Request headers as key-value pairs"},
            "body": {"type": "string", "description": "Request body (for POST, PUT, PATCH)"},
            "timeout": {"type": "number", "description": "Request timeout in seconds (default: 30)"}
        },
        "required": ["url"]
    },
    validator=_validate_http_request,
    run=_run_http_request,
)
