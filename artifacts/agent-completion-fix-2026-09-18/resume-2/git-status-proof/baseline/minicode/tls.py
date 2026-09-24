"""Verified TLS construction shared by all outbound HTTPS adapters."""

from __future__ import annotations

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


def open_verified_url(request: Any, *, timeout: float):
    """Open a URL with verified TLS while preserving simple test adapters."""
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
