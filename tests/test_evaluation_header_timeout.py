"""Real loopback HTTP with slow headers; no provider or project content."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import urllib.error

import pytest

from minicode.openai_adapter import OpenAIModelAdapter
from minicode.model_call_control import ModelCallDeadlineExceeded
from scripts.evaluation_transport import EvaluationTransport, TransportConfig, route_fingerprint


@pytest.fixture
def endpoint(request, tmp_path):
    observed = {'delay': .22, 'arrived': threading.Event()}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            observed['arrived'].set()
            if observed.get('redirect'):
                self.send_response(302)
                self.send_header('Location', '/redirected-stream')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.do_GET()
        def do_GET(self):
            observed['finalPath'] = self.path
            time.sleep(observed['delay'])
            body = b'data: ' + json.dumps({'choices': [{'delta': {'content': 'ok'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}}).encode() + b'\n\ndata: [DONE]\n\n'
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    tls_enabled = getattr(request, 'param', None) == 'tls'
    if tls_enabled:
        import datetime
        import ipaddress
        import ssl
        x509 = pytest.importorskip('cryptography.x509', reason='ephemeral local TLS certificate fixture')
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
            .not_valid_after(now+datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
        cert_file, key_file = tmp_path/'server.crt', tmp_path/'server.key'
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        observed['certificate'] = cert_file
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    worker.start()
    runtime = {'model': 'generic-local-fixture', 'openaiBaseUrl': f'{"https" if tls_enabled else "http"}://127.0.0.1:{server.server_port}/v1',
        'openaiApiKey': 'local-fixture-no-credential', 'modelTimeoutSeconds': .01, 'modelMaxRetries': 0}
    try:
        yield runtime, observed
    finally:
        server.shutdown()
        server.server_close()
        worker.join(1)


def transport(tmp_path, runtime, **overrides):
    return EvaluationTransport(TransportConfig(ledger_path=str(tmp_path/'calls.sqlite'),
        expected_model=runtime['model'], expected_route_sha256=route_fingerprint(runtime),
        deadline_epoch=None, request_timeout_seconds=overrides.pop('request_timeout_seconds', None),
        connect_timeout_seconds=.05, idle_timeout_seconds=overrides.pop('idle_timeout_seconds', .5),
        max_total_tokens=None, max_model_calls=None, **overrides))


def invoke(runtime):
    return OpenAIModelAdapter(runtime, None).next([{'role': 'user', 'content': 'Return the fixed local fixture.'}])


def test_connected_slow_header_survives_connect_timer_without_total_deadline(endpoint, tmp_path):
    runtime, observed = endpoint
    with transport(tmp_path, runtime) as current:
        result = invoke(runtime)
        row = current.ledger.snapshot()['requests'][0]
    assert observed['arrived'].is_set() and result.content == 'ok'
    assert row['headersSeconds'] >= .20 > .05
    assert row['requestDeadlineEpoch'] is None and row['event'] == 'completed'
    assert row['usage']['source'] == 'provider' and row['httpStatus'] == 200


def test_header_idle_limit_is_still_enforced(endpoint, tmp_path):
    runtime, observed = endpoint
    observed['delay'] = .3
    started = time.monotonic()
    with transport(tmp_path, runtime, idle_timeout_seconds=.12) as current:
        with pytest.raises((TimeoutError, urllib.error.URLError)):
            invoke(runtime)
        row = current.ledger.snapshot()['requests'][0]
    elapsed = time.monotonic() - started
    assert observed['arrived'].is_set()
    assert .10 <= elapsed < .28
    assert row['errorPhase'] == 'headers' and row['usage']['source'] == 'unavailable'


def test_real_connect_timeout_is_not_replaced_by_header_idle(endpoint, tmp_path, monkeypatch):
    import socket
    runtime, _ = endpoint
    captured = []
    def unavailable(address, timeout, *args, **kwargs):
        captured.append(timeout)
        raise TimeoutError('generic simulated TCP connect failure')
    monkeypatch.setattr(socket, 'create_connection', unavailable)
    with transport(tmp_path, runtime) as current:
        with pytest.raises((TimeoutError, urllib.error.URLError)):
            invoke(runtime)
        assert current.ledger.snapshot()['failed'] == 1
    assert captured == [.05]


def test_explicit_total_deadline_still_bounds_slow_headers(endpoint, tmp_path):
    runtime, observed = endpoint
    observed['delay'] = .3
    started = time.monotonic()
    with transport(tmp_path, runtime, request_timeout_seconds=.09) as current:
        with pytest.raises(ModelCallDeadlineExceeded):
            invoke(runtime)
        row = current.ledger.snapshot()['requests'][0]
    assert observed['arrived'].is_set() and time.monotonic() - started < .25
    assert row['requestDeadlineEpoch'] is not None
    assert row['usage']['source'] == 'unavailable'


def test_urllib_redirect_still_uses_separate_connection_and_header_timeouts(endpoint, tmp_path):
    runtime, observed = endpoint
    observed['redirect'] = True
    with transport(tmp_path, runtime):
        assert invoke(runtime).content == 'ok'
    assert observed['finalPath'] == '/redirected-stream'


@pytest.mark.parametrize('endpoint', ['tls'], indirect=True)
def test_verified_https_slow_headers_keep_hostname_and_certificate_checks(endpoint, tmp_path, monkeypatch):
    import ssl
    import minicode.tls as tls
    runtime, observed = endpoint
    context = ssl.create_default_context(cafile=str(observed['certificate']))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    monkeypatch.setattr(tls, 'create_verified_ssl_context', lambda: context)
    with transport(tmp_path, runtime) as current:
        assert invoke(runtime).content == 'ok'
        assert current.ledger.snapshot()['requests'][0]['headersSeconds'] >= .20
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.parametrize('endpoint', ['tls'], indirect=True)
def test_untrusted_https_certificate_is_still_rejected(endpoint, tmp_path):
    import ssl
    runtime, observed = endpoint
    with transport(tmp_path, runtime) as current:
        with pytest.raises(urllib.error.URLError) as error:
            invoke(runtime)
        assert isinstance(error.value.reason, ssl.SSLCertVerificationError)
        assert current.ledger.snapshot()['failed'] == 1
    assert not observed['arrived'].is_set()


@pytest.mark.parametrize('kind', ['tls-handshake', 'proxy-connect'])
def test_real_tls_and_proxy_connect_stalls_use_connect_limit(tmp_path, monkeypatch, kind):
    import socket
    accepted = threading.Event()
    stopped = threading.Event()
    observed = []
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    def stall():
        connection, _ = server.accept()
        with connection:
            observed.append(connection.recv(4096))
            accepted.set()
            stopped.wait(.3)
    worker = threading.Thread(target=stall, daemon=True)
    worker.start()
    address = f'127.0.0.1:{server.getsockname()[1]}'
    runtime = {'model': 'generic-local-fixture', 'openaiBaseUrl': f'https://{address}/v1',
        'openaiApiKey': 'local-no-credential', 'modelTimeoutSeconds': .01, 'modelMaxRetries': 0}
    if kind == 'proxy-connect':
        runtime['openaiBaseUrl'] = 'https://fixture.invalid/v1'
        monkeypatch.setattr('urllib.request.getproxies', lambda: {'https': 'http://' + address})
        monkeypatch.setattr('urllib.request.proxy_bypass', lambda host: False)
    started = time.monotonic()
    try:
        with transport(tmp_path, runtime) as current:
            with pytest.raises((TimeoutError, urllib.error.URLError)):
                invoke(runtime)
            assert current.ledger.snapshot()['failed'] == 1
        assert accepted.wait(.1) and time.monotonic() - started < .22
        if kind == 'proxy-connect':
            assert observed[0].startswith(b'CONNECT fixture.invalid:443 HTTP/')
        else:
            assert observed[0][0] == 22  # Actual TLS ClientHello record.
    finally:
        stopped.set()
        server.close()
        worker.join(1)
