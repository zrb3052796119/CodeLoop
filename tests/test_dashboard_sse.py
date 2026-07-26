from __future__ import annotations

import http.client
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.gateway as gateway_module
from minicode.gateway import MiniCodeGatewayHandler
from minicode.web.event_stream import DashboardEventStream


RESOURCE_NAMES = (
    "runs",
    "sessions",
    "turns",
    "memory",
    "skills",
    "connections",
    "permissions",
)


def _snapshot(suffix: str = "a") -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "generatedAt": "2026-07-19T00:00:00.000Z",
        "mode": "read-only",
        "pollAfterMs": 2_000,
        "resources": {
            name: {"status": "live", "revision": f"rev_{suffix * 64}"}
            for name in RESOURCE_NAMES
        },
        "diagnostics": [],
    }


class _StaticFeed:
    def __init__(self) -> None:
        self.sampled = threading.Event()

    def snapshot(self) -> dict[str, object]:
        self.sampled.set()
        return _snapshot()


class _MutableFeed:
    def __init__(self) -> None:
        self.current = _snapshot()
        self.calls = 0
        self.sampled = threading.Condition()

    def snapshot(self) -> dict[str, object]:
        with self.sampled:
            self.calls += 1
            value = self.current
            self.sampled.notify_all()
            return value

    def wait_for_calls(self, count: int) -> None:
        with self.sampled:
            assert self.sampled.wait_for(lambda: self.calls >= count, timeout=2)


class _ManualSamplerWait:
    def __init__(self) -> None:
        self.waiting = threading.Event()
        self.advance_event = threading.Event()

    def __call__(self, stop: threading.Event, _seconds: float) -> bool:
        self.waiting.set()
        while not stop.is_set():
            if self.advance_event.wait(0.05):
                self.advance_event.clear()
                self.waiting.clear()
                return False
        return True

    def advance(self) -> None:
        assert self.waiting.wait(timeout=2)
        self.advance_event.set()

    def release(self) -> None:
        self.advance_event.set()


@contextmanager
def _sse_server(
    *,
    stream: DashboardEventStream | None,
) -> Iterator[tuple[int, DashboardEventStream | None]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    if stream is not None:
        server.dashboard_event_stream = stream
        stream.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], stream
    finally:
        if stream is not None:
            stream.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _read_frame(response: http.client.HTTPResponse) -> bytes:
    lines = []
    while True:
        line = response.fp.readline()
        assert line
        lines.append(line)
        if line in {b"\n", b"\r\n"}:
            return b"".join(lines)


def _read_business_frame(response: http.client.HTTPResponse) -> bytes:
    while True:
        frame = _read_frame(response)
        if not frame.startswith(b":"):
            return frame


def _decode_frame(frame: bytes) -> tuple[str, str, dict[str, object]]:
    values = {}
    for line in frame.decode("utf-8").splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            values[key] = value
    return values["id"], values["event"], json.loads(values["data"])


def _json_get(
    port: int,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        response_headers = dict(response.getheaders())
        if response.headers.get_content_type() != "application/json":
            return response.status, response_headers, {}
        return (
            response.status,
            response_headers,
            json.loads(response.read()),
        )
    finally:
        connection.close()


def test_events_endpoint_streams_ready_with_strict_sse_headers() -> None:
    feed = _StaticFeed()
    stream = DashboardEventStream(feed, heartbeat_seconds=0.1)
    with _sse_server(stream=stream) as (port, _):
        assert feed.sampled.wait(timeout=2)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(
                "GET",
                "/api/v1/events",
                headers={"Accept": "text/event-stream"},
            )
            response = connection.getresponse()
            headers = dict(response.getheaders())
            assert response.status == 200
            assert headers["Content-Type"] == "text/event-stream; charset=utf-8"
            assert headers["Cache-Control"] == "no-store"
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert headers["Connection"] == "keep-alive"
            assert headers["X-Accel-Buffering"] == "no"
            assert "Content-Length" not in headers
            event_id, event_type, payload = _decode_frame(_read_frame(response))
            assert event_id.startswith("evt_")
            assert event_type == "stream.ready"
            assert payload["schemaVersion"] == 2
            assert payload["type"] == "stream.ready"
            assert payload["retryMs"] == 2_000
        finally:
            connection.close()


def test_gateway_composes_one_broker_feed_stream_and_closes_approval_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []
    feeds: list[object] = []
    streams: list[object] = []
    servers: list[object] = []
    read_model = SimpleNamespace(
        workspace=Path("/workspace"),
        data_dir=Path("/data"),
        configured_mcp_server_keys=lambda: frozenset(),
    )

    class FakeRegistry:
        def snapshot_for(self, _server_keys):
            return SimpleNamespace(to_dict=lambda: {"servers": []})

    class FakeFeed:
        def __init__(self, *_args, **kwargs) -> None:
            self.kwargs = kwargs
            feeds.append(self)

    class FakeBroker:
        def __init__(self, workspace) -> None:
            self.workspace = workspace

        def revision(self) -> str:
            return "permissionrev_" + "a" * 32

        def close(self) -> None:
            lifecycle.append("broker.close")

    class FakeStream:
        def __init__(self, feed) -> None:
            self.feed = feed
            streams.append(self)

        def start(self) -> None:
            lifecycle.append("stream.start")

        def close(self) -> None:
            lifecycle.append("stream.close")

    class FakeServer:
        def __init__(self, _address, _handler) -> None:
            servers.append(self)

        def serve_forever(self) -> None:
            lifecycle.append("server.serve")

        def server_close(self) -> None:
            lifecycle.append("server.close")

    monkeypatch.setattr(
        "minicode.mcp_current_state.McpCurrentStateRegistry", FakeRegistry
    )
    monkeypatch.setattr(
        gateway_module.DashboardReadModel,
        "from_environment",
        lambda **_kwargs: read_model,
    )
    monkeypatch.setattr(gateway_module, "DashboardChangeFeed", FakeFeed)
    monkeypatch.setattr(gateway_module, "DashboardEventStream", FakeStream, raising=False)
    monkeypatch.setattr(gateway_module, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(
        "minicode.permission_approval.PermissionApprovalBroker", FakeBroker
    )

    gateway_module.run_gateway()

    assert len(feeds) == 1
    assert len(streams) == 1
    assert len(servers) == 1
    assert streams[0].feed is feeds[0]
    assert servers[0].dashboard_change_feed is feeds[0]
    assert servers[0].dashboard_event_stream is streams[0]
    assert servers[0].permission_approval_broker is not None
    loader = feeds[0].kwargs["permission_revision_loader"]
    assert loader.__self__ is servers[0].permission_approval_broker
    assert (
        servers[0].conversation_turn_service._approval_broker
        is servers[0].permission_approval_broker
    )
    assert lifecycle == [
        "stream.start",
        "server.serve",
        "broker.close",
        "stream.close",
        "server.close",
    ]

    lifecycle.clear()
    feeds.clear()
    streams.clear()
    servers.clear()
    monkeypatch.setenv("MINI_CODE_GATEWAY_HOST", "0.0.0.0")
    gateway_module.run_gateway()

    assert len(feeds) == len(streams) == len(servers) == 1
    assert servers[0].permission_approval_broker is None
    assert feeds[0].kwargs["permission_revision_loader"] is None
    assert servers[0].conversation_turn_service._approval_broker is None
    assert lifecycle == [
        "stream.start",
        "server.serve",
        "stream.close",
        "server.close",
    ]


@pytest.mark.parametrize(
    ("path", "headers", "status", "code"),
    [
        ("/api/v1/events?cursor=hidden", {}, 400, "invalid_query"),
        (
            "/api/v1/events",
            {"Accept": "application/json"},
            406,
            "not_acceptable",
        ),
        (
            "/api/v1/events",
            {"Accept": "text/event-stream;q=0"},
            406,
            "not_acceptable",
        ),
        (
            "/api/v1/events",
            {"Accept": "text/event-stream;q=2"},
            406,
            "not_acceptable",
        ),
        (
            "/api/v1/events",
            {"Accept": "text/event-stream;q=invalid"},
            406,
            "not_acceptable",
        ),
        (
            "/api/v1/events",
            {"Last-Event-ID": "Bearer secret /Users/private"},
            400,
            "invalid_event_cursor",
        ),
        (
            "/api/v1/events",
            {"Last-Event-ID": "x" * 65},
            400,
            "invalid_event_cursor",
        ),
    ],
)
def test_events_endpoint_rejects_query_accept_and_invalid_cursor_before_headers(
    path: str,
    headers: dict[str, str],
    status: int,
    code: str,
) -> None:
    stream = DashboardEventStream(_StaticFeed(), heartbeat_seconds=0.1)
    with _sse_server(stream=stream) as (port, _):
        actual_status, response_headers, payload = _json_get(
            port, path, headers=headers
        )

    assert actual_status == status
    assert response_headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload["ok"] is False
    assert payload["error"]["code"] == code
    rendered = json.dumps(payload)
    assert "hidden" not in rendered
    assert "secret" not in rendered
    assert "/Users/" not in rendered


def test_duplicate_last_event_id_is_rejected_without_echo() -> None:
    stream = DashboardEventStream(_StaticFeed(), heartbeat_seconds=0.1)
    with _sse_server(stream=stream) as (port, _):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.putrequest("GET", "/api/v1/events")
            connection.putheader("Accept", "text/event-stream")
            connection.putheader("Last-Event-ID", f"evt_{'a' * 32}_{1:016x}")
            connection.putheader("Last-Event-ID", f"evt_{'b' * 32}_{2:016x}")
            connection.endheaders()
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 400
        finally:
            connection.close()

    assert payload == {
        "ok": False,
        "error": {
            "code": "invalid_event_cursor",
            "message": "Last-Event-ID is invalid.",
        },
    }


def test_events_endpoint_without_composed_stream_is_fixed_unavailable() -> None:
    with _sse_server(stream=None) as (port, _):
        status, headers, payload = _json_get(
            port,
            "/api/v1/events",
            headers={"Accept": "text/event-stream"},
        )

    assert status == 503
    assert headers["Cache-Control"] == "no-store"
    assert payload == {
        "ok": False,
        "error": {
            "code": "events_unavailable",
            "message": "Dashboard events are unavailable.",
        },
    }


def test_events_endpoint_enforces_subscriber_budget_before_sse_headers() -> None:
    stream = DashboardEventStream(
        _StaticFeed(), max_subscribers=1, heartbeat_seconds=0.1
    )
    with _sse_server(stream=stream) as (port, _):
        first = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            first.request(
                "GET",
                "/api/v1/events",
                headers={"Accept": "text/event-stream"},
            )
            first_response = first.getresponse()
            assert first_response.status == 200
            assert _decode_frame(_read_frame(first_response))[1] == "stream.ready"

            status, headers, payload = _json_get(
                port,
                "/api/v1/events",
                headers={"Accept": "text/event-stream"},
            )
            assert status == 503
            assert headers["Content-Type"] == "application/json; charset=utf-8"
            assert payload["error"]["code"] == "stream_busy"
        finally:
            first.close()


def test_http_changed_replay_restart_and_two_client_delivery_share_event_ids() -> None:
    feed = _MutableFeed()
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(
        feed,
        sampler_wait=sampler_wait,
        heartbeat_seconds=0.1,
    )
    with _sse_server(stream=stream) as (port, _):
        feed.wait_for_calls(1)
        first = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        peer = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            for connection in (first, peer):
                connection.request(
                    "GET",
                    "/api/v1/events",
                    headers={"Accept": "text/event-stream"},
                )
            first_response = first.getresponse()
            peer_response = peer.getresponse()
            first_ready = _decode_frame(_read_business_frame(first_response))
            peer_ready = _decode_frame(_read_business_frame(peer_response))
            assert first_ready[0] == peer_ready[0]

            changed_resources = dict(_snapshot()["resources"])
            changed_resources["runs"] = {
                "status": "live",
                "revision": f"rev_{'b' * 64}",
            }
            feed.current = {**_snapshot(), "resources": changed_resources}
            sampler_wait.advance()
            feed.wait_for_calls(2)
            first_changed = _read_business_frame(first_response)
            peer_changed = _read_business_frame(peer_response)
            changed_id, changed_type, changed_payload = _decode_frame(first_changed)
            assert peer_changed == first_changed
            assert changed_type == "resources.changed"
            assert [item["name"] for item in changed_payload["resources"]] == [
                "runs"
            ]
            assert feed.calls == 2
        finally:
            first.close()
            peer.close()

        second_resources = dict(feed.current["resources"])
        second_resources["memory"] = {
            "status": "partial",
            "revision": f"rev_{'c' * 64}",
        }
        feed.current = {**_snapshot(), "resources": second_resources}
        sampler_wait.advance()
        feed.wait_for_calls(3)

        replay = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            replay.request(
                "GET",
                "/api/v1/events",
                headers={
                    "Accept": "text/event-stream",
                    "Last-Event-ID": changed_id,
                },
            )
            replay_response = replay.getresponse()
            assert replay_response.status == 200
            replay_id, replay_type, replay_payload = _decode_frame(
                _read_business_frame(replay_response)
            )
            assert replay_type == "resources.changed"
            assert replay_id != changed_id
            assert [item["name"] for item in replay_payload["resources"]] == [
                "memory"
            ]
        finally:
            replay.close()

        restarted = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            restarted.request(
                "GET",
                "/api/v1/events",
                headers={
                    "Accept": "text/event-stream",
                    "Last-Event-ID": f"evt_{'f' * 32}_{1:016x}",
                },
            )
            restarted_response = restarted.getresponse()
            _, reset_type, reset_payload = _decode_frame(
                _read_business_frame(restarted_response)
            )
            assert reset_type == "stream.reset"
            assert reset_payload["reason"] == "stream_restarted"
        finally:
            restarted.close()

    sampler_wait.release()


def test_http_heartbeat_is_a_comment_and_expired_cursor_gets_reset() -> None:
    feed = _MutableFeed()
    sampler_wait = _ManualSamplerWait()
    stream = DashboardEventStream(
        feed,
        sampler_wait=sampler_wait,
        heartbeat_seconds=0.1,
        ring_size=1,
    )
    with _sse_server(stream=stream) as (port, _):
        feed.wait_for_calls(1)
        initial = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            initial.request("GET", "/api/v1/events")
            response = initial.getresponse()
            ready_id, _, _ = _decode_frame(_read_business_frame(response))
            assert _read_frame(response) == b": heartbeat\n\n"
        finally:
            initial.close()

        for call, suffix in ((2, "b"), (3, "c")):
            resources = dict(_snapshot()["resources"])
            resources["turns"] = {
                "status": "live",
                "revision": f"rev_{suffix * 64}",
            }
            feed.current = {**_snapshot(), "resources": resources}
            sampler_wait.advance()
            feed.wait_for_calls(call)

        expired = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            expired.request(
                "GET",
                "/api/v1/events",
                headers={"Last-Event-ID": ready_id},
            )
            expired_response = expired.getresponse()
            _, kind, payload = _decode_frame(_read_business_frame(expired_response))
            assert kind == "stream.reset"
            assert payload["reason"] == "replay_unavailable"
        finally:
            expired.close()

    sampler_wait.release()


def test_post_header_write_timeout_closes_only_that_subscription() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.timeout = None

        def settimeout(self, value: float) -> None:
            self.timeout = value

    class TimeoutWriter:
        def write(self, _frame: bytes) -> None:
            raise socket.timeout("client stopped reading")

        def flush(self) -> None:
            raise AssertionError("flush must not follow a failed write")

    class FakeSubscription:
        def __init__(self) -> None:
            self.closed = 0

        def next_batch(self) -> tuple[bytes, ...]:
            return (b": heartbeat\n\n",)

        def close(self) -> None:
            self.closed += 1

    handler = object.__new__(MiniCodeGatewayHandler)
    handler.connection = FakeConnection()
    handler.wfile = TimeoutWriter()
    handler.close_connection = False
    subscription = FakeSubscription()

    handler._write_event_subscription(subscription, write_timeout=0.25)

    assert handler.connection.timeout == 0.25
    assert handler.close_connection is True
    assert subscription.closed == 1


def test_gateway_restart_rejects_prior_process_cursor_with_restart_reset() -> None:
    first_stream = DashboardEventStream(_StaticFeed(), heartbeat_seconds=0.1)
    with _sse_server(stream=first_stream) as (first_port, _):
        first = http.client.HTTPConnection("127.0.0.1", first_port, timeout=2)
        try:
            first.request("GET", "/api/v1/events")
            first_response = first.getresponse()
            old_event_id, _, old_payload = _decode_frame(
                _read_business_frame(first_response)
            )
        finally:
            first.close()

    second_stream = DashboardEventStream(_StaticFeed(), heartbeat_seconds=0.1)
    with _sse_server(stream=second_stream) as (second_port, _):
        second = http.client.HTTPConnection("127.0.0.1", second_port, timeout=2)
        try:
            second.request(
                "GET",
                "/api/v1/events",
                headers={"Last-Event-ID": old_event_id},
            )
            second_response = second.getresponse()
            reset_id, reset_type, reset_payload = _decode_frame(
                _read_business_frame(second_response)
            )
        finally:
            second.close()

    assert reset_type == "stream.reset"
    assert reset_payload["reason"] == "stream_restarted"
    assert reset_id.split("_")[1] != old_event_id.split("_")[1]
    assert reset_payload["resources"] == list(RESOURCE_NAMES)
    assert old_payload["streamId"] != f"stream_{reset_id.split('_')[1]}"
