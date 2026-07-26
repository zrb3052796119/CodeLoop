from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from minicode.conversation import ConversationSessionConflict
from minicode.gateway import MiniCodeGatewayHandler
from minicode.web.chat_stream import CHAT_STREAM_MAX_FRAME_BYTES, ChatStreamWriter


TURN_ID = "turn_" + "7" * 32


class BlockingStreamingService:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.presentation = None

    def turn(self, **kwargs):
        self.presentation = kwargs.get("presentation")
        self.entered.set()
        assert self.release.wait(5)
        return SimpleNamespace(
            turn_id=TURN_ID,
            session_id="session_stream",
            created=True,
            assistant="committed assistant",
            updated_at="2026-07-20T12:00:00.000Z",
            run_id="run_" + "8" * 32,
        )


class PresentingService:
    def __init__(self, outcome: BaseException | None = None) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    def turn(self, **kwargs):
        self.calls.append(kwargs)
        presentation = kwargs.get("presentation")
        if presentation is not None:
            presentation.assistant_delta("first ")
            presentation.tool_started("read_file")
            presentation.tool_finished("read_file", is_error=False)
            presentation.assistant_delta("last")
        if self.outcome is not None:
            raise self.outcome
        return SimpleNamespace(
            turn_id=TURN_ID,
            session_id="session_stream",
            created=True,
            assistant="first last",
            updated_at="2026-07-20T12:00:00.000Z",
            run_id="run_" + "8" * 32,
        )


@pytest.fixture
def streaming_server():
    service = PresentingService()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.conversation_turn_service = service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request_chat(
    port: int,
    *,
    accept: str,
    body: dict[str, object] | bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request_body = (
        json.dumps(
            body
            or {"message": "stream", "sessionId": None, "turnId": TURN_ID}
        ).encode()
        if not isinstance(body, bytes)
        else body
    )
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/v1/chat/turns",
            body=request_body,
            headers={"Accept": accept, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_ndjson_ready_is_flushed_before_the_synchronous_turn_finishes() -> None:
    service = BlockingStreamingService()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.conversation_turn_service = service
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    observed: dict[str, object] = {}
    response_started = threading.Event()
    ready_received = threading.Event()

    def request_stream() -> None:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=5
        )
        try:
            connection.request(
                "POST",
                "/api/v1/chat/turns",
                body=json.dumps(
                    {"message": "stream", "sessionId": None, "turnId": TURN_ID}
                ).encode(),
                headers={
                    "Accept": "application/x-ndjson",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            observed["status"] = response.status
            observed["headers"] = dict(response.getheaders())
            response_started.set()
            observed["ready"] = json.loads(response.readline())
            ready_received.set()
            if observed["headers"].get("Content-Type") == (
                "application/x-ndjson; charset=utf-8"
            ):
                observed["terminal"] = json.loads(response.readline())
        finally:
            connection.close()

    client_thread = threading.Thread(target=request_stream, daemon=True)
    client_thread.start()
    try:
        assert service.entered.wait(2)
        assert response_started.wait(0.5), (
            "the current JSON-only Chat POST does not expose headers or a ready "
            "frame until the synchronous Turn has finished"
        )
        assert ready_received.wait(0.5)
        assert observed["status"] == 200
        assert observed["headers"]["Content-Type"] == (
            "application/x-ndjson; charset=utf-8"
        )
        assert observed["ready"] == {
            "schemaVersion": 1,
            "type": "chat.stream.ready",
            "turnId": TURN_ID,
            "sequence": 0,
        }
    finally:
        service.release.set()
        client_thread.join(timeout=5)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def test_ndjson_http_contract_orders_only_allowlisted_frames(
    streaming_server,
) -> None:
    port, service = streaming_server

    status, headers, body = request_chat(
        port,
        accept="application/x-ndjson",
    )

    assert status == 200
    assert headers["Content-Type"] == "application/x-ndjson; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Accel-Buffering"] == "no"
    assert "Content-Length" not in headers
    frames = [json.loads(line) for line in body.splitlines()]
    assert [frame["type"] for frame in frames] == [
        "chat.stream.ready",
        "chat.assistant.delta",
        "chat.tool.started",
        "chat.tool.finished",
        "chat.assistant.delta",
        "chat.turn.completed",
    ]
    assert [frame["sequence"] for frame in frames] == list(range(6))
    assert frames[-1] == {
        "schemaVersion": 1,
        "type": "chat.turn.completed",
        "turnId": TURN_ID,
        "sequence": 5,
        "status": "completed",
        "sessionId": "session_stream",
        "created": True,
        "updatedAt": "2026-07-20T12:00:00.000Z",
        "runId": "run_" + "8" * 32,
    }
    assert service.calls == [
        {
            "message": "stream",
            "session_id": None,
            "turn_id": TURN_ID,
            "presentation": service.calls[0]["presentation"],
        }
    ]
    serialized = body.decode()
    for forbidden in (
        "tool_input",
        "tool_output",
        "thinking",
        "provider",
        "secret",
        "/private/",
    ):
        assert forbidden not in serialized.lower()


def test_ndjson_post_header_failure_uses_fixed_safe_terminal_frame(
    streaming_server,
) -> None:
    port, service = streaming_server
    service.outcome = ConversationSessionConflict()

    status, _, body = request_chat(port, accept="application/x-ndjson")

    frames = [json.loads(line) for line in body.splitlines()]
    assert status == 200
    assert frames[-1] == {
        "schemaVersion": 1,
        "type": "chat.turn.error",
        "turnId": TURN_ID,
        "sequence": 5,
        "code": "session_conflict",
    }
    assert "provider secret" not in body.decode()
    assert "/private/workspace" not in body.decode()


@pytest.mark.parametrize(
    ("accept", "body"),
    [
        ("application/x-ndjson", b'{"message":"stream"}'),
        ("application/x-ndjson", b"{"),
        ("application/x-ndjson;q=0", None),
    ],
)
def test_invalid_or_unaccepted_stream_requests_keep_json_contract(
    streaming_server,
    accept: str,
    body: bytes | None,
) -> None:
    port, service = streaming_server

    status, headers, response_body = request_chat(
        port,
        accept=accept,
        body=body,
    )
    payload = json.loads(response_body)

    assert headers["Content-Type"] == "application/json; charset=utf-8"
    if accept.endswith("q=0"):
        assert status == 200
        assert payload["ok"] is True
        assert "presentation" not in service.calls[-1]
    else:
        assert status == 400
        assert payload["error"]["code"] == "invalid_request"
        assert service.calls == []


def test_assistant_delta_is_unicode_safe_ordered_and_frame_bounded() -> None:
    lines: list[bytes] = []
    writer = ChatStreamWriter(TURN_ID, lines.append)
    text = ("汉字🙂\\\"\n" * 900) + "tail"

    writer.ready()
    writer.assistant_delta(text)

    frames = [json.loads(line) for line in lines]
    assert [frame["sequence"] for frame in frames] == list(range(len(frames)))
    assert frames[0]["type"] == "chat.stream.ready"
    deltas = [frame for frame in frames if frame["type"] == "chat.assistant.delta"]
    assert "".join(frame["text"] for frame in deltas) == text
    assert all(frame["text"] for frame in deltas)
    assert all(len(line) <= CHAT_STREAM_MAX_FRAME_BYTES for line in lines)


def test_assistant_budget_truncates_once_without_blocking_terminal() -> None:
    lines: list[bytes] = []
    writer = ChatStreamWriter(
        TURN_ID,
        lines.append,
        assistant_budget_bytes=12,
    )

    writer.ready()
    writer.assistant_delta("1234567890")
    writer.assistant_delta("abcdef")
    writer.assistant_delta("ignored")
    writer.completed(
        SimpleNamespace(
            session_id="session_stream",
            created=True,
            updated_at="2026-07-20T12:00:00.000Z",
            run_id=None,
        )
    )

    frames = [json.loads(line) for line in lines]
    assert "".join(
        frame["text"]
        for frame in frames
        if frame["type"] == "chat.assistant.delta"
    ) == "1234567890ab"
    assert [
        frame for frame in frames if frame["type"] == "chat.stream.truncated"
    ] == [
        {
            "schemaVersion": 1,
            "type": "chat.stream.truncated",
            "turnId": TURN_ID,
            "sequence": 3,
            "category": "assistant",
        }
    ]
    assert frames[-1]["type"] == "chat.turn.completed"


def test_tool_frames_use_safe_names_and_fifo_pairing_without_fabrication() -> None:
    lines: list[bytes] = []
    writer = ChatStreamWriter(TURN_ID, lines.append)

    writer.tool_started("read_file")
    writer.tool_started("read_file")
    writer.tool_finished("read_file", is_error=False)
    writer.tool_finished("read_file", is_error=True)
    writer.tool_finished("write_file", is_error=False)
    writer.tool_started("../password=secret")

    frames = [json.loads(line) for line in lines]
    first_id = frames[0]["toolStreamId"]
    second_id = frames[1]["toolStreamId"]
    assert first_id != second_id
    assert frames[2] == {
        "schemaVersion": 1,
        "type": "chat.tool.finished",
        "turnId": TURN_ID,
        "sequence": 2,
        "toolName": "read_file",
        "outcome": "success",
        "paired": True,
        "toolStreamId": first_id,
    }
    assert frames[3]["toolStreamId"] == second_id
    assert frames[3]["outcome"] == "error"
    assert frames[4]["paired"] is False
    assert "toolStreamId" not in frames[4]
    assert frames[5]["toolName"] == "unknown"
    assert all(
        frame.get("toolName") not in {"../password=secret", "password=secret"}
        for frame in frames
    )


def test_concurrent_tool_callbacks_never_interleave_ndjson_lines() -> None:
    output = bytearray()

    def deliberately_split_write(frame: bytes) -> None:
        middle = len(frame) // 2
        output.extend(frame[:middle])
        time.sleep(0.001)
        output.extend(frame[middle:])

    writer = ChatStreamWriter(TURN_ID, deliberately_split_write)
    barrier = threading.Barrier(9)

    def tool_worker(index: int) -> None:
        barrier.wait()
        writer.tool_started("parallel.read")
        time.sleep((index % 3) * 0.001)
        writer.tool_finished("parallel.read", is_error=index % 2 == 1)

    workers = [threading.Thread(target=tool_worker, args=(index,)) for index in range(8)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()

    frames = [json.loads(line) for line in bytes(output).splitlines()]
    assert len(frames) == 16
    assert [frame["sequence"] for frame in frames] == list(range(16))
    started = [
        frame["toolStreamId"]
        for frame in frames
        if frame["type"] == "chat.tool.started"
    ]
    finished = [
        frame["toolStreamId"]
        for frame in frames
        if frame["type"] == "chat.tool.finished"
    ]
    assert finished == started


def test_writer_failure_detaches_once_and_all_later_events_are_noops() -> None:
    attempts: list[bytes] = []

    def broken_connection(frame: bytes) -> None:
        attempts.append(frame)
        raise ConnectionResetError("private provider path must never escape")

    writer = ChatStreamWriter(TURN_ID, broken_connection)

    writer.ready()
    writer.assistant_delta("ignored")
    writer.tool_started("read_file")
    writer.tool_finished("read_file", is_error=False)
    writer.error("turn_failed")

    assert writer.detached is True
    assert len(attempts) == 1


def test_tool_budget_truncates_once_and_terminal_still_wins() -> None:
    lines: list[bytes] = []
    writer = ChatStreamWriter(TURN_ID, lines.append, tool_event_budget=2)

    writer.tool_started("read_file")
    writer.tool_finished("read_file", is_error=False)
    writer.tool_started("list_files")
    writer.tool_finished("list_files", is_error=False)
    writer.completed(
        SimpleNamespace(
            session_id="session_stream",
            created=False,
            updated_at="2026-07-20T12:00:00.000Z",
            run_id="run_" + "8" * 32,
        )
    )

    frames = [json.loads(line) for line in lines]
    assert [frame["type"] for frame in frames] == [
        "chat.tool.started",
        "chat.tool.finished",
        "chat.stream.truncated",
        "chat.turn.completed",
    ]
    assert frames[2]["category"] == "tools"
    assert frames[-1]["sequence"] == 3
