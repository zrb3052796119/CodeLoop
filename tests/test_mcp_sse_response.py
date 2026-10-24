"""Exercise SSE response decoding without opening a network connection."""

from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from minicode.mcp import HttpMcpClient


class SseResponse(BytesIO):
    def __init__(self, body: str) -> None:
        super().__init__(body.encode("utf-8"))
        self.headers = Message()
        self.headers["Content-Type"] = "text/event-stream"


@pytest.mark.parametrize(
    "body",
    [
        'data: {"jsonrpc":"2.0","id":8,"result":{}}\n\n',
        'data: {"jsonrpc":"2.0","method":"notifications/ping"}\n\n',
        "data: malformed-json\n\n",
    ],
)
def test_sse_missing_response_reports_server_and_request(
    tmp_path: Path, body: str,
) -> None:
    client = HttpMcpClient("fixture-sse", {"url": "https://example.invalid/mcp"}, str(tmp_path))

    with pytest.raises(
        RuntimeError,
        match=r"MCP fixture-sse: SSE stream ended without a response for request id 7\.",
    ):
        client._parse_response_body(SseResponse(body), expected_id=7)


def test_sse_response_skips_unrelated_events(tmp_path: Path) -> None:
    client = HttpMcpClient("fixture-sse", {"url": "https://example.invalid/mcp"}, str(tmp_path))
    response = SseResponse(
        'data: {"jsonrpc":"2.0","method":"notifications/ping"}\n\n'
        'data: {"jsonrpc":"2.0","id":8,"result":{}}\n\n'
        'data: {"jsonrpc":"2.0","id":7,"result":{"answer":"ready"}}\n\n'
    )

    assert client._parse_response_body(response, expected_id=7) == {
        "jsonrpc": "2.0", "id": 7, "result": {"answer": "ready"},
    }
