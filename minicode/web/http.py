"""Standard-library HTTP adapter for the packaged Dashboard shell."""

from __future__ import annotations

import json
import math
import mimetypes
import os
import socket
from http.server import BaseHTTPRequestHandler
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from minicode.web.change_feed import DashboardChangeFeed
from minicode.web.event_stream import (
    DashboardEventStream,
    EventStreamBusy,
    InvalidEventCursor,
)
from minicode.web.read_model import DashboardReadError, DashboardReadModel


HEALTH_PAYLOAD = {"ok": True, "service": "minicode-gateway"}
STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


class MiniCodeWebHandler(BaseHTTPRequestHandler):
    """Serve the Dashboard shell and stable read-only Gateway GET routes."""

    server_version = "MiniCodeGateway/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if os.environ.get("MINI_CODE_GATEWAY_ACCESS_LOG") == "1":
            super().log_message(format, *args)

    def _send_body(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_body(body, "application/json; charset=utf-8", status)

    def _send_not_found(self) -> None:
        self._send_json({"ok": False, "error": "not found"}, status=404)

    def _send_api_not_found(self, path: str) -> None:
        self._send_json(
            {
                "ok": False,
                "error": {
                    "code": "not_found",
                    "message": "API route not found",
                    "path": path,
                },
            },
            status=404,
        )

    def _request_path(self) -> str | None:
        try:
            return unquote(urlsplit(self.path).path, encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            return None

    @staticmethod
    def _content_type(filename: str) -> str:
        suffix = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else ""
        if suffix in STATIC_CONTENT_TYPES:
            return STATIC_CONTENT_TYPES[suffix]
        guessed, _ = mimetypes.guess_type(filename)
        content_type = guessed or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            return f"{content_type}; charset=utf-8"
        return content_type

    def _serve_resource(self, *parts: str) -> None:
        resource = files("minicode.web").joinpath("static", *parts)
        try:
            if not resource.is_file():
                self._send_not_found()
                return
            body = resource.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError):
            self._send_not_found()
            return
        self._send_body(body, self._content_type(parts[-1]))

    def _serve_asset(self, asset_path: str) -> None:
        parts = asset_path.split("/")
        if not asset_path or any(
            part in {"", ".", ".."} or "\\" in part or "\x00" in part for part in parts
        ):
            self._send_json(
                {"ok": False, "error": "invalid asset path"},
                status=400,
            )
            return
        self._serve_resource("assets", *parts)

    def _dashboard_read_model(self) -> DashboardReadModel:
        read_model = getattr(self.server, "dashboard_read_model", None)
        if read_model is None:
            read_model = DashboardReadModel.from_environment()
            setattr(self.server, "dashboard_read_model", read_model)
        return read_model

    def _dashboard_change_feed(self) -> DashboardChangeFeed:
        change_feed = getattr(self.server, "dashboard_change_feed", None)
        if change_feed is None:
            read_model = self._dashboard_read_model()
            registry = getattr(self.server, "mcp_current_state_registry", None)
            permission_broker = getattr(
                self.server, "permission_approval_broker", None
            )
            current_state_loader = None
            if registry is not None:
                def current_state_loader() -> dict[str, object]:
                    return registry.snapshot_for(
                        read_model.configured_mcp_server_keys()
                    ).to_dict()
            change_feed = DashboardChangeFeed(
                read_model.workspace,
                data_dir=read_model.data_dir,
                mcp_current_state_loader=current_state_loader,
                permission_revision_loader=(
                    permission_broker.revision
                    if callable(getattr(permission_broker, "revision", None))
                    else None
                ),
            )
            setattr(self.server, "dashboard_change_feed", change_feed)
        return change_feed

    def _dashboard_event_stream(self) -> DashboardEventStream | None:
        stream = getattr(self.server, "dashboard_event_stream", None)
        return stream if isinstance(stream, DashboardEventStream) else None

    def _accepts_event_stream(self) -> bool:
        values = self.headers.get_all("Accept", [])
        if not values:
            return True
        rendered = ",".join(values)
        if len(rendered) > 1_024:
            return False
        for item in rendered.split(","):
            parts = [part.strip().lower() for part in item.split(";")]
            media_type = parts[0]
            quality = 1.0
            quality_seen = False
            valid = True
            for parameter in parts[1:]:
                if parameter.startswith("q="):
                    if quality_seen:
                        valid = False
                        break
                    quality_seen = True
                    try:
                        quality = float(parameter.removeprefix("q="))
                    except ValueError:
                        valid = False
                        break
                    if not math.isfinite(quality) or not 0 <= quality <= 1:
                        valid = False
                        break
            if (
                valid
                and media_type in {"text/event-stream", "*/*"}
                and quality > 0
            ):
                return True
        return False

    def _serve_event_stream(self) -> None:
        try:
            self._query_params(set())
        except DashboardReadError as error:
            self._send_read_error(error)
            return
        if not self._accepts_event_stream():
            self._send_json(
                {
                    "ok": False,
                    "error": {
                        "code": "not_acceptable",
                        "message": "This route requires text/event-stream.",
                    },
                },
                status=406,
            )
            return
        cursor_headers = self.headers.get_all("Last-Event-ID", [])
        if len(cursor_headers) > 1:
            self._send_json(
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_event_cursor",
                        "message": "Last-Event-ID is invalid.",
                    },
                },
                status=400,
            )
            return
        stream = self._dashboard_event_stream()
        if stream is None:
            self._send_json(
                {
                    "ok": False,
                    "error": {
                        "code": "events_unavailable",
                        "message": "Dashboard events are unavailable.",
                    },
                },
                status=503,
            )
            return
        try:
            subscription = stream.subscribe(
                cursor_headers[0] if cursor_headers else None
            )
        except InvalidEventCursor:
            self._send_json(
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_event_cursor",
                        "message": "Last-Event-ID is invalid.",
                    },
                },
                status=400,
            )
            return
        except EventStreamBusy:
            self._send_json(
                {
                    "ok": False,
                    "error": {
                        "code": "stream_busy",
                        "message": "Dashboard event capacity is temporarily full.",
                    },
                },
                status=503,
            )
            return
        except RuntimeError:
            self._send_json(
                {
                    "ok": False,
                    "error": {
                        "code": "events_unavailable",
                        "message": "Dashboard events are unavailable.",
                    },
                },
                status=503,
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        write_timeout = getattr(self.server, "dashboard_event_write_timeout", 5.0)
        if (
            isinstance(write_timeout, bool)
            or not isinstance(write_timeout, (int, float))
            or not 0.1 <= float(write_timeout) <= 30.0
        ):
            write_timeout = 5.0
        self._write_event_subscription(
            subscription,
            write_timeout=float(write_timeout),
        )

    def _write_event_subscription(
        self,
        subscription: Any,
        *,
        write_timeout: float,
    ) -> None:
        """Write one post-header subscription; failures close only this client."""
        try:
            self.connection.settimeout(write_timeout)
            while True:
                frames = subscription.next_batch()
                if not frames:
                    return
                for frame in frames:
                    self.wfile.write(frame)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout, OSError):
            return
        finally:
            subscription.close()
            self.close_connection = True

    def _conversation_turn_service(self) -> Any:
        service = getattr(self.server, "conversation_turn_service", None)
        if service is None:
            from minicode.conversation import ConversationTurnService

            read_model = self._dashboard_read_model()
            service = ConversationTurnService(
                read_model.workspace,
                mcp_current_state_registry=getattr(
                    self.server, "mcp_current_state_registry", None
                ),
                approval_broker=getattr(
                    self.server, "permission_approval_broker", None
                ),
            )
            setattr(self.server, "conversation_turn_service", service)
        return service

    def _query_params(self, allowed: set[str]) -> dict[str, str]:
        try:
            pairs = parse_qsl(
                urlsplit(self.path).query,
                keep_blank_values=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=10,
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise DashboardReadError(
                400, "invalid_query", "Query parameters are invalid."
            ) from exc
        params: dict[str, str] = {}
        for key, value in pairs:
            if key not in allowed or key in params or value == "":
                raise DashboardReadError(
                    400, "invalid_query", "Query parameters are invalid."
                )
            params[key] = value
        return params

    def _send_read_error(self, error: DashboardReadError) -> None:
        self._send_json(
            {
                "ok": False,
                "error": {"code": error.code, "message": error.message},
            },
            status=error.status,
        )

    def _send_read_failure(self, code: str, message: str) -> None:
        self._send_json(
            {"ok": False, "error": {"code": code, "message": message}},
            status=500,
        )

    def do_GET(self) -> None:  # noqa: N802
        path = self._request_path()
        if path is None:
            self._send_json({"ok": False, "error": "invalid request path"}, status=400)
            return
        if path == "/":
            self._serve_resource("index.html")
            return
        if path in {"/health", "/api/v1/health"}:
            self._send_json(HEALTH_PAYLOAD)
            return
        if path == "/api/v1/changes":
            try:
                self._query_params(set())
                self._send_json(self._dashboard_change_feed().snapshot())
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "changes_failed", "Change data could not be generated."
                )
            return
        if path == "/api/v1/events":
            self._serve_event_stream()
            return
        if path == "/api/v1/permissions/pending":
            from minicode.web.permission_http import serve_permission_pending

            serve_permission_pending(self)
            return
        if path == "/api/v1/memory/approvals/pending":
            from minicode.web.memory_approval_http import (
                serve_memory_approval_pending,
            )

            serve_memory_approval_pending(self)
            return
        from minicode.web.data_management_http import (
            is_deletion_path,
            serve_deletion_preview,
        )

        if is_deletion_path(path):
            serve_deletion_preview(self, path)
            return
        if path.startswith("/api/v1/chat/turns/"):
            from minicode.web.chat_http import serve_chat_turn_status

            serve_chat_turn_status(self, path)
            return
        if path == "/api/v1/runs":
            try:
                params = self._query_params(
                    {"status", "source", "limit", "cursor"}
                )
                self._send_json(self._dashboard_read_model().runs(**params))
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "runs_failed", "Runs could not be generated."
                )
            return
        if path.startswith("/api/v1/runs/"):
            try:
                params = self._query_params({"limit", "cursor"})
                run_id = path.removeprefix("/api/v1/runs/")
                self._send_json(
                    self._dashboard_read_model().run_detail(
                        run_id,
                        limit=params.get("limit"),
                        cursor=params.get("cursor"),
                    )
                )
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "run_failed", "Run detail could not be generated."
                )
            return
        if path == "/api/v1/sessions":
            try:
                params = self._query_params({"limit", "cursor"})
                self._send_json(
                    self._dashboard_read_model().sessions(
                        limit=params.get("limit"), cursor=params.get("cursor")
                    )
                )
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "sessions_failed", "Sessions could not be generated."
                )
            return
        if path.startswith("/api/v1/sessions/"):
            try:
                params = self._query_params({"limit", "cursor"})
                session_id = path.removeprefix("/api/v1/sessions/")
                self._send_json(
                    self._dashboard_read_model().session_detail(
                        session_id,
                        limit=params.get("limit"),
                        cursor=params.get("cursor"),
                    )
                )
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "session_failed", "Session detail could not be generated."
                )
            return
        if path == "/api/v1/memory":
            try:
                params = self._query_params(
                    {"scope", "tier", "category", "limit", "cursor"}
                )
                self._send_json(self._dashboard_read_model().memory(**params))
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "memory_failed", "Memory data could not be generated."
                )
            return
        if path == "/api/v1/skills":
            try:
                params = self._query_params(
                    {"source", "directory", "limit", "cursor"}
                )
                self._send_json(self._dashboard_read_model().skills(**params))
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "skills_failed", "Skill data could not be generated."
                )
            return
        if path == "/api/v1/connections":
            try:
                self._query_params(set())
                self._send_json(self._dashboard_read_model().connections())
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "connections_failed", "Connection data could not be generated."
                )
            return
        if path == "/api/v1/system":
            try:
                self._query_params(set())
                self._send_json(self._dashboard_read_model().system())
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "system_failed", "System data could not be generated."
                )
            return
        if path == "/api/v1/data-health":
            from minicode.web.storage_health_http import serve_data_health

            serve_data_health(self)
            return
        if path == "/api/v1/ops":
            try:
                self._query_params(set())
                self._send_json(self._dashboard_read_model().ops())
            except DashboardReadError as error:
                self._send_read_error(error)
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_read_failure(
                    "ops_failed", "Ops data could not be generated."
                )
            return
        if path == "/api/v1/snapshot":
            try:
                self._send_json(self._dashboard_read_model().snapshot())
            except Exception:  # noqa: BLE001 - never expose source exception text
                self._send_json(
                    {
                        "ok": False,
                        "error": {
                            "code": "snapshot_failed",
                            "message": "Dashboard snapshot could not be generated.",
                        },
                    },
                    status=500,
                )
            return
        if path.startswith("/assets/"):
            self._serve_asset(path.removeprefix("/assets/"))
            return
        if path.startswith("/api/"):
            self._send_api_not_found(path)
            return
        self._send_not_found()
