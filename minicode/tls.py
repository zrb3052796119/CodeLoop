"""Verified TLS construction shared by all outbound HTTPS adapters."""

from __future__ import annotations

import http.client
import math
import ssl
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any


_SYSTEM_CA_CANDIDATES = (
    Path("/etc/ssl/cert.pem"),
    Path("/etc/ssl/certs/ca-certificates.crt"),
    Path("/etc/pki/tls/certs/ca-bundle.crt"),
)


def _ca_count(context: ssl.SSLContext) -> int:
    try:
        value = context.cert_store_stats().get("x509_ca", 0)
    except (AttributeError, TypeError, ValueError, ssl.SSLError):
        return 0
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@lru_cache(maxsize=1)
def create_verified_ssl_context() -> ssl.SSLContext:
    """Return a hostname-checking, certificate-verifying TLS context."""
    context = ssl.create_default_context()

    # Some python.org macOS installations point OpenSSL at a Python-prefix CA
    # path that does not exist, yielding a context with zero trust anchors even
    # though the operating system provides a maintained bundle. Load only a
    # known system CA bundle, and only when the default store is actually empty.
    # This supplements verification; it never disables hostname or certificate
    # checks.
    if _ca_count(context) == 0:
        for candidate in _SYSTEM_CA_CANDIDATES:
            if not candidate.is_file():
                continue
            try:
                context.load_verify_locations(cafile=str(candidate))
            except (OSError, ssl.SSLError):
                continue
            if _ca_count(context) > 0:
                break

    try:
        import certifi
    except ImportError:
        return context
    # Keep operating-system and enterprise trust roots, then supplement them
    # with certifi for Python installations whose bundled CA store is empty.
    context.load_verify_locations(cafile=certifi.where())
    return context


class _ConnectTimeout:
    """Separate TCP/proxy CONNECT/TLS setup from connected response waiting."""

    def __init__(self, *args, connect_timeout, **kwargs):
        super().__init__(*args, **kwargs)
        self._connect_timeout = connect_timeout

    def connect(self):
        response_timeout = self.timeout
        self.timeout = min(self._connect_timeout, response_timeout)
        try:
            # Standard HTTP(S) connection retains proxy tunnelling, SNI,
            # hostname checks and certificate verification.
            super().connect()
        finally:
            self.timeout = response_timeout
        self.sock.settimeout(response_timeout)


class _PhasedHTTPConnection(_ConnectTimeout, http.client.HTTPConnection):
    pass


class _PhasedHTTPSConnection(_ConnectTimeout, http.client.HTTPSConnection):
    pass


def _open_with_connect_timeout(request, *, timeout, connect_timeout):
    if (isinstance(connect_timeout, bool) or not isinstance(connect_timeout, (int, float))
            or not math.isfinite(connect_timeout) or connect_timeout <= 0):
        raise ValueError("model connect timeout must be positive and finite")

    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(lambda host, **kwargs: _PhasedHTTPConnection(
                host, connect_timeout=connect_timeout, **kwargs), req)

    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(lambda host, **kwargs: _PhasedHTTPSConnection(
                host, connect_timeout=connect_timeout, **kwargs), req,
                context=self._context)

    # build_opener retains urllib's normal proxy, CONNECT, HTTP-error and
    # redirect handlers. Every redirected connection uses the same policy.
    opener = urllib.request.build_opener(HTTPHandler(), HTTPSHandler(context=create_verified_ssl_context()))
    return opener.open(request, timeout=timeout)


def open_verified_url(request: Any, *, timeout: float):
    """Open a URL with verified TLS while preserving simple test adapters."""
    connect_timeout = getattr(request, "_minicode_connect_timeout_seconds", None)
    if connect_timeout is not None:
        return _open_with_connect_timeout(request, timeout=timeout, connect_timeout=connect_timeout)
    opener = urllib.request.urlopen
    try:
        return opener(
            request,
            timeout=timeout,
            context=create_verified_ssl_context(),
        )
    except TypeError as error:
        # Existing boundary fakes often implement the historic two-argument
        # urllib interface. Real urllib accepts ``context``; only retry a fake
        # whose callable rejected that keyword at argument binding time.
        if "context" not in str(error):
            raise
        return opener(request, timeout=timeout)


__all__ = ["create_verified_ssl_context", "open_verified_url"]
