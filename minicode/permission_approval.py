"""Process-local, workspace-scoped Gateway permission approval authority.

The broker is deliberately synchronous at the PermissionManager boundary while
remaining queryable and decidable from other Gateway request threads. It owns
all pending state, safe Web projections, timeouts, cancellation, tombstones,
and content-free Run event emission. It never judges or executes a Tool.
"""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
import socket
import threading
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from minicode.file_review import contains_unsafe_review_character
from minicode.permission_event_contract import normalize_permission_event_payload
from minicode.turn_cancellation import (
    TurnCancellationRequested,
    TurnCancellationToken,
)


PermissionStatus = Literal[
    "pending", "allowed", "denied", "expired", "cancelled", "closed"
]
WebPermissionDecision = Literal["allow_once", "deny_once"]

PERMISSION_ID_RE = re.compile(r"^permission_[0-9a-f]{32}$")
TOOL_OPERATION_ID_RE = re.compile(r"^permissiontool_[0-9a-f]{32}$")
TURN_ID_RE = re.compile(r"^turn_[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^run_[0-9a-f]{32}$")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

DEFAULT_APPROVAL_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_PENDING = 16
DEFAULT_TOMBSTONE_LIMIT = 256
DEFAULT_TOMBSTONE_TTL_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.1
MAX_DIFF_PREVIEW_BYTES = 32 * 1024
MAX_COMMAND_PREVIEW_BYTES = 4 * 1024
MAX_REVIEW_BYTES = 40 * 1024
MAX_SNAPSHOT_BYTES = 128 * 1024

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_TOKEN_RE = re.compile(r"(?i)\bsk-[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|"
    r"secret|credential|authorization|cookie)\b\s*[:=]"
)
_WEB_URL_RE = re.compile(r"(?i)https?://[^\s'\"<>]+")
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s=:(\[{'\"`,;<>+\-])/(?=[^\s/])", re.MULTILINE
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9._~%+\-])[a-z]:[\\/]"
)
_WINDOWS_UNC_PATH_RE = re.compile(r"(?<![A-Za-z0-9._~%+\-])\\\\[^\\\s]+")
_HOME_PATH_RE = re.compile(r"(?<![A-Za-z0-9._~%+\-])~(?:/|$)")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_META_RE = re.compile(r"[|&;<>()$`\r\n]")
_SENSITIVE_REVIEW_NAMES = frozenset(
    {
        "password",
        "passwd",
        "token",
        "accesstoken",
        "authtoken",
        "apikey",
        "secret",
        "credential",
        "credentials",
        "authorization",
        "cookie",
        "user",
        "username",
    }
)
_SENSITIVE_SHORT_OPTIONS = frozenset({"-p", "-u"})
_SHELL_INTERPRETERS = frozenset(
    {"bash", "sh", "zsh", "fish", "cmd", "powershell", "pwsh"}
)
_SHELL_COMMAND_OPTIONS = frozenset({"-c", "-lc", "/c", "-command", "/command"})
_REDACTED_REVIEW = "[REDACTED SENSITIVE REVIEW]"
_UNAVAILABLE_COMMAND_REASON = "Command review is unavailable."
_NETWORK_FINGERPRINT_RE = re.compile(r"^networkreq_[0-9a-f]{64}$")
_NETWORK_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _safe_network_review_hostname(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return (
            _NETWORK_HOST_RE.fullmatch(value) is not None
            and value != "localhost"
            and not value.endswith(".localhost")
        )
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(
        address.is_global
        and not address.is_loopback
        and not address.is_private
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and (
            mapped is None
            or mapped.is_global
            and not mapped.is_loopback
            and not mapped.is_private
            and not mapped.is_link_local
            and not mapped.is_multicast
            and not mapped.is_reserved
            and not mapped.is_unspecified
        )
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _safe_tool_name(value: object) -> str:
    return value if isinstance(value, str) and TOOL_NAME_RE.fullmatch(value) else "unknown"


def is_loopback_gateway_host(host: str) -> bool:
    """Return true only when a configured bind host resolves entirely locally."""
    if not isinstance(host, str) or not host.strip():
        return False
    candidate = host.strip().removeprefix("[").removesuffix("]")
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        pass
    if candidate.casefold() != "localhost":
        return False
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(candidate, None, type=socket.SOCK_STREAM)
        }
    except socket.gaierror:
        return False
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_loopback for address in addresses)
    except ValueError:
        return False


class PermissionApprovalError(RuntimeError):
    """Fixed-code authority error suitable for a safe HTTP adapter."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PermissionDecisionResult:
    permission_id: str
    turn_id: str
    status: PermissionStatus
    decision: WebPermissionDecision
    decision_accepted: bool
    updated_at: str


@dataclass(frozen=True, slots=True)
class _ToolBinding:
    session_key: str
    operation_id: str
    tool_name: str


_CURRENT_TOOL: ContextVar[_ToolBinding | None] = ContextVar(
    "minicode_permission_tool", default=None
)


@dataclass(slots=True)
class _ProjectedRequest:
    kind: str
    summary: str
    reviewable: bool
    review: dict[str, object]


@dataclass(slots=True)
class _ApprovalRequest:
    permission_id: str
    session_key: str
    turn_id: str
    run_id: str | None
    tool_operation_id: str
    tool_name: str
    kind: str
    summary: str
    reviewable: bool
    review: dict[str, object]
    choices: list[str]
    created_at: str
    expires_at: str
    deadline: float
    updated_at: str
    status: PermissionStatus = "pending"
    decision: WebPermissionDecision | None = None
    terminal_at: float | None = None
    wake: threading.Event = field(default_factory=threading.Event)
    _session: object | None = field(default=None, repr=False)

    def public_item(self) -> dict[str, object]:
        return {
            "permissionId": self.permission_id,
            "turnId": self.turn_id,
            "runId": self.run_id,
            "toolOperationId": self.tool_operation_id,
            "toolName": self.tool_name,
            "kind": self.kind,
            "summary": self.summary,
            "reviewable": self.reviewable,
            "review": dict(self.review),
            "choices": list(self.choices),
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
        }


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    if max_bytes <= 0:
        return "", True
    marker = "…"
    marker_bytes = marker.encode("utf-8")
    if max_bytes < len(marker_bytes):
        return "." * max_bytes, True
    clipped = encoded[: max_bytes - len(marker_bytes)]
    return clipped.decode("utf-8", errors="ignore") + marker, True


def _normalized_review_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _is_sensitive_review_name(value: str) -> bool:
    normalized = _normalized_review_name(value)
    return normalized in _SENSITIVE_REVIEW_NAMES or normalized.endswith(
        ("apikey", "authorization", "cookie")
    )


def _url_has_userinfo(value: str) -> bool:
    for match in _WEB_URL_RE.finditer(value):
        try:
            parsed = urlsplit(match.group(0))
        except ValueError:
            return True
        if parsed.username is not None or parsed.password is not None:
            return True
    return False


def _contains_sensitive_review_value(value: str) -> bool:
    if (
        "\x00" in value
        or _BEARER_RE.search(value)
        or _KEY_TOKEN_RE.search(value)
        or _PRIVATE_KEY_RE.search(value)
        or _ASSIGNMENT_RE.search(value)
        or _url_has_userinfo(value)
    ):
        return True
    stripped = value.strip()
    if not stripped:
        return False
    if stripped[:2].casefold() in _SENSITIVE_SHORT_OPTIONS:
        return True
    candidate = stripped.lstrip("-")
    for separator in ("=", ":"):
        if separator in candidate:
            candidate = candidate.split(separator, 1)[0]
            break
    return _is_sensitive_review_name(candidate)


def _contains_local_absolute_path(value: str) -> bool:
    if "\x00" in value:
        return True
    without_web_urls = _WEB_URL_RE.sub("[WEB_URL]", value)
    return bool(
        Path(without_web_urls).is_absolute()
        or _POSIX_ABSOLUTE_PATH_RE.search(without_web_urls)
        or _WINDOWS_ABSOLUTE_PATH_RE.search(without_web_urls)
        or _WINDOWS_UNC_PATH_RE.search(without_web_urls)
        or _HOME_PATH_RE.search(without_web_urls)
    )


def _is_complex_shell_review(command: str, args: list[str]) -> bool:
    if (
        not command
        or command != command.strip()
        or any(character.isspace() for character in command)
        or _SHELL_META_RE.search(command)
        or "'" in command
        or '"' in command
    ):
        return True
    command_name = Path(command).name.casefold()
    if command_name in _SHELL_INTERPRETERS and any(
        arg.casefold() in _SHELL_COMMAND_OPTIONS for arg in args
    ):
        return True
    return any(
        "\x00" in arg
        or _SHELL_META_RE.search(arg) is not None
        or _ENV_ASSIGNMENT_RE.match(arg) is not None
        for arg in args
    )


def _reason_review_is_unsafe(reason: str) -> bool:
    try:
        tokens = shlex.split(reason)
    except ValueError:
        return True
    return any(_contains_sensitive_review_value(token) for token in tokens)


def _command_review_is_unsafe(command: str, args: list[str], reason: str) -> bool:
    structured_values = [command, *args]
    return bool(
        _is_complex_shell_review(command, args)
        or any(_contains_sensitive_review_value(value) for value in structured_values)
        or any(_contains_local_absolute_path(value) for value in structured_values)
        or _reason_review_is_unsafe(reason)
        or _contains_local_absolute_path(reason)
    )


def _redact_review_text(value: str, *, workspace: Path) -> tuple[str, bool]:
    redacted = value
    changed = False
    replacements = [str(workspace), str(Path.home().expanduser().resolve())]
    for sensitive_path in replacements:
        if sensitive_path and sensitive_path in redacted:
            redacted = redacted.replace(sensitive_path, "[LOCAL_PATH]")
            changed = True
    if (
        _contains_sensitive_review_value(redacted)
        or _contains_local_absolute_path(redacted)
        or contains_unsafe_review_character(redacted)
    ):
        return "[REDACTED SENSITIVE REVIEW]", True
    return redacted, changed


def _redact_edit_diff(
    value: str,
    *,
    workspace: Path,
    target_path: str,
) -> tuple[str, bool]:
    if contains_unsafe_review_character(value):
        return _REDACTED_REVIEW, True
    lines = value.split("\n")
    expected_headers = [f"--- a/{target_path}", f"+++ b/{target_path}"]
    if len(lines) < 2 or lines[:2] != expected_headers:
        return _REDACTED_REVIEW, True
    _safe_body, redacted = _redact_review_text(
        "\n".join(lines[2:]),
        workspace=workspace,
    )
    return (_REDACTED_REVIEW, True) if redacted else (value, False)


def _relative_workspace_path(workspace: Path, value: str) -> str | None:
    try:
        target = Path(value).expanduser().resolve()
        relative = target.relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        return None
    rendered = relative.as_posix()
    return rendered if rendered and rendered != "." else "."


def _project_request(workspace: Path, request: object) -> _ProjectedRequest:
    unavailable = _ProjectedRequest(
        kind="path",
        summary="Permission review is unavailable.",
        reviewable=False,
        review={},
    )
    if not isinstance(request, dict):
        return unavailable
    version = request.get("schemaVersion")
    kind = request.get("kind")
    review = request.get("review")
    if (
        isinstance(version, bool)
        or version != 1
        or kind not in {"edit", "command", "path", "network"}
        or not isinstance(review, dict)
    ):
        return unavailable

    if kind == "network":
        fields = {
            "reviewVersion",
            "method",
            "scheme",
            "hostname",
            "port",
            "pathSummary",
            "hasBody",
            "hasSensitiveHeaders",
            "requestFingerprint",
        }
        if set(review) != fields:
            return _ProjectedRequest(
                "network", "Review a network request.", False, {}
            )
        review_version = review.get("reviewVersion")
        method = review.get("method")
        scheme = review.get("scheme")
        hostname = review.get("hostname")
        port = review.get("port")
        path_summary = review.get("pathSummary")
        has_body = review.get("hasBody")
        has_sensitive_headers = review.get("hasSensitiveHeaders")
        fingerprint = review.get("requestFingerprint")
        valid = (
            not isinstance(review_version, bool)
            and review_version == 1
            and method in {"POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
            and scheme == "https"
            and _safe_network_review_hostname(hostname)
            and isinstance(port, int)
            and not isinstance(port, bool)
            and 1 <= port <= 65535
            and isinstance(path_summary, str)
            and path_summary.startswith("/")
            and len(path_summary.encode("utf-8")) <= 256
            and "?" not in path_summary
            and "#" not in path_summary
            and not contains_unsafe_review_character(path_summary)
            and isinstance(has_body, bool)
            and isinstance(has_sensitive_headers, bool)
            and isinstance(fingerprint, str)
            and _NETWORK_FINGERPRINT_RE.fullmatch(fingerprint) is not None
        )
        return _ProjectedRequest(
            "network",
            "Review a network request.",
            valid,
            dict(review) if valid else {},
        )

    if kind == "edit":
        if set(review) != {"targetPath", "diffPreview"}:
            return _ProjectedRequest("edit", "Review a file modification.", False, {})
        target = review.get("targetPath")
        diff = review.get("diffPreview")
        if not isinstance(target, str) or not isinstance(diff, str):
            return _ProjectedRequest("edit", "Review a file modification.", False, {})
        relative = _relative_workspace_path(workspace, target)
        if relative is None:
            return _ProjectedRequest(
                "edit",
                "Review a file modification.",
                False,
                {"outsideWorkspace": True},
            )
        safe_diff, redacted = _redact_edit_diff(
            diff,
            workspace=workspace,
            target_path=relative,
        )
        safe_diff, truncated = _truncate_utf8(safe_diff, MAX_DIFF_PREVIEW_BYTES)
        projected = {
            "targetPath": relative,
            "diffPreview": safe_diff,
            "complete": not truncated,
            "truncated": truncated,
            "redacted": redacted,
        }
        reviewable = not redacted and not truncated
        if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) > MAX_REVIEW_BYTES:
            projected = {
                "targetPath": relative,
                "diffPreview": "",
                "complete": False,
                "truncated": True,
                "redacted": redacted,
            }
            reviewable = False
        return _ProjectedRequest(
            "edit", "Review a file modification.", reviewable, projected
        )

    if kind == "command":
        if set(review) != {"command", "args", "cwd", "reason"}:
            return _ProjectedRequest("command", "Review a command.", False, {})
        command = review.get("command")
        args = review.get("args")
        cwd = review.get("cwd")
        reason = review.get("reason")
        if (
            not isinstance(command, str)
            or not command
            or not isinstance(args, list)
            or any(not isinstance(arg, str) for arg in args)
            or not isinstance(cwd, str)
            or not isinstance(reason, str)
        ):
            return _ProjectedRequest("command", "Review a command.", False, {})
        relative_cwd = _relative_workspace_path(workspace, cwd)
        if relative_cwd is None:
            return _ProjectedRequest(
                "command",
                "Review a command.",
                False,
                {"outsideWorkspace": True},
            )
        unsafe = _command_review_is_unsafe(command, args, reason)
        if unsafe:
            preview = _REDACTED_REVIEW
            safe_reason = _UNAVAILABLE_COMMAND_REASON
            command_redacted = True
            reason_redacted = True
        else:
            preview = shlex.join([command, *args])
            preview, command_redacted = _redact_review_text(
                preview, workspace=workspace
            )
            safe_reason, reason_redacted = _redact_review_text(
                reason, workspace=workspace
            )
            if command_redacted or reason_redacted:
                preview = _REDACTED_REVIEW
                safe_reason = _UNAVAILABLE_COMMAND_REASON
                command_redacted = True
                reason_redacted = True
        preview, command_truncated = _truncate_utf8(
            preview, MAX_COMMAND_PREVIEW_BYTES
        )
        safe_reason, reason_truncated = _truncate_utf8(safe_reason, 1_024)
        redacted = command_redacted or reason_redacted
        truncated = command_truncated or reason_truncated
        projected = {
            "commandPreview": preview,
            "cwd": relative_cwd,
            "reason": safe_reason,
            "complete": not truncated and not redacted,
            "truncated": truncated,
            "redacted": redacted,
        }
        reviewable = not redacted and not truncated
        if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) > MAX_REVIEW_BYTES:
            projected = {
                "commandPreview": "",
                "cwd": relative_cwd,
                "reason": "Command review exceeded the safe limit.",
                "complete": False,
                "truncated": True,
                "redacted": redacted,
            }
            reviewable = False
        return _ProjectedRequest("command", "Review a command.", reviewable, projected)

    if set(review) != {"targetPath", "intent", "scopeDirectory"}:
        return _ProjectedRequest("path", "Review external path access.", False, {})
    target = review.get("targetPath")
    intent = review.get("intent")
    scope = review.get("scopeDirectory")
    if (
        not isinstance(target, str)
        or not isinstance(intent, str)
        or not intent
        or not isinstance(scope, str)
    ):
        return _ProjectedRequest("path", "Review external path access.", False, {})
    # Path prompts are generated only for workspace-external access. Batch 8A-1
    # deliberately exposes no absolute path and never permits that boundary.
    return _ProjectedRequest(
        "path",
        "Review external path access.",
        False,
        {"intent": intent[:128], "outsideWorkspace": True},
    )


class PermissionApprovalBroker:
    """One bounded approval authority for one resolved Workspace."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        max_pending: int = DEFAULT_MAX_PENDING,
        tombstone_limit: int = DEFAULT_TOMBSTONE_LIMIT,
        tombstone_ttl_seconds: float = DEFAULT_TOMBSTONE_TTL_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_pending, bool) or not isinstance(max_pending, int) or max_pending < 1:
            raise ValueError("max_pending must be positive")
        if (
            isinstance(tombstone_limit, bool)
            or not isinstance(tombstone_limit, int)
            or tombstone_limit < 1
        ):
            raise ValueError("tombstone_limit must be positive")
        if (
            isinstance(tombstone_ttl_seconds, bool)
            or not isinstance(tombstone_ttl_seconds, (int, float))
            or tombstone_ttl_seconds <= 0
        ):
            raise ValueError("tombstone_ttl_seconds must be positive")
        if (
            isinstance(poll_interval, bool)
            or not isinstance(poll_interval, (int, float))
            or poll_interval <= 0
        ):
            raise ValueError("poll_interval must be positive")
        self.workspace = Path(workspace).expanduser().resolve()
        self._timeout = float(timeout_seconds)
        self._max_pending = max_pending
        self._tombstone_limit = tombstone_limit
        self._tombstone_ttl = float(tombstone_ttl_seconds)
        self._poll_interval = float(poll_interval)
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._lock = threading.RLock()
        self._records: dict[str, _ApprovalRequest] = {}
        self._sessions: dict[str, "PermissionApprovalSession"] = {}
        self._closed = False
        self._revision = f"permissionrev_{uuid.uuid4().hex}"

    def _touch_revision(self) -> None:
        self._revision = f"permissionrev_{uuid.uuid4().hex}"

    def _cleanup_locked(self, now: float) -> None:
        terminal = sorted(
            (
                record
                for record in self._records.values()
                if record.status != "pending" and record.terminal_at is not None
            ),
            key=lambda item: (item.terminal_at or 0.0, item.permission_id),
        )
        expired_ids = {
            record.permission_id
            for record in terminal
            if now - (record.terminal_at or now) >= self._tombstone_ttl
        }
        survivors = [record for record in terminal if record.permission_id not in expired_ids]
        if len(survivors) > self._tombstone_limit:
            expired_ids.update(
                record.permission_id
                for record in survivors[: len(survivors) - self._tombstone_limit]
            )
        for permission_id in expired_ids:
            self._records.pop(permission_id, None)
        if expired_ids:
            self._touch_revision()

    @staticmethod
    def _emit(record: _ApprovalRequest, event_type: str, payload: dict[str, object]) -> None:
        session = getattr(record, "_session", None)
        emit = getattr(session, "_emit", None)
        normalized = normalize_permission_event_payload(event_type, payload)
        if emit is None or normalized is None:
            return
        try:
            emit(event_type, normalized)
        except BaseException:  # noqa: BLE001 - observation is optional
            pass

    def _transition_locked(
        self,
        record: _ApprovalRequest,
        status: PermissionStatus,
        *,
        decision: WebPermissionDecision | None = None,
    ) -> bool:
        if record.status != "pending":
            return False
        record.status = status
        record.decision = decision
        record.updated_at = _iso_time(self._wall_clock())
        record.terminal_at = self._monotonic()
        record.review = {}
        record.summary = ""
        record.choices = []
        self._touch_revision()
        record.wake.set()
        self._emit(
            record,
            "permission.decided",
            {
                "permissionVersion": 1,
                "permissionId": record.permission_id,
                "decisionKind": status,
            },
        )
        record._session = None
        record.session_key = ""
        record.run_id = None
        record.tool_name = "unknown"
        record.kind = ""
        self._cleanup_locked(record.terminal_at)
        return True

    def begin_turn(
        self,
        *,
        turn_id: str,
        run_id: str | None,
        cancellation_token: TurnCancellationToken,
        event_sink: Callable[[str, dict[str, object]], None] | None = None,
    ) -> "PermissionApprovalSession":
        if not isinstance(turn_id, str) or TURN_ID_RE.fullmatch(turn_id) is None:
            raise ValueError("invalid turn id")
        if run_id is not None and (
            not isinstance(run_id, str) or RUN_ID_RE.fullmatch(run_id) is None
        ):
            raise ValueError("invalid run id")
        if not isinstance(cancellation_token, TurnCancellationToken):
            raise TypeError("cancellation_token is required")
        if cancellation_token.turn_id != turn_id:
            raise ValueError("cancellation token mismatch")
        with self._lock:
            if self._closed:
                raise PermissionApprovalError("permission_unavailable")
            session = PermissionApprovalSession(
                broker=self,
                session_key=uuid.uuid4().hex,
                turn_id=turn_id,
                run_id=run_id,
                cancellation_token=cancellation_token,
                event_sink=event_sink,
            )
            self._sessions[session._session_key] = session
            return session

    def revision(self) -> str:
        with self._lock:
            return self._revision

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            now = self._monotonic()
            for record in list(self._records.values()):
                if record.status == "pending" and now >= record.deadline:
                    self._transition_locked(record, "expired")
            self._cleanup_locked(now)
            items = sorted(
                (
                    record.public_item()
                    for record in self._records.values()
                    if record.status == "pending"
                ),
                key=lambda item: (str(item["createdAt"]), str(item["permissionId"])),
            )
            payload: dict[str, object] = {
                "schemaVersion": 1,
                "generatedAt": _iso_time(self._wall_clock()),
                "mode": "read-only",
                "source": "gateway-permission-broker",
                "revision": self._revision,
                "items": items,
            }
            if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
                raise PermissionApprovalError("permission_unavailable")
            return payload

    def decide(
        self,
        *,
        permission_id: str,
        turn_id: str,
        decision: str,
    ) -> PermissionDecisionResult:
        if not isinstance(permission_id, str) or PERMISSION_ID_RE.fullmatch(permission_id) is None:
            raise PermissionApprovalError("invalid_request")
        if not isinstance(turn_id, str) or TURN_ID_RE.fullmatch(turn_id) is None:
            raise PermissionApprovalError("invalid_request")
        if decision not in {"allow_once", "deny_once"}:
            raise PermissionApprovalError("invalid_request")
        with self._lock:
            if self._closed:
                raise PermissionApprovalError("permission_unavailable")
            now = self._monotonic()
            self._cleanup_locked(now)
            record = self._records.get(permission_id)
            if record is None:
                raise PermissionApprovalError("permission_not_found")
            if record.turn_id != turn_id:
                raise PermissionApprovalError("permission_turn_mismatch")
            if record.status == "pending" and now >= record.deadline:
                self._transition_locked(record, "expired")
            if record.status == "pending":
                if decision == "allow_once" and not record.reviewable:
                    raise PermissionApprovalError("permission_not_reviewable")
                status: PermissionStatus = (
                    "allowed" if decision == "allow_once" else "denied"
                )
                self._transition_locked(record, status, decision=decision)
                return PermissionDecisionResult(
                    record.permission_id,
                    record.turn_id,
                    record.status,
                    decision,
                    True,
                    record.updated_at,
                )
            if record.status in {"allowed", "denied"}:
                if record.decision == decision:
                    return PermissionDecisionResult(
                        record.permission_id,
                        record.turn_id,
                        record.status,
                        decision,
                        False,
                        record.updated_at,
                    )
                raise PermissionApprovalError("permission_already_decided")
            error_for_state = {
                "expired": "permission_expired",
                "cancelled": "permission_cancelled",
                "closed": "permission_unavailable",
            }
            raise PermissionApprovalError(error_for_state[record.status])

    def cancel_turn(self, turn_id: str) -> None:
        if not isinstance(turn_id, str) or TURN_ID_RE.fullmatch(turn_id) is None:
            return
        with self._lock:
            for record in list(self._records.values()):
                if record.turn_id == turn_id:
                    self._transition_locked(record, "cancelled")

    def _close_session(self, session: "PermissionApprovalSession") -> None:
        with self._lock:
            self._sessions.pop(session._session_key, None)
            for record in list(self._records.values()):
                if record.session_key == session._session_key:
                    self._transition_locked(record, "closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for record in list(self._records.values()):
                self._transition_locked(record, "closed")
            self._sessions.clear()


class PermissionApprovalSession:
    """One Turn-scoped prompt and Tool-operation context adapter."""

    def __init__(
        self,
        *,
        broker: PermissionApprovalBroker,
        session_key: str,
        turn_id: str,
        run_id: str | None,
        cancellation_token: TurnCancellationToken,
        event_sink: Callable[[str, dict[str, object]], None] | None,
    ) -> None:
        self._broker = broker
        self._session_key = session_key
        self.turn_id = turn_id
        self.run_id = run_id
        self._cancellation_token = cancellation_token
        self._event_sink = event_sink
        self._closed = False
        self._state_lock = threading.Lock()
        self._local = threading.local()

    def _emit(self, event_type: str, payload: dict[str, object]) -> None:
        if self._event_sink is not None:
            self._event_sink(event_type, payload)

    def tool_started(self, tool_name: str) -> str:
        operation_id = f"permissiontool_{uuid.uuid4().hex}"
        token = _CURRENT_TOOL.set(
            _ToolBinding(self._session_key, operation_id, _safe_tool_name(tool_name))
        )
        stack = getattr(self._local, "tokens", None)
        if stack is None:
            stack = []
            self._local.tokens = stack
        stack.append(token)
        return operation_id

    def tool_finished(self, _tool_name: str) -> None:
        stack: list[Token[_ToolBinding | None]] = getattr(
            self._local, "tokens", []
        )
        if not stack:
            return
        token = stack.pop()
        try:
            _CURRENT_TOOL.reset(token)
        except (RuntimeError, ValueError):
            _CURRENT_TOOL.set(None)

    def check_operation(self) -> None:
        self._cancellation_token.raise_if_requested()
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Permission approval session is closed.")

    def prompt(self, request: dict[str, Any]) -> dict[str, str]:
        self._cancellation_token.raise_if_requested()
        with self._state_lock:
            if self._closed:
                return {"decision": "deny_operation"}
        binding = _CURRENT_TOOL.get()
        if binding is None or binding.session_key != self._session_key:
            binding = _ToolBinding(
                self._session_key,
                f"permissiontool_{uuid.uuid4().hex}",
                "unknown",
            )
        projected = _project_request(self._broker.workspace, request)
        now_mono = self._broker._monotonic()
        now_wall = self._broker._wall_clock()
        record = _ApprovalRequest(
            permission_id=f"permission_{uuid.uuid4().hex}",
            session_key=self._session_key,
            turn_id=self.turn_id,
            run_id=self.run_id,
            tool_operation_id=binding.operation_id,
            tool_name=binding.tool_name,
            kind=projected.kind,
            summary=projected.summary,
            reviewable=projected.reviewable,
            review=projected.review,
            choices=(
                ["allow_once", "deny_once"]
                if projected.reviewable
                else ["deny_once"]
            ),
            created_at=_iso_time(now_wall),
            expires_at=_iso_time(
                now_wall + timedelta(seconds=self._broker._timeout)
            ),
            deadline=now_mono + self._broker._timeout,
            updated_at=_iso_time(now_wall),
        )
        # A private back-reference exists only while the request is live and is
        # never serialized. It lets the broker keep event failures isolated.
        record._session = self
        with self._broker._lock:
            if self._broker._closed or self._closed:
                return {"decision": "deny_operation"}
            self._broker._cleanup_locked(now_mono)
            pending = sum(
                item.status == "pending" for item in self._broker._records.values()
            )
            candidate_bytes = len(
                json.dumps(record.public_item(), ensure_ascii=False).encode("utf-8")
            )
            current_bytes = sum(
                len(json.dumps(item.public_item(), ensure_ascii=False).encode("utf-8"))
                for item in self._broker._records.values()
                if item.status == "pending"
            )
            if (
                pending >= self._broker._max_pending
                or current_bytes + candidate_bytes > MAX_SNAPSHOT_BYTES - 2_048
            ):
                self._emit(
                    "permission.requested",
                    {
                        "permissionVersion": 1,
                        "permissionId": record.permission_id,
                        "kind": record.kind,
                        "toolName": record.tool_name,
                        "toolOperationId": record.tool_operation_id,
                        "reviewable": False,
                    },
                )
                self._emit(
                    "permission.decided",
                    {
                        "permissionVersion": 1,
                        "permissionId": record.permission_id,
                        "decisionKind": "unavailable",
                    },
                )
                result = {"decision": "deny_operation"}
                if projected.kind == "network":
                    result["reason"] = "permission_unavailable"
                return result
            self._emit(
                "permission.requested",
                {
                    "permissionVersion": 1,
                    "permissionId": record.permission_id,
                    "kind": record.kind,
                    "toolName": record.tool_name,
                    "toolOperationId": record.tool_operation_id,
                    "reviewable": record.reviewable,
                },
            )
            self._broker._records[record.permission_id] = record
            self._broker._touch_revision()

        while True:
            with self._broker._lock:
                if record.status == "pending":
                    if self._cancellation_token.is_requested():
                        self._broker._transition_locked(record, "cancelled")
                    elif self._broker._closed or self._closed:
                        self._broker._transition_locked(record, "closed")
                    elif self._broker._monotonic() >= record.deadline:
                        self._broker._transition_locked(record, "expired")
                status = record.status
                remaining = max(0.0, record.deadline - self._broker._monotonic())
            if status != "pending":
                break
            record.wake.wait(timeout=min(self._broker._poll_interval, remaining))

        if status == "cancelled":
            raise TurnCancellationRequested(self.turn_id)
        if status == "allowed":
            self.check_operation()
            return {"decision": "allow_operation"}
        result = {"decision": "deny_operation"}
        if projected.kind == "network":
            result["reason"] = {
                "denied": "permission_denied",
                "expired": "permission_expired",
                "closed": "permission_unavailable",
            }.get(status, "permission_unavailable")
        return result

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        stack: list[Token[_ToolBinding | None]] = getattr(
            self._local, "tokens", []
        )
        while stack:
            token = stack.pop()
            try:
                _CURRENT_TOOL.reset(token)
            except (RuntimeError, ValueError):
                _CURRENT_TOOL.set(None)
        self._broker._close_session(self)


__all__ = [
    "DEFAULT_APPROVAL_TIMEOUT_SECONDS",
    "DEFAULT_MAX_PENDING",
    "MAX_COMMAND_PREVIEW_BYTES",
    "MAX_DIFF_PREVIEW_BYTES",
    "MAX_REVIEW_BYTES",
    "MAX_SNAPSHOT_BYTES",
    "PERMISSION_ID_RE",
    "PermissionApprovalBroker",
    "PermissionApprovalError",
    "PermissionApprovalSession",
    "PermissionDecisionResult",
    "RUN_ID_RE",
    "TOOL_OPERATION_ID_RE",
    "TURN_ID_RE",
    "is_loopback_gateway_host",
]
