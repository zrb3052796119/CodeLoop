from __future__ import annotations

import threading
import time
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import minicode.tools.http_utils as http_utils
import minicode.tools.network_safety as network_safety
from minicode.tooling import ToolContext, ToolRegistry
from minicode.permissions import NetworkPermissionError, PermissionManager
from minicode.permission_approval import PermissionApprovalBroker
from minicode.tools.bounded_resolver import BoundedResolver
from minicode.tools.http_utils import http_request_tool
from minicode.turn_cancellation import TurnCancellationToken
from minicode.turn_cancellation import TurnCancellationRequested


@pytest.fixture(autouse=True)
def _deterministic_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    original = network_safety.socket.getaddrinfo

    def resolve(host: str, port: int, **kwargs):
        if host.endswith(".example"):
            return [
                (
                    network_safety.socket.AF_INET,
                    network_safety.socket.SOCK_STREAM,
                    network_safety.socket.IPPROTO_TCP,
                    "",
                    ("93.184.216.34", port),
                )
            ]
        return original(host, port, **kwargs)

    monkeypatch.setattr(network_safety.socket, "getaddrinfo", resolve)


class _MutationFixture(BaseHTTPRequestHandler):
    calls: list[tuple[str, str]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        self.__class__.calls.append(("POST", self.path))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"changed":true}')

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _mutation_server() -> Iterator[tuple[str, list[tuple[str, str]]]]:
    _MutationFixture.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MutationFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", _MutationFixture.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unapproved_loopback_post_has_zero_network_side_effect(
    tmp_path: Path,
) -> None:
    with _mutation_server() as (base_url, calls):
        result = ToolRegistry([http_request_tool]).execute(
            "http_request",
            {
                "url": f"{base_url}/mutation?fixture-secret=hidden",
                "method": "POST",
                "body": '{"change":true}',
                "timeout": 2,
            },
            ToolContext(cwd=str(tmp_path), permissions=None),
        )

    assert result.ok is False
    assert result.output == (
        "error[destination_blocked]: The request destination is not allowed."
    )
    assert calls == []
    assert "fixture-secret" not in result.output


class _SmallResponse:
    status = 200
    headers = {"Content-Type": "application/json", "Content-Length": "11"}

    def __enter__(self) -> "_SmallResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return b'{"ok":true}'


class _RedirectResponse(_SmallResponse):
    status = 302
    headers = {
        "Content-Type": "text/plain",
        "Content-Length": "0",
        "Location": "https://other.public.example/redirected",
    }

    def read(self, _size: int = -1) -> bytes:
        return b""


class _PrivateRedirectResponse(_RedirectResponse):
    headers = {
        "Content-Type": "text/plain",
        "Content-Length": "0",
        "Location": "http://127.0.0.1/internal?redirect-secret=hidden",
    }


def test_denied_public_https_post_has_zero_network_side_effect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    network_calls: list[object] = []
    permission_requests: list[dict[str, object]] = []

    def deny(request: dict[str, object]) -> dict[str, str]:
        permission_requests.append(request)
        return {"decision": "deny_once"}

    def fake_urlopen(request: object, *, timeout: float) -> _SmallResponse:
        network_calls.append((request, timeout))
        return _SmallResponse()

    monkeypatch.setattr("minicode.tools.http_utils._open_no_redirect", fake_urlopen)
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://public.example/mutation?private=hidden",
                "method": "POST",
                "body": '{"change":true}',
                "timeout": 2,
            }
        ),
        ToolContext(
            cwd=str(tmp_path),
            permissions=PermissionManager(str(tmp_path), prompt=deny),
        ),
    )

    assert result.ok is False
    assert result.output == (
        "error[permission_denied]: The network request was denied."
    )
    assert len(permission_requests) == 1
    assert network_calls == []
    assert "private=hidden" not in str(permission_requests)


def test_permission_manager_rejects_unsafe_network_review_before_prompt(
    tmp_path: Path,
) -> None:
    prompts: list[dict[str, object]] = []
    manager = PermissionManager(
        str(tmp_path),
        prompt=lambda request: prompts.append(request) or {"decision": "allow_once"},
    )

    with pytest.raises(NetworkPermissionError) as blocked:
        manager.ensure_network(
            {
                "reviewVersion": 1,
                "method": "POST",
                "scheme": "https",
                "hostname": "api.public.example",
                "port": 443,
                "pathSummary": "/mutate\nfixture-secret",
                "hasBody": True,
                "hasSensitiveHeaders": False,
                "requestFingerprint": "networkreq_" + "a" * 64,
            }
        )

    assert blocked.value.code == "permission_required"
    assert prompts == []


def _pending_network_item(
    broker: PermissionApprovalBroker,
) -> dict[str, object]:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        items = broker.snapshot()["items"]
        if items:
            return items[0]
        time.sleep(0.005)
    raise AssertionError("network approval did not become pending")


def test_allow_once_is_bound_to_one_exact_network_operation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    network_calls: list[str] = []

    def fake_urlopen(request: object, *, timeout: float) -> _SmallResponse:
        del timeout
        network_calls.append(request.full_url)
        return _SmallResponse()

    monkeypatch.setattr("minicode.tools.http_utils._open_no_redirect", fake_urlopen)
    broker = PermissionApprovalBroker(tmp_path, timeout_seconds=2)
    turn_id = "turn_" + "a" * 32
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=TurnCancellationToken(turn_id),
    )
    manager = PermissionManager(str(tmp_path), prompt=session.prompt)

    def start_request(url: str) -> tuple[threading.Thread, dict[str, object]]:
        outcome: dict[str, object] = {}

        def run() -> None:
            session.tool_started("http_request")
            try:
                outcome["result"] = http_request_tool.run(
                    http_request_tool.validator(
                        {
                            "url": url,
                            "method": "POST",
                            "body": '{"change":true}',
                            "timeout": 2,
                        }
                    ),
                    ToolContext(cwd=str(tmp_path), permissions=manager),
                )
            finally:
                session.tool_finished("http_request")

        thread = threading.Thread(target=run)
        thread.start()
        return thread, outcome

    first, first_outcome = start_request(
        "https://first.public.example/mutate?approval-secret=hidden"
    )
    first_item = _pending_network_item(broker)
    assert first_item["kind"] == "network"
    assert first_item["reviewable"] is True
    assert first_item["choices"] == ["allow_once", "deny_once"]
    assert first_item["review"] == {
        "reviewVersion": 1,
        "method": "POST",
        "scheme": "https",
        "hostname": "first.public.example",
        "port": 443,
        "pathSummary": "/mutate",
        "hasBody": True,
        "hasSensitiveHeaders": False,
        "requestFingerprint": first_item["review"]["requestFingerprint"],
    }
    assert "approval-secret" not in str(first_item)
    broker.decide(
        permission_id=first_item["permissionId"],
        turn_id=turn_id,
        decision="allow_once",
    )
    first.join(timeout=1)
    assert first_outcome["result"].ok is True
    assert network_calls == [
        "https://first.public.example/mutate?approval-secret=hidden"
    ]

    second, second_outcome = start_request(
        "https://second.public.example/mutate?other-secret=hidden"
    )
    second_item = _pending_network_item(broker)
    assert second_item["permissionId"] != first_item["permissionId"]
    assert (
        second_item["review"]["requestFingerprint"]
        != first_item["review"]["requestFingerprint"]
    )
    assert network_calls == [
        "https://first.public.example/mutate?approval-secret=hidden"
    ]
    broker.decide(
        permission_id=second_item["permissionId"],
        turn_id=turn_id,
        decision="deny_once",
    )
    second.join(timeout=1)
    assert second_outcome["result"].ok is False
    assert network_calls == [
        "https://first.public.example/mutate?approval-secret=hidden"
    ]
    session.close()
    broker.close()


def test_cancel_timeout_close_and_unavailable_have_zero_network_side_effect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    network_calls: list[object] = []

    def forbidden_urlopen(request: object, *, timeout: float) -> _SmallResponse:
        network_calls.append((request, timeout))
        return _SmallResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", forbidden_urlopen
    )

    def start(
        broker: PermissionApprovalBroker,
        token: TurnCancellationToken,
        turn_id: str,
    ) -> tuple[threading.Thread, dict[str, object], object]:
        session = broker.begin_turn(
            turn_id=turn_id,
            run_id=None,
            cancellation_token=token,
        )
        manager = PermissionManager(str(tmp_path), prompt=session.prompt)
        outcome: dict[str, object] = {}

        def run() -> None:
            session.tool_started("http_request")
            try:
                outcome["result"] = http_request_tool.run(
                    http_request_tool.validator(
                        {
                            "url": "https://public.example/mutate",
                            "method": "POST",
                            "body": "bounded",
                            "timeout": 2,
                        }
                    ),
                    ToolContext(cwd=str(tmp_path), permissions=manager),
                )
            except BaseException as error:  # noqa: BLE001 - control-flow evidence
                outcome["error"] = error
            finally:
                session.tool_finished("http_request")

        thread = threading.Thread(target=run)
        thread.start()
        return thread, outcome, session

    cancel_broker = PermissionApprovalBroker(tmp_path, timeout_seconds=2)
    cancel_turn = "turn_" + "b" * 32
    cancel_token = TurnCancellationToken(cancel_turn)
    cancel_thread, cancel_outcome, cancel_session = start(
        cancel_broker, cancel_token, cancel_turn
    )
    _pending_network_item(cancel_broker)
    cancel_token.request()
    cancel_broker.cancel_turn(cancel_turn)
    cancel_thread.join(timeout=1)
    assert isinstance(cancel_outcome.get("error"), TurnCancellationRequested)
    cancel_session.close()
    cancel_broker.close()

    monotonic = [0.0]
    timeout_broker = PermissionApprovalBroker(
        tmp_path,
        timeout_seconds=1,
        monotonic=lambda: monotonic[0],
    )
    timeout_turn = "turn_" + "c" * 32
    timeout_thread, timeout_outcome, timeout_session = start(
        timeout_broker,
        TurnCancellationToken(timeout_turn),
        timeout_turn,
    )
    _pending_network_item(timeout_broker)
    monotonic[0] = 2.0
    timeout_broker.snapshot()
    timeout_thread.join(timeout=1)
    assert timeout_outcome["result"].ok is False
    assert timeout_outcome["result"].output == (
        "error[permission_expired]: The network approval expired."
    )
    timeout_session.close()
    timeout_broker.close()

    close_broker = PermissionApprovalBroker(tmp_path, timeout_seconds=2)
    close_turn = "turn_" + "d" * 32
    close_thread, close_outcome, close_session = start(
        close_broker,
        TurnCancellationToken(close_turn),
        close_turn,
    )
    _pending_network_item(close_broker)
    close_broker.close()
    close_thread.join(timeout=1)
    assert close_outcome["result"].ok is False
    assert close_outcome["result"].output == (
        "error[permission_unavailable]: Network approval is unavailable."
    )
    close_session.close()

    unavailable = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://public.example/mutate",
                "method": "POST",
                "body": "bounded",
                "timeout": 2,
            }
        ),
        ToolContext(
            cwd=str(tmp_path),
            permissions=PermissionManager(str(tmp_path), prompt=None),
        ),
    )
    assert unavailable.ok is False
    assert unavailable.output == (
        "error[permission_required]: The network request requires approval."
    )
    assert network_calls == []


def test_allow_then_cancel_at_final_network_checkpoint_has_zero_side_effect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    network_calls: list[object] = []

    def forbidden_urlopen(request: object, *, timeout: float) -> _SmallResponse:
        network_calls.append((request, timeout))
        return _SmallResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", forbidden_urlopen
    )
    turn_id = "turn_" + "e" * 32
    token = TurnCancellationToken(turn_id)
    broker = PermissionApprovalBroker(tmp_path, timeout_seconds=2)
    session = broker.begin_turn(
        turn_id=turn_id,
        run_id=None,
        cancellation_token=token,
    )
    manager = PermissionManager(str(tmp_path), prompt=session.prompt)
    at_final_checkpoint = threading.Event()
    continue_checkpoint = threading.Event()

    def checkpoint() -> None:
        at_final_checkpoint.set()
        assert continue_checkpoint.wait(1)
        session.check_operation()

    manager.operation_checkpoint = checkpoint
    outcome: dict[str, object] = {}

    def run() -> None:
        session.tool_started("http_request")
        try:
            outcome["result"] = http_request_tool.run(
                http_request_tool.validator(
                    {
                        "url": "https://public.example/mutate",
                        "method": "POST",
                        "body": "bounded",
                        "timeout": 2,
                    }
                ),
                ToolContext(cwd=str(tmp_path), permissions=manager),
            )
        except BaseException as error:  # noqa: BLE001 - cancellation evidence
            outcome["error"] = error
        finally:
            session.tool_finished("http_request")

    thread = threading.Thread(target=run)
    thread.start()
    item = _pending_network_item(broker)
    broker.decide(
        permission_id=item["permissionId"],
        turn_id=turn_id,
        decision="allow_once",
    )
    assert at_final_checkpoint.wait(1)
    assert network_calls == []
    token.request()
    continue_checkpoint.set()
    thread.join(timeout=1)

    assert isinstance(outcome.get("error"), TurnCancellationRequested)
    assert network_calls == []
    session.close()
    broker.close()


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://127.0.0.1/", "destination_blocked"),
        ("http://127.0.0.2/", "destination_blocked"),
        ("http://10.0.0.1/", "destination_blocked"),
        ("http://172.16.0.1/", "destination_blocked"),
        ("http://192.168.0.1/", "destination_blocked"),
        ("http://169.254.1.1/", "destination_blocked"),
        ("http://0.0.0.0/", "destination_blocked"),
        ("http://224.0.0.1/", "destination_blocked"),
        ("http://240.0.0.1/", "destination_blocked"),
        ("http://[::1]/", "destination_blocked"),
        ("http://[fc00::1]/", "destination_blocked"),
        ("http://[fe80::1]/", "destination_blocked"),
        ("http://[::ffff:127.0.0.1]/", "destination_blocked"),
        ("https://user:password@public.example/", "invalid_request"),
        ("https://public.example:99999/", "invalid_request"),
        ("ftp://public.example/", "unsupported_scheme"),
    ],
)
def test_destination_policy_rejects_unsafe_url_classes(
    url: str,
    code: str,
) -> None:
    with pytest.raises(network_safety.NetworkSafetyError) as blocked:
        network_safety.validate_destination(url)

    assert blocked.value.code == code
    assert url not in blocked.value.tool_output()


def test_destination_policy_rejects_any_unsafe_dns_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mixed_answers(host: str, port: int, **_kwargs):
        assert host == "mixed.public.example"
        return [
            (
                network_safety.socket.AF_INET,
                network_safety.socket.SOCK_STREAM,
                network_safety.socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
            (
                network_safety.socket.AF_INET,
                network_safety.socket.SOCK_STREAM,
                network_safety.socket.IPPROTO_TCP,
                "",
                ("10.0.0.8", port),
            ),
        ]

    monkeypatch.setattr(network_safety.socket, "getaddrinfo", mixed_answers)
    with pytest.raises(network_safety.NetworkSafetyError) as blocked:
        network_safety.validate_destination(
            "https://mixed.public.example/path?dns-secret=hidden"
        )

    assert blocked.value.code == "destination_blocked"
    assert "dns-secret" not in blocked.value.tool_output()


def test_destination_policy_returns_safe_dns_error_without_raw_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_dns(*_args, **_kwargs):
        raise network_safety.socket.gaierror(
            "resolver leaked Authorization=fixture-secret"
        )

    monkeypatch.setattr(network_safety.socket, "getaddrinfo", fail_dns)
    with pytest.raises(network_safety.NetworkSafetyError) as blocked:
        network_safety.validate_destination("https://unavailable.public.example/")

    assert blocked.value.code == "dns_error"
    assert "fixture-secret" not in blocked.value.tool_output()


def test_destination_is_revalidated_after_approval_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolutions = iter(("93.184.216.34", "10.0.0.8"))
    network_calls: list[object] = []

    def changing_dns(host: str, port: int, **_kwargs):
        assert host == "changing.public.example"
        return [
            (
                network_safety.socket.AF_INET,
                network_safety.socket.SOCK_STREAM,
                network_safety.socket.IPPROTO_TCP,
                "",
                (next(resolutions), port),
            )
        ]

    def forbidden_urlopen(request: object, *, timeout: float) -> _SmallResponse:
        network_calls.append((request, timeout))
        return _SmallResponse()

    monkeypatch.setattr(network_safety.socket, "getaddrinfo", changing_dns)
    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", forbidden_urlopen
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://changing.public.example/mutate",
                "method": "POST",
                "body": "bounded",
                "timeout": 2,
            }
        ),
        ToolContext(
            cwd=str(tmp_path),
            permissions=PermissionManager(
                str(tmp_path),
                prompt=lambda _request: {"decision": "allow_once"},
            ),
        ),
    )

    assert result.ok is False
    assert result.output == (
        "error[destination_blocked]: The request destination is not allowed."
    )
    assert network_calls == []


def test_mutation_redirect_is_never_followed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[str] = []

    def redirect_once(request: object, *, timeout: float) -> _RedirectResponse:
        del timeout
        network_calls.append(request.full_url)
        return _RedirectResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", redirect_once
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://first.public.example/mutate",
                "method": "POST",
                "body": "bounded",
                "timeout": 2,
            }
        ),
        ToolContext(
            cwd=str(tmp_path),
            permissions=PermissionManager(
                str(tmp_path),
                prompt=lambda _request: {"decision": "allow_once"},
            ),
        ),
    )

    assert result.ok is False
    assert result.output == (
        "error[redirect_not_allowed]: Redirects are not allowed for this method."
    )
    assert network_calls == ["https://first.public.example/mutate"]


def test_public_get_redirect_to_private_destination_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[str] = []

    def private_redirect(
        request: object, *, timeout: float
    ) -> _PrivateRedirectResponse:
        del timeout
        network_calls.append(request.full_url)
        return _PrivateRedirectResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", private_redirect
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://first.public.example/read",
                "method": "GET",
                "timeout": 2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == (
        "error[redirect_blocked]: The redirect target is not allowed."
    )
    assert network_calls == ["https://first.public.example/read"]
    assert "redirect-secret" not in result.output


def test_get_redirect_loop_is_detected_before_repeating_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[str] = []

    class LoopResponse(_RedirectResponse):
        headers = {
            "Content-Type": "text/plain",
            "Content-Length": "0",
            "Location": "/loop",
        }

    def loop_response(request: object, *, timeout: float) -> LoopResponse:
        del timeout
        network_calls.append(request.full_url)
        return LoopResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", loop_response
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://first.public.example/loop",
                "method": "GET",
                "timeout": 2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == (
        "error[redirect_blocked]: The redirect target is not allowed."
    )
    assert network_calls == ["https://first.public.example/loop"]


def test_oversized_redirect_target_is_rejected_before_second_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[str] = []

    class OversizedRedirect(_RedirectResponse):
        headers = {
            "Content-Type": "text/plain",
            "Content-Length": "0",
            "Location": "https://other.public.example/" + "x" * 4097,
        }

    def redirect_once(request: object, *, timeout: float) -> OversizedRedirect:
        del timeout
        network_calls.append(request.full_url)
        return OversizedRedirect()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        redirect_once,
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://first.public.example/read",
                "method": "GET",
                "timeout": 2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == (
        "error[redirect_blocked]: The redirect target is not allowed."
    )
    assert network_calls == ["https://first.public.example/read"]


def test_cross_origin_get_redirect_drops_sensitive_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[object] = []

    def redirect_then_ok(request: object, *, timeout: float):
        del timeout
        requests.append(request)
        return _RedirectResponse() if len(requests) == 1 else _SmallResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", redirect_then_ok
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://first.public.example/read",
                "method": "GET",
                "headers": {
                    "Authorization": "Bearer fixture-secret",
                    "Cookie": "session=fixture-secret",
                    "X-Visible": "kept",
                },
                "timeout": 2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is True
    assert len(requests) == 2
    second_headers = {key.casefold(): value for key, value in requests[1].headers.items()}
    assert "authorization" not in second_headers
    assert "cookie" not in second_headers
    assert second_headers["x-visible"] == "kept"


def test_get_redirect_count_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[str] = []

    def endless_redirect(request: object, *, timeout: float) -> _RedirectResponse:
        del timeout
        network_calls.append(request.full_url)
        response = _RedirectResponse()
        response.headers = {
            "Content-Type": "text/plain",
            "Content-Length": "0",
            "Location": f"/hop-{len(network_calls)}",
        }
        return response

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", endless_redirect
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://first.public.example/start",
                "method": "GET",
                "timeout": 2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == (
        "error[redirect_blocked]: The redirect target is not allowed."
    )
    assert len(network_calls) == 4


@pytest.mark.parametrize(
    ("input_data", "expected_code"),
    [
        ({"url": "https://public.example/" + "x" * 4097}, "invalid_request"),
        ({"url": "https://public.example/\nsecret"}, "invalid_request"),
        ({"url": "https://public.example/", "method": True}, "invalid_request"),
        ({"url": "https://public.example/", "method": "TRACE"}, "invalid_request"),
        (
            {
                "url": "https://public.example/",
                "headers": {f"X-{index}": "ok" for index in range(33)},
            },
            "invalid_request",
        ),
        (
            {
                "url": "https://public.example/",
                "headers": {"X" * 129: "ok"},
            },
            "invalid_request",
        ),
        (
            {
                "url": "https://public.example/",
                "headers": {"X-Test": "v" * 4097},
            },
            "invalid_request",
        ),
        (
            {
                "url": "https://public.example/",
                "method": "POST",
                "headers": {f"X-Fixture-{index}": "v" for index in range(32)},
                "body": {"bounded": True},
            },
            "invalid_request",
        ),
        (
            {
                "url": "https://public.example/",
                "headers": {"X-Test": "ok\r\nAuthorization: fixture-secret"},
            },
            "invalid_request",
        ),
        (
            {
                "url": "https://public.example/",
                "headers": {"Host": "fixture-secret"},
            },
            "invalid_request",
        ),
        (
            {
                "url": "https://public.example/",
                "headers": {"Proxy-Authorization": "fixture-secret"},
            },
            "invalid_request",
        ),
        (
            {
                "url": "https://public.example/",
                "method": "POST",
                "body": "b" * (64 * 1024 + 1),
            },
            "request_body_too_large",
        ),
        (
            {
                "url": "https://public.example/",
                "method": "GET",
                "body": "fixture-secret",
            },
            "invalid_request",
        ),
        (
            {
                "url": "https://public.example/",
                "method": "POST",
                "body": True,
            },
            "invalid_request",
        ),
        ({"url": "https://public.example/", "timeout": True}, "invalid_request"),
        ({"url": "https://public.example/", "timeout": 0}, "invalid_request"),
        ({"url": "https://public.example/", "timeout": float("nan")}, "invalid_request"),
        ({"url": "https://public.example/", "timeout": float("inf")}, "invalid_request"),
        ({"url": "https://public.example/", "timeout": 30.01}, "invalid_request"),
        (
            {
                "url": "http://public.example/mutate",
                "method": "POST",
                "body": "fixture-secret",
            },
            "destination_blocked",
        ),
    ],
)
def test_request_budgets_fail_before_network_without_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_data: dict[str, object],
    expected_code: str,
) -> None:
    network_calls: list[object] = []

    def forbidden_open(request: object, *, timeout: float) -> _SmallResponse:
        network_calls.append((request, timeout))
        return _SmallResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect", forbidden_open
    )
    result = ToolRegistry([http_request_tool]).execute(
        "http_request",
        input_data,
        ToolContext(
            cwd=str(tmp_path),
            permissions=PermissionManager(
                str(tmp_path),
                prompt=lambda _request: {"decision": "allow_once"},
            ),
        ),
    )

    assert result.ok is False
    assert result.output.startswith(f"error[{expected_code}]:")
    assert "fixture-secret" not in result.output
    assert network_calls == []


def test_stream_larger_than_response_budget_uses_only_bounded_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x" * (1024 * 1024 + 1)
    read_sizes: list[int] = []

    class LargeResponse(_SmallResponse):
        headers = {"Content-Type": "text/plain"}

        def __init__(self) -> None:
            self.offset = 0

        def read(self, size: int = -1) -> bytes:
            if size < 0:
                raise AssertionError("unbounded read attempted")
            read_sizes.append(size)
            chunk = payload[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        lambda _request, *, timeout: LargeResponse(),
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://public.example/large",
                "method": "GET",
                "timeout": 2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == (
        "error[response_too_large]: The response exceeds the safe byte limit."
    )
    assert read_sizes
    assert all(0 < size <= 64 * 1024 for size in read_sizes)


def test_http_error_body_is_bounded_and_returns_fixed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"error-secret-" * (1024 * 1024 // 13 + 2)
    read_sizes: list[int] = []

    class LargeHttpError(urllib.error.HTTPError):
        def __init__(self) -> None:
            super().__init__(
                "https://public.example/error",
                500,
                "fixture-secret-reason",
                {"Content-Type": "text/plain"},
                None,
            )
            self.offset = 0

        def read(self, size: int = -1) -> bytes:
            if size < 0:
                raise AssertionError("unbounded HTTPError read attempted")
            read_sizes.append(size)
            chunk = payload[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    def fail_open(_request: object, *, timeout: float) -> _SmallResponse:
        raise LargeHttpError()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        fail_open,
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://public.example/error?fixture-secret=hidden",
                "method": "GET",
                "timeout": 2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == "error[http_error]: The server returned an HTTP error."
    assert "fixture-secret" not in result.output
    assert read_sizes
    assert all(0 < size <= 64 * 1024 for size in read_sizes)


@pytest.mark.parametrize(
    ("content_type", "payload", "expected_ok", "expected_fragment"),
    [
        (
            "application/json; charset=utf-8",
            b'{"answer":true}',
            True,
            '"answer": true',
        ),
        ("text/plain; charset=utf-8", b"safe-\xff-text", True, "safe-\ufffd-text"),
        (
            "application/octet-stream",
            b"\x00\x01\x02",
            False,
            "error[unsupported_response_type]:",
        ),
    ],
)
def test_response_rendering_is_safe_and_content_type_aware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
    payload: bytes,
    expected_ok: bool,
    expected_fragment: str,
) -> None:
    class Response(_SmallResponse):
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
            "Set-Cookie": "session=fixture-secret",
            "Authorization": "fixture-secret",
            "X-Fixture-Secret": "fixture-secret",
        }

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 64 * 1024
            return payload

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        lambda _request, *, timeout: Response(),
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {"url": "https://public.example/safe", "method": "GET"}
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is expected_ok
    assert expected_fragment in result.output
    assert "Set-Cookie" not in result.output
    assert "Authorization" not in result.output
    assert "X-Fixture-Secret" not in result.output
    assert "fixture-secret" not in result.output
    assert len(result.output) <= 15_000


def test_timeout_is_one_monotonic_budget_across_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[float] = []

    def redirect_then_ok(
        _request: object, *, timeout: float
    ) -> _SmallResponse:
        observed_timeouts.append(timeout)
        if len(observed_timeouts) == 1:
            time.sleep(0.03)
            return _RedirectResponse()
        return _SmallResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        redirect_then_ok,
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://public.example/start",
                "method": "GET",
                "timeout": 0.2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is True
    assert len(observed_timeouts) == 2
    assert 0 < observed_timeouts[1] < observed_timeouts[0]


def test_declared_response_length_over_budget_rejects_without_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeclaredLargeResponse(_SmallResponse):
        headers = {
            "Content-Type": "text/plain",
            "Content-Length": str(1024 * 1024 + 1),
        }

        def read(self, _size: int = -1) -> bytes:
            raise AssertionError("oversized declared body must not be read")

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        lambda _request, *, timeout: DeclaredLargeResponse(),
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {"url": "https://public.example/declared-large", "method": "GET"}
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == (
        "error[response_too_large]: The response exceeds the safe byte limit."
    )


def test_rendered_response_never_exceeds_tool_output_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x" * 20_000

    class LongTextResponse(_SmallResponse):
        headers = {
            "Content-Type": "text/plain",
            "Content-Length": str(len(payload)),
        }

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 64 * 1024
            return payload

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        lambda _request, *, timeout: LongTextResponse(),
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {"url": "https://public.example/long-text", "method": "GET"}
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is True
    assert len(result.output) == 15_000


def test_validated_destination_is_bound_to_the_transport_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_destinations: list[network_safety.ValidatedDestination] = []

    def inspect_pinned_request(
        request: object, *, timeout: float
    ) -> _SmallResponse:
        assert timeout > 0
        destination = getattr(request, "_minicode_destination")
        observed_destinations.append(destination)
        return _SmallResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        inspect_pinned_request,
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {"url": "https://public.example/pinned", "method": "GET"}
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is True
    assert len(observed_destinations) == 1
    assert observed_destinations[0].hostname == "public.example"
    assert observed_destinations[0].addresses == ("93.184.216.34",)


def test_dns_resolution_is_bounded_by_the_total_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls: list[object] = []
    resolver_started = threading.Event()
    release_resolver = threading.Event()

    def slow_resolver(_host: str, port: int, **_kwargs: object):
        resolver_started.set()
        if not release_resolver.wait(timeout=5):
            raise AssertionError("request waited for the resolver past its deadline")
        return [
            (
                network_safety.socket.AF_INET,
                network_safety.socket.SOCK_STREAM,
                network_safety.socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    def forbidden_open(request: object, *, timeout: float) -> _SmallResponse:
        network_calls.append((request, timeout))
        return _SmallResponse()

    monkeypatch.setattr(network_safety.socket, "getaddrinfo", slow_resolver)
    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        forbidden_open,
    )
    started = time.monotonic()
    try:
        result = http_request_tool.run(
            http_request_tool.validator(
                {
                    "url": "https://slow.public.example/deadline",
                    "method": "GET",
                    "timeout": 0.1,
                }
            ),
            ToolContext(cwd=str(tmp_path), permissions=None),
        )
        elapsed = time.monotonic() - started
    finally:
        release_resolver.set()

    assert resolver_started.wait(timeout=1)
    assert result.ok is False
    assert result.output == "error[timeout]: The network request timed out."
    # A loaded hosted runner may overshoot a 100 ms wait, so compare against
    # the resolver's five-second blocking window instead of a 100 ms jitter
    # allowance. Waiting on DNS would exceed this bound by several seconds.
    assert elapsed < 1.0
    assert network_calls == []


def test_each_response_read_uses_only_the_remaining_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[float] = []

    class Clock:
        now = 1_000.0

        def monotonic(self) -> float:
            return self.now

        def advance(self, seconds: float) -> None:
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(network_safety, "time", clock)

    class TimedResponse:
        headers = {"Content-Type": "text/plain"}

        def __init__(self) -> None:
            self.reads = 0

        def set_read_timeout(self, timeout: float) -> None:
            timeouts.append(timeout)

        def read(self, size: int) -> bytes:
            assert 0 < size <= 64 * 1024
            self.reads += 1
            clock.advance(0.0625)
            return b"bounded" if self.reads == 1 else b""

    payload = network_safety.read_bounded_response(
        TimedResponse(),
        method="GET",
        deadline=clock.monotonic() + 0.25,
    )

    assert payload == b"bounded"
    assert timeouts == [0.25, 0.1875]


def test_pinned_https_transport_connects_to_validated_ip_and_preserves_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[dict[str, object]] = []

    class Response(_SmallResponse):
        def close(self) -> None:
            return None

    class FakePinnedHttpsConnection:
        def __init__(
            self,
            hostname: str,
            port: int,
            *,
            address: str,
            timeout: float,
        ) -> None:
            self.record: dict[str, object] = {
                "hostname": hostname,
                "port": port,
                "address": address,
                "timeout": timeout,
            }
            connections.append(self.record)

        def request(
            self,
            method: str,
            target: str,
            *,
            body: bytes,
            headers: dict[str, str],
        ) -> None:
            self.record.update(
                {
                    "method": method,
                    "target": target,
                    "body": body,
                    "headers": headers,
                }
            )

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            self.record["closed"] = True

    monkeypatch.setattr(
        http_utils,
        "_PinnedHTTPSConnection",
        FakePinnedHttpsConnection,
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://public.example:8443/pinned?opaque=value",
                "method": "GET",
                "timeout": 2,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is True
    assert len(connections) == 1
    assert connections[0]["hostname"] == "public.example"
    assert connections[0]["port"] == 8443
    assert connections[0]["address"] == "93.184.216.34"
    assert connections[0]["target"] == "/pinned?opaque=value"
    assert connections[0]["closed"] is True


@pytest.mark.parametrize(
    ("failure", "expected_output"),
    [
        (
            TimeoutError("fixture-secret"),
            "error[timeout]: The network request timed out.",
        ),
        (
            http_utils.ssl.SSLError("fixture-secret"),
            "error[tls_error]: The secure connection could not be established.",
        ),
        (
            ConnectionRefusedError("fixture-secret"),
            "error[network_unavailable]: The network destination is unavailable.",
        ),
        (
            RuntimeError("fixture-secret"),
            "error[request_failed]: The network request failed.",
        ),
    ],
)
def test_transport_failures_use_fixed_redacted_error_vocabulary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_output: str,
) -> None:
    def fail_open(_request: object, *, timeout: float) -> _SmallResponse:
        assert timeout > 0
        raise failure

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        fail_open,
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://public.example/failure?fixture-secret=hidden",
                "method": "GET",
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == expected_output
    assert "fixture-secret" not in result.output


def test_response_read_cannot_overrun_total_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowResponse(_SmallResponse):
        headers = {"Content-Type": "text/plain", "Content-Length": "4"}

        def read(self, size: int = -1) -> bytes:
            assert 0 < size <= 64 * 1024
            time.sleep(0.11)
            return b"late"

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        lambda _request, *, timeout: SlowResponse(),
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": "https://public.example/slow-response",
                "method": "GET",
                "timeout": 0.1,
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.ok is False
    assert result.output == "error[timeout]: The network request timed out."


def test_transport_failure_neither_logs_nor_echoes_raw_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_marker = "transport-log-fixture-secret"

    def fail_open(_request: object, *, timeout: float) -> _SmallResponse:
        assert timeout > 0
        raise RuntimeError(sensitive_marker)

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        fail_open,
    )
    result = http_request_tool.run(
        http_request_tool.validator(
            {
                "url": (
                    "https://public.example/failure"
                    f"?credential={sensitive_marker}"
                ),
                "method": "GET",
            }
        ),
        ToolContext(cwd=str(tmp_path), permissions=None),
    )

    assert result.output == "error[request_failed]: The network request failed."
    assert sensitive_marker not in result.output
    assert sensitive_marker not in caplog.text
    assert str(tmp_path) not in result.output
    assert str(tmp_path) not in caplog.text


def test_resolver_saturation_fails_before_http_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    network_calls: list[object] = []

    def blocking_resolver(
        _hostname: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[object, ...]]:
        assert release.wait(2)
        return [
            (
                network_safety.socket.AF_INET,
                network_safety.socket.SOCK_STREAM,
                network_safety.socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    resolver = BoundedResolver(
        worker_limit=1,
        queue_limit=1,
        resolver=blocking_resolver,
    )
    monkeypatch.setattr(network_safety, "_DNS_RESOLVER", resolver)

    def forbidden_open(request: object, *, timeout: float) -> _SmallResponse:
        network_calls.append((request, timeout))
        return _SmallResponse()

    monkeypatch.setattr(
        "minicode.tools.http_utils._open_no_redirect",
        forbidden_open,
    )

    def occupy(url: str) -> None:
        try:
            network_safety.validate_destination(
                url,
                deadline=time.monotonic() + 2,
            )
        except network_safety.NetworkSafetyError:
            return

    active = threading.Thread(
        target=occupy,
        args=("https://active.public.example/",),
    )
    queued = threading.Thread(
        target=occupy,
        args=("https://queued.public.example/",),
    )
    try:
        active.start()
        deadline = time.monotonic() + 1
        while (
            resolver.snapshot().active_count != 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        queued.start()
        deadline = time.monotonic() + 1
        while (
            resolver.snapshot().queued_count != 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)

        result = http_request_tool.run(
            http_request_tool.validator(
                {
                    "url": "https://saturated-secret.public.example/read",
                    "method": "GET",
                    "timeout": 1,
                }
            ),
            ToolContext(cwd=str(tmp_path), permissions=None),
        )

        assert result.ok is False
        assert result.output == (
            "error[resolver_busy]: The DNS resolver is temporarily busy."
        )
        assert "saturated-secret" not in result.output
        assert network_calls == []
    finally:
        resolver.close()
        release.set()
        active.join(timeout=2)
        queued.join(timeout=2)
