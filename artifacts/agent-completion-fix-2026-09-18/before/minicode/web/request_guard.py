"""Authorization guard for gateway endpoints that can drive agent execution.

``POST /run`` and the Chat turn endpoints hand a prompt to the local agent —
which can run commands and edit files — so they must never be reachable by
arbitrary network peers or by cross-site requests from a browser. The guard
enforces, in order:

1. A shared-secret token (``MINI_CODE_GATEWAY_TOKEN``) always authorizes the
   request, regardless of bind address (constant-time comparison).
2. Without a token, the server must be bound to a loopback address; non-local
   binds (e.g. ``0.0.0.0``) are rejected outright.
3. On loopback binds, the ``Host`` header must itself be local (DNS-rebinding
   defence) and any ``Origin`` header must be same-origin loopback (CSRF
   defence for browser-initiated requests).
"""

from __future__ import annotations

import hmac
import os
from typing import Any
from urllib.parse import urlsplit

from minicode.permission_approval import is_loopback_gateway_host

GATEWAY_TOKEN_ENV = "MINI_CODE_GATEWAY_TOKEN"
_MAX_HEADER_LENGTH = 512


def _single_header(handler: Any, name: str) -> str | None:
    values = handler.headers.get_all(name, [])
    if len(values) != 1 or not isinstance(values[0], str):
        return None
    if len(values[0]) > _MAX_HEADER_LENGTH:
        return None
    return values[0]


def _bound_host(handler: Any) -> str:
    try:
        return str(handler.server.server_address[0])
    except (AttributeError, IndexError, TypeError):
        return ""


def _host_header_is_local(handler: Any) -> bool:
    raw = _single_header(handler, "Host")
    if not raw:
        return False
    try:
        host = urlsplit("//" + raw)
        host.port  # noqa: B018 - validates the port, raising ValueError when malformed
    except ValueError:
        return False
    return isinstance(host.hostname, str) and is_loopback_gateway_host(host.hostname)


def _origin_is_local(handler: Any) -> bool:
    values = handler.headers.get_all("Origin", [])
    if not values:
        # Non-browser clients (curl, SDKs) send no Origin; the Host check
        # above still applies.
        return True
    if len(values) != 1 or not isinstance(values[0], str) or len(values[0]) > _MAX_HEADER_LENGTH:
        return False
    host_raw = _single_header(handler, "Host")
    if not host_raw:
        return False
    try:
        origin = urlsplit(values[0])
        origin_port = origin.port
        host = urlsplit("//" + host_raw)
        host_port = host.port
    except ValueError:
        return False
    return (
        origin.scheme == "http"
        and origin.username is None
        and origin.password is None
        and origin.path in {"", "/"}
        and not origin.query
        and not origin.fragment
        and isinstance(origin.hostname, str)
        and isinstance(host.hostname, str)
        and origin.hostname.casefold() == host.hostname.casefold()
        and origin_port == host_port
        and is_loopback_gateway_host(origin.hostname)
    )


def _token_authorized(handler: Any) -> bool:
    expected = os.environ.get(GATEWAY_TOKEN_ENV, "")
    if not expected:
        return False
    provided = _single_header(handler, "Authorization")
    if not provided or not provided.startswith("Bearer "):
        return False
    return hmac.compare_digest(provided[len("Bearer "):].strip(), expected)


def ensure_execution_authorized(handler: Any) -> bool:
    """Gate an execution-capable endpoint.

    Returns True when the request may proceed. Otherwise sends a JSON error
    response on the handler and returns False.
    """
    if _token_authorized(handler):
        return True
    if not is_loopback_gateway_host(_bound_host(handler)):
        handler._send_json(
            {
                "ok": False,
                "error": (
                    "execution endpoints require a loopback bind address or "
                    f"a shared secret via {GATEWAY_TOKEN_ENV}"
                ),
            },
            status=403,
        )
        return False
    if not _host_header_is_local(handler):
        handler._send_json({"ok": False, "error": "invalid Host header"}, status=403)
        return False
    if not _origin_is_local(handler):
        handler._send_json({"ok": False, "error": "cross-origin request rejected"}, status=403)
        return False
    return True


__all__ = ["GATEWAY_TOKEN_ENV", "ensure_execution_authorized"]
