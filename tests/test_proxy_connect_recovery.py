"""Exercise urllib's actual CONNECT failure, without a provider or TCP listener."""

import errno
import socket
import ssl
import threading
import urllib.error
import urllib.request

import pytest

from minicode.agent_loop import _is_transient_model_transport_error


def _proxy_connect_error(monkeypatch, status, reason):
    client, proxy = socket.socketpair()
    client.settimeout(3)
    proxy.settimeout(3)
    requests = []
    server_errors = []

    def serve():
        try:
            with proxy:
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    part = proxy.recv(1024)
                    if not part:
                        raise AssertionError("Client closed before sending CONNECT")
                    request.extend(part)
                requests.append(bytes(request))
                proxy.sendall(
                    f"HTTP/1.1 {status} {reason}\r\n"
                    "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
                )
        except BaseException as error:
            server_errors.append(error)

    class LocalStream:
        def __getattr__(self, name):
            return getattr(client, name)

        def setsockopt(self, level, option, value):
            # AF_UNIX has no Nagle setting. All HTTP parsing remains stdlib.
            if (level, option) == (socket.IPPROTO_TCP, socket.TCP_NODELAY):
                return
            return client.setsockopt(level, option, value)

    connections = []

    def connect_local_stream(address, *args, **kwargs):
        connections.append(address)
        assert address == ("127.0.0.1", 54321)
        return LocalStream()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(socket, "create_connection", connect_local_stream)
    # Explicit proxy prevents machine proxy/no_proxy settings affecting the test.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        "https://provider.invalid/chat/completions", data=b"{}", method="POST"
    )
    request.set_proxy("127.0.0.1:54321", "http")
    try:
        with pytest.raises(urllib.error.URLError) as caught:
            opener.open(request, timeout=3)
    finally:
        client.close()
        thread.join(3)
    assert not thread.is_alive()
    assert not server_errors
    assert connections == [("127.0.0.1", 54321)]
    assert len(requests) == 1
    assert requests[0].startswith(b"CONNECT provider.invalid:443 HTTP/")
    assert b"POST " not in requests[0]  # No provider HTTP request was sent.
    return caught.value


@pytest.mark.parametrize(
    ("status", "reason", "retryable"),
    [
        (502, "Bad Gateway", True),
        (503, "Service Unavailable", True),
        (504, "Gateway Timeout", True),
        (403, "Forbidden", False),
        (407, "Proxy Authentication Required", False),
    ],
)
def test_real_stdlib_connect_failure_classification(monkeypatch, status, reason, retryable):
    error = _proxy_connect_error(monkeypatch, status, reason)
    assert not isinstance(error, urllib.error.HTTPError)
    assert type(error.reason) is OSError
    assert error.reason.errno is None
    assert str(error.reason) == f"Tunnel connection failed: {status} {reason}"
    assert _is_transient_model_transport_error(error) is retryable


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("Tunnel connection failed: 503 Service Unavailable"),
        OSError("Tunnel connection failed: 503 Service Unavailable"),
        urllib.error.URLError(OSError("unrelated service reported 503")),
        urllib.error.URLError(OSError("prefix Tunnel connection failed: 503 unavailable")),
        urllib.error.URLError(OSError("Tunnel connection failed: 5030 unavailable")),
        urllib.error.URLError(OSError("Tunnel connection failed: 503 unavailable\nother error")),
        urllib.error.URLError(OSError(errno.EIO, "Tunnel connection failed: 503 unavailable")),
        urllib.error.URLError(PermissionError("Tunnel connection failed: 503 unavailable")),
        urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed")),
        urllib.error.URLError(ssl.SSLError("Tunnel connection failed: 503 unavailable")),
        urllib.error.HTTPError("https://provider.invalid", 503, "Unavailable", {}, None),
    ],
    ids=[
        "untyped-reason", "no-urlerror-wrapper", "unrelated-text", "extra-prefix",
        "not-three-digit-status", "multiline", "other-errno", "permission-error",
        "certificate-error", "tls-with-similar-text", "provider-http-error",
    ],
)
def test_connect_retry_does_not_accept_unrelated_errors(error):
    assert _is_transient_model_transport_error(error) is False
