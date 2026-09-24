"""Minimal HTTP gateway composition entry point for MiniCode.

The gateway intentionally uses only the standard library so the Docker and
console entry points remain zero-dependency. Dashboard GET handling lives in
``minicode.web``; this module retains headless execution and server startup.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import ThreadingHTTPServer

from minicode.web import DashboardReadModel, MiniCodeWebHandler
from minicode.web.change_feed import DashboardChangeFeed
from minicode.web.event_stream import DashboardEventStream

MAX_REQUEST_BODY_BYTES = 1_048_576


class MiniCodeGatewayHandler(MiniCodeWebHandler):
    """Compose Dashboard GET routes with the existing headless POST route."""

    def do_POST(self) -> None:  # noqa: N802
        path = self._request_path()
        if path is None:
            self._send_json({"ok": False, "error": "invalid request path"}, status=400)
            return
        is_execution_path = path == "/run" or path == "/api/v1/chat/turns" or (
            path.startswith("/api/v1/chat/turns/")
            and path.endswith(("/cancel", "/feedback"))
        )
        if is_execution_path:
            from minicode.web.request_guard import ensure_execution_authorized

            if not ensure_execution_authorized(self):
                return
        if path == "/api/v1/chat/turns":
            from minicode.web.chat_http import serve_chat_turn

            serve_chat_turn(self)
            return
        if path.startswith("/api/v1/chat/turns/") and path.endswith("/cancel"):
            from minicode.web.chat_http import serve_chat_turn_cancel

            serve_chat_turn_cancel(self, path)
            return
        if path.startswith("/api/v1/chat/turns/") and path.endswith("/feedback"):
            from minicode.web.chat_http import serve_chat_turn_feedback

            serve_chat_turn_feedback(self, path)
            return
        if path.startswith("/api/v1/permissions/"):
            from minicode.web.permission_http import (
                is_permission_decision_path,
                serve_permission_decision,
            )

            if is_permission_decision_path(path):
                serve_permission_decision(self, path)
            else:
                self._send_api_not_found(path)
            return
        if path.startswith("/api/v1/memory/approvals/"):
            from minicode.web.memory_approval_http import (
                is_memory_approval_decision_path,
                serve_memory_approval_decision,
            )

            if is_memory_approval_decision_path(path) or path.endswith("/decision"):
                serve_memory_approval_decision(self, path)
            else:
                self._send_api_not_found(path)
            return
        from minicode.web.data_management_http import (
            is_deletion_path,
            serve_deletion_request,
        )

        if is_deletion_path(path):
            serve_deletion_request(self, path)
            return
        if path != "/run":
            if path.startswith("/api/"):
                self._send_api_not_found(path)
            else:
                self._send_not_found()
            return

        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            # A JSON content type forces a CORS preflight for browser-issued
            # requests; text/plain form posts must never reach the agent.
            self._send_json(
                {"ok": False, "error": "Content-Type must be application/json"},
                status=415,
            )
            return

        try:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                self._send_json(
                    {"ok": False, "error": "invalid content length"},
                    status=400,
                )
                return
            if length < 0:
                self._send_json(
                    {"ok": False, "error": "invalid content length"},
                    status=400,
                )
                return
            if length > MAX_REQUEST_BODY_BYTES:
                self._send_json(
                    {"ok": False, "error": "request body too large"},
                    status=413,
                )
                return
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw) if raw.strip() else {}
            prompt = str(data.get("prompt", "")).strip()
            if not prompt:
                self._send_json({"ok": False, "error": "prompt is required"}, status=400)
                return

            from minicode.headless import run_headless

            run_kwargs = {"run_source": "gateway"}
            state_registry = getattr(
                self.server,
                "mcp_current_state_registry",
                None,
            )
            if state_registry is not None:
                run_kwargs["mcp_current_state_registry"] = state_registry
            self._send_json(
                {
                    "ok": True,
                    "response": run_headless(prompt, **run_kwargs),
                }
            )
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            if isinstance(exc, SystemExit):
                message = str(exc) or f"headless exited with status {exc.code}"
                print("CodeLoop gateway headless execution exited.", file=sys.stderr)
                self._send_json({"ok": False, "error": message}, status=500)
                return
            self._send_json({"ok": False, "error": str(exc)}, status=500)


def run_gateway() -> None:
    from minicode.config import MINI_CODE_DIR
    from minicode.conversation_deletion import ConversationDeletionAuthority
    from minicode.conversation import ConversationTurnService
    from minicode.mcp_current_state import McpCurrentStateRegistry
    from minicode.permission_approval import (
        PermissionApprovalBroker,
        is_loopback_gateway_host,
    )
    from minicode.memory_approval import MemoryApprovalAuthority
    from minicode.project_memory_deletion import ProjectMemoryDeletionAuthority
    from minicode.storage_health import PersistenceHealthReader

    host = os.environ.get("MINI_CODE_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("MINI_CODE_GATEWAY_PORT", "8080"))
    current_state_registry = McpCurrentStateRegistry()
    read_model = DashboardReadModel.from_environment(
        mcp_current_state_loader=lambda server_keys: (
            current_state_registry.snapshot_for(server_keys).to_dict()
        )
    )
    server = ThreadingHTTPServer((host, port), MiniCodeGatewayHandler)
    server.dashboard_read_model = read_model
    dashboard_data_dir = getattr(read_model, "data_dir", MINI_CODE_DIR)
    server.persistence_health_reader = PersistenceHealthReader(
        read_model.workspace,
        data_dir=dashboard_data_dir,
    )
    approval_broker = (
        PermissionApprovalBroker(read_model.workspace)
        if is_loopback_gateway_host(host)
        else None
    )
    server.permission_approval_broker = approval_broker
    server.memory_approval_authority = (
        MemoryApprovalAuthority(read_model.workspace)
        if is_loopback_gateway_host(host)
        else None
    )
    server.conversation_deletion_authority = (
        ConversationDeletionAuthority(
            read_model.workspace,
            data_dir=dashboard_data_dir,
        )
        if is_loopback_gateway_host(host)
        else None
    )
    server.project_memory_deletion_authority = (
        ProjectMemoryDeletionAuthority(
            read_model.workspace,
            data_dir=dashboard_data_dir,
        )
        if is_loopback_gateway_host(host)
        else None
    )
    change_feed = DashboardChangeFeed(
        read_model.workspace,
        data_dir=getattr(read_model, "data_dir", None),
        mcp_current_state_loader=lambda: current_state_registry.snapshot_for(
            read_model.configured_mcp_server_keys()
        ).to_dict(),
        permission_revision_loader=(
            approval_broker.revision if approval_broker is not None else None
        ),
    )
    event_stream = DashboardEventStream(change_feed)
    server.dashboard_change_feed = change_feed
    server.dashboard_event_stream = event_stream
    server.mcp_current_state_registry = current_state_registry
    server.conversation_turn_service = ConversationTurnService(
        read_model.workspace,
        mcp_current_state_registry=current_state_registry,
        approval_broker=approval_broker,
    )
    try:
        event_stream.start()
        print(f"CodeLoop gateway listening on http://{host}:{port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if approval_broker is not None:
            approval_broker.close()
        event_stream.close()
        server.server_close()


if __name__ == "__main__":
    run_gateway()
