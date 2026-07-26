from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

import minicode.permissions as permissions_module
from minicode.agent_runtime import AgentTurnRuntime
from minicode.conversation import ConversationTurnService
from minicode.conversation_turn_store import ConversationTurnStore
from minicode.gateway import MiniCodeGatewayHandler
from minicode.permission_approval import PermissionApprovalBroker
from minicode.permissions import PermissionManager
from minicode.run_journal import RunJournal
from minicode.session import SessionData
from minicode.tooling import ToolRegistry
from minicode.tools.write_file import write_file_tool
from minicode.types import AgentStep
from minicode.web.read_model import DashboardReadModel


class ScriptedWriteModel:
    def __init__(self, contents: list[str]) -> None:
        self._steps = [
            AgentStep(
                type="tool_calls",
                calls=[
                    {
                        "id": f"provider_{index}",
                        "toolName": "write_file",
                        "input": {"path": "approved.txt", "content": content},
                    }
                ],
            )
            for index, content in enumerate(contents)
        ]
        self._steps.append(AgentStep(type="assistant", content="tool turn complete"))
        self.calls = 0

    def next(self, _messages, on_stream_chunk=None):
        step = self._steps[self.calls]
        self.calls += 1
        return step


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read())
    finally:
        connection.close()


def _wait_pending(port: int, *, previous: str | None = None) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status, _, payload = _request(
            port, "GET", "/api/v1/permissions/pending"
        )
        assert status == 200
        items = payload["items"]
        if items and items[0]["permissionId"] != previous:
            return items[0]
        time.sleep(0.005)
    raise AssertionError("permission request did not become pending")


def _build_service(
    tmp_path: Path,
    *,
    contents: list[str],
) -> tuple[ConversationTurnService, PermissionApprovalBroker, dict[str, SessionData], Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "state"
    sessions: dict[str, SessionData] = {}
    model = ScriptedWriteModel(contents)
    permissions = PermissionManager(str(workspace), prompt=None)
    runtime = AgentTurnRuntime(
        workspace=workspace,
        runtime={},
        tools=ToolRegistry([write_file_tool]),
        permissions=permissions,
        memory_manager=None,
        model=model,
        skill_routing=None,
        system_prompt="safe system",
    )
    broker = PermissionApprovalBroker(workspace, timeout_seconds=2)

    def create(workspace_value: str) -> SessionData:
        session = SessionData(
            session_id="session_permission",
            created_at=time.time(),
            updated_at=time.time(),
            workspace=workspace_value,
        )
        sessions[session.session_id] = session
        return session

    service = ConversationTurnService(
        workspace,
        runtime_factory=lambda **_kwargs: runtime,
        session_loader=sessions.get,
        session_creator=create,
        session_saver=lambda session: sessions.__setitem__(session.session_id, session),
        journal_factory=lambda current: RunJournal(current, data_dir=data_dir),
        turn_store=ConversationTurnStore(workspace, data_dir=data_dir),
        approval_broker=broker,
    )
    return service, broker, sessions, data_dir


@pytest.fixture(autouse=True)
def isolated_permission_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store_path = tmp_path / "permissions.json"
    monkeypatch.setattr(permissions_module, "MINI_CODE_PERMISSIONS_PATH", store_path)
    permissions_module._normalize_path_cached.cache_clear()
    yield
    permissions_module._normalize_path_cached.cache_clear()


@pytest.mark.parametrize("decision", ["allow_once", "deny_once"])
def test_real_chat_permission_decision_controls_file_and_safe_run_events(
    tmp_path: Path,
    decision: str,
) -> None:
    service, broker, sessions, data_dir = _build_service(
        tmp_path, contents=["approved content\n"]
    )
    server = __import__("http.server").server.ThreadingHTTPServer(
        ("127.0.0.1", 0), MiniCodeGatewayHandler
    )
    server.conversation_turn_service = service
    server.permission_approval_broker = broker
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    turn_id = "turn_" + ("1" if decision == "allow_once" else "2") * 32
    outcome: dict[str, object] = {}

    def chat() -> None:
        outcome["response"] = _request(
            server.server_address[1],
            "POST",
            "/api/v1/chat/turns",
            body=json.dumps(
                {"message": "write the file", "sessionId": None, "turnId": turn_id}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )

    chat_thread = threading.Thread(target=chat)
    chat_thread.start()
    try:
        item = _wait_pending(server.server_address[1])
        assert item["turnId"] == turn_id
        assert item["toolName"] == "write_file"
        assert item["toolOperationId"].startswith("permissiontool_")
        assert item["runId"].startswith("run_")
        target = tmp_path / "workspace" / "approved.txt"
        assert not target.exists()

        decision_status, _, decision_payload = _request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{item['permissionId']}/decision",
            body=json.dumps({"turnId": turn_id, "decision": decision}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert decision_status == 200
        assert decision_payload["decisionAccepted"] is True
        chat_thread.join(timeout=5)
        assert not chat_thread.is_alive()
        chat_status, _, chat_payload = outcome["response"]
        assert chat_status == 200
        assert chat_payload["assistant"] == {
            "role": "assistant",
            "content": "tool turn complete",
        }
        assert target.exists() is (decision == "allow_once")
        assert sessions["session_permission"].messages[-1]["content"] == "tool turn complete"

        journal = RunJournal(tmp_path / "workspace", data_dir=data_dir)
        run_id = chat_payload["runId"]
        events = journal.list_events(run_id, limit=50).items
        relevant = [
            event.type
            for event in events
            if event.type
            in {
                "tool.started",
                "permission.requested",
                "permission.decided",
                "tool.finished",
            }
        ]
        assert relevant == [
            "tool.started",
            "permission.requested",
            "permission.decided",
            "tool.finished",
        ]
        permission_events = [
            event for event in events if event.type.startswith("permission.")
        ]
        serialized = json.dumps([event.payload for event in permission_events])
        assert "approved.txt" not in serialized
        assert "approved content" not in serialized
        assert permission_events[-1].payload["decisionKind"] == (
            "allowed" if decision == "allow_once" else "denied"
        )
        tool_finished = next(event for event in events if event.type == "tool.finished")
        assert tool_finished.payload["outcome"] == (
            "success" if decision == "allow_once" else "error"
        )
        run_detail = DashboardReadModel(
            tmp_path / "workspace",
            data_dir=data_dir,
            run_journal=RunJournal(tmp_path / "workspace", data_dir=data_dir),
            session_loader=lambda: [],
            skill_loader=lambda _workspace: [],
        ).run_detail(run_id)
        serialized_detail = json.dumps(run_detail)
        assert "approved.txt" not in serialized_detail
        assert "approved content" not in serialized_detail
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_same_chat_turn_requires_two_distinct_approvals_for_same_file(tmp_path: Path) -> None:
    service, broker, _sessions, _data_dir = _build_service(
        tmp_path, contents=["first\n", "second\n"]
    )
    server = __import__("http.server").server.ThreadingHTTPServer(
        ("127.0.0.1", 0), MiniCodeGatewayHandler
    )
    server.conversation_turn_service = service
    server.permission_approval_broker = broker
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    turn_id = "turn_" + "3" * 32
    outcome: dict[str, object] = {}
    chat_thread = threading.Thread(
        target=lambda: outcome.setdefault(
            "response",
            _request(
                server.server_address[1],
                "POST",
                "/api/v1/chat/turns",
                body=json.dumps(
                    {"message": "write twice", "sessionId": None, "turnId": turn_id}
                ).encode(),
                headers={"Content-Type": "application/json"},
            ),
        )
    )
    chat_thread.start()
    try:
        first = _wait_pending(server.server_address[1])
        _request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{first['permissionId']}/decision",
            body=json.dumps({"turnId": turn_id, "decision": "allow_once"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        second = _wait_pending(
            server.server_address[1], previous=first["permissionId"]
        )
        assert second["permissionId"] != first["permissionId"]
        _request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{second['permissionId']}/decision",
            body=json.dumps({"turnId": turn_id, "decision": "allow_once"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        chat_thread.join(timeout=5)
        assert outcome["response"][0] == 200
        assert (tmp_path / "workspace" / "approved.txt").read_text() == "second\n"
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_chat_cancel_actively_wakes_pending_permission_and_late_allow_fails(
    tmp_path: Path,
) -> None:
    service, broker, _sessions, _data_dir = _build_service(
        tmp_path, contents=["must not write\n"]
    )
    server = __import__("http.server").server.ThreadingHTTPServer(
        ("127.0.0.1", 0), MiniCodeGatewayHandler
    )
    server.conversation_turn_service = service
    server.permission_approval_broker = broker
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    turn_id = "turn_" + "4" * 32
    outcome: dict[str, object] = {}
    chat_thread = threading.Thread(
        target=lambda: outcome.setdefault(
            "response",
            _request(
                server.server_address[1],
                "POST",
                "/api/v1/chat/turns",
                body=json.dumps(
                    {"message": "cancel this write", "sessionId": None, "turnId": turn_id}
                ).encode(),
                headers={"Content-Type": "application/json"},
            ),
        )
    )
    chat_thread.start()
    try:
        item = _wait_pending(server.server_address[1])
        cancel_status, _, cancel = _request(
            server.server_address[1],
            "POST",
            f"/api/v1/chat/turns/{turn_id}/cancel",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert cancel_status == 200
        assert cancel["cancellationAccepted"] is True
        chat_thread.join(timeout=5)
        assert not chat_thread.is_alive()
        assert outcome["response"][2]["error"]["code"] == "turn_cancelled"
        assert not (tmp_path / "workspace" / "approved.txt").exists()
        late_status, _, late = _request(
            server.server_address[1],
            "POST",
            f"/api/v1/permissions/{item['permissionId']}/decision",
            body=json.dumps({"turnId": turn_id, "decision": "allow_once"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert late_status == 409
        assert late["error"]["code"] == "permission_cancelled"
    finally:
        broker.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
