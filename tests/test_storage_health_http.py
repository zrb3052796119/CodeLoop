from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from minicode.gateway import MiniCodeGatewayHandler
from minicode.storage_health import PersistenceHealthReader


@pytest.fixture
def health_server(tmp_path: Path) -> Iterator[tuple[int, ThreadingHTTPServer]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MiniCodeGatewayHandler)
    server.persistence_health_reader = PersistenceHealthReader(
        workspace,
        data_dir=tmp_path / "data",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _get(port: int, path: str) -> tuple[int, dict[str, object], dict[str, str]]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", path, headers={"Accept": "application/json"})
    response = connection.getresponse()
    body = response.read()
    headers = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    return response.status, json.loads(body), headers


def test_data_health_get_is_strict_read_only_json_and_no_store(
    health_server: tuple[int, ThreadingHTTPServer],
) -> None:
    status, payload, headers = _get(health_server[0], "/api/v1/data-health")

    assert status == 200
    assert payload["schemaVersion"] == 1
    assert payload["mode"] == "read-only"
    assert payload["maintenancePlan"]["destructiveActionsAvailable"] is False
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "query",
    [
        "?path=/tmp/private",
        "?workspace=other",
        "?refresh=true",
        "?x=1&x=2",
        "?=",
    ],
)
def test_data_health_rejects_every_query_parameter(
    health_server: tuple[int, ThreadingHTTPServer],
    query: str,
) -> None:
    status, payload, headers = _get(health_server[0], f"/api/v1/data-health{query}")

    assert status == 400
    assert payload == {
        "ok": False,
        "error": {
            "code": "invalid_query",
            "message": "Query parameters are invalid.",
        },
    }
    assert headers["cache-control"] == "no-store"


def test_data_health_unexpected_failure_uses_a_fixed_safe_error(
    health_server: tuple[int, ThreadingHTTPServer],
) -> None:
    class BrokenReader:
        def snapshot(self) -> dict[str, object]:
            raise RuntimeError(
                "/Users/private/workspace secret=DO_NOT_EXPOSE command=rm"
            )

    health_server[1].persistence_health_reader = BrokenReader()
    status, payload, headers = _get(health_server[0], "/api/v1/data-health")

    assert status == 500
    assert payload == {
        "ok": False,
        "error": {
            "code": "data_health_failed",
            "message": "Data health could not be generated.",
        },
    }
    assert headers["cache-control"] == "no-store"
    assert "/Users/" not in json.dumps(payload)
    assert "DO_NOT_EXPOSE" not in json.dumps(payload)


def test_unknown_data_health_subroute_remains_structured_api_404(
    health_server: tuple[int, ThreadingHTTPServer],
) -> None:
    status, payload, _ = _get(health_server[0], "/api/v1/data-health/reset")

    assert status == 404
    assert payload["error"]["code"] == "not_found"
