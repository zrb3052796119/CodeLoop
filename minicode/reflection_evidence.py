"""Deterministic evidence extraction for post-task reflection.

This module treats execution traces as untrusted, bounded data.  It never
executes trace content and exposes one extraction interface used by both the
reflection engine and its evaluator.
"""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse


EpistemicStatus = Literal["confirmed", "inferred", "unknown"]

TRACE_SCHEMA_VERSION = 2
TRACE_MAX_EVENTS = 500
EVIDENCE_MAX_TEXT_CHARS = 600
EVIDENCE_MAX_LIST_ITEMS = 64
EVIDENCE_MAX_DEPTH = 5

PATH_KEYS = {
    "path",
    "file_path",
    "filepath",
    "paths",
    "files",
    "files_read",
    "files_changed",
    "changed_files",
    "referenced_files",
}

_READ_TOOLS = {
    "read_file",
    "grep_files",
    "search_files",
    "find_symbols",
    "find_references",
    "get_ast_info",
    "code_review",
    "diff_viewer",
    "file_line_count",
    "format_file",  # Legacy traces expose only a generic files field.
}
_CHANGE_TOOLS = {
    "write_file",
    "modify_file",
    "edit_file",
    "patch_file",
    "create_file",
    "delete_file",
    "move_file",
    "batch_copy",
    "batch_move",
    "batch_delete",
}
_COMMAND_TOOLS = {
    "run_command",
    "execute_command",
    "test_runner",
    "compile",
    "build",
    "pytest",
    "unittest",
    "ruff",
    "pyright",
    "mypy",
}
_MANIFEST_NAMES = {
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "cargo.toml",
    "go.mod",
}
_IMPORT_ALIASES = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "pil": "pillow",
    "yaml": "pyyaml",
}
_STANDARD_LIBRARY_IMPORTS = {
    "argparse",
    "collections",
    "dataclasses",
    "datetime",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "re",
    "shlex",
    "sys",
    "time",
    "typing",
    "urllib",
}
_MENTION_LIBRARIES = {
    "angular",
    "django",
    "fastapi",
    "flask",
    "gin",
    "jest",
    "next",
    "nuxt",
    "pytest",
    "react",
    "redux",
    "ruff",
    "svelte",
    "tailwind",
    "uvicorn",
    "vitest",
    "vue",
    "zod",
}

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b((?:[a-z][a-z0-9]*[_-])*(?:api[_-]?key|authorization|credential|password|token|secret(?:[_-]?key)?))\b"
    r"(\s*[:=]\s*)(?!\[redacted)[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+(?!\[redacted)[a-z0-9._~+/-]+")
_OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_ERROR_TYPE_RE = re.compile(r"(?:^|[\[\s])([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))[]:]?")


def append_trace_event(
    trace: list[dict[str, Any]],
    event: dict[str, Any],
    *,
    max_events: int = TRACE_MAX_EVENTS,
) -> bool:
    """Append one Trace Contract v2 event, returning whether it was accepted."""
    if len(trace) >= max_events:
        return False
    existing_ids = {
        str(existing.get("event_id"))
        for existing in trace
        if existing.get("event_id")
    }
    normalized = dict(event)
    supplied_id = str(normalized.get("event_id", "")).strip()
    if supplied_id:
        if supplied_id in existing_ids:
            raise ValueError(f"duplicate trace event_id: {supplied_id}")
        event_id = supplied_id
    else:
        sequence = len(trace) + 1
        event_id = f"event-{sequence:06d}"
        while event_id in existing_ids:
            sequence += 1
            event_id = f"event-{sequence:06d}"
    normalized["trace_schema_version"] = TRACE_SCHEMA_VERSION
    normalized["event_id"] = event_id
    trace.append(normalized)
    return True


def sanitize_evidence_text(value: Any, limit: int = EVIDENCE_MAX_TEXT_CHARS) -> str:
    """Safely stringify, redact, and bound untrusted trace text."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        text = "[unprintable]"
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )
    text = _OPENAI_STYLE_KEY_RE.sub("[REDACTED_API_KEY]", text)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


@dataclass(frozen=True)
class FileEvidence:
    path: str
    role: Literal["read", "changed", "referenced"]
    event_ids: tuple[str, ...]
    call_id: str | None = None
    epistemic_status: EpistemicStatus = "confirmed"


@dataclass(frozen=True)
class ToolEvidence:
    tool_name: str
    call_id: str | None
    call_event_id: str
    result_event_ids: tuple[str, ...]
    status: Literal["success", "failed", "unknown"]


@dataclass(frozen=True)
class LibraryEvidence:
    name: str
    status: Literal["confirmed", "weak_mention"]
    event_ids: tuple[str, ...]
    import_name: str | None = None
    epistemic_status: EpistemicStatus = "confirmed"


@dataclass(frozen=True)
class ErrorEvidence:
    error_id: str
    call_id: str | None
    tool_name: str | None
    error_type: str | None
    message: str
    source_event_ids: tuple[str, ...]
    epistemic_status: EpistemicStatus = "confirmed"


@dataclass(frozen=True)
class RecoveryEvidence:
    recovery_id: str
    related_error_ids: tuple[str, ...]
    action: str
    event_ids: tuple[str, ...]
    files_changed: tuple[str, ...]
    epistemic_status: EpistemicStatus


@dataclass(frozen=True)
class RecoverySuggestionEvidence:
    suggestion_id: str
    related_error_ids: tuple[str, ...]
    suggestion: str
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class DecisionEvidence:
    decision_id: str
    statement: str
    rationale: str | None
    event_ids: tuple[str, ...]
    epistemic_status: EpistemicStatus
    source_kind: Literal[
        "assistant_decision",
        "user_constraint",
        "user_correction",
        "config_constraint",
        "old_memory_disproved",
        "inferred_rationale",
    ] = "assistant_decision"


@dataclass(frozen=True)
class VerificationEvidence:
    verification_id: str
    tool_name: str | None
    call_id: str | None
    command_kind: str | None
    scope: Literal["targeted", "full", "unknown"]
    result: Literal["passed", "failed", "unknown"]
    event_ids: tuple[str, ...]
    summary: str = ""


@dataclass
class TaskEvidence:
    files_read: list[FileEvidence] = field(default_factory=list)
    files_changed: list[FileEvidence] = field(default_factory=list)
    referenced_files: list[FileEvidence] = field(default_factory=list)
    tool_calls: list[ToolEvidence] = field(default_factory=list)
    libraries: list[LibraryEvidence] = field(default_factory=list)
    errors: list[ErrorEvidence] = field(default_factory=list)
    recoveries: list[RecoveryEvidence] = field(default_factory=list)
    recovery_suggestions: list[RecoverySuggestionEvidence] = field(default_factory=list)
    decisions: list[DecisionEvidence] = field(default_factory=list)
    verification: list[VerificationEvidence] = field(default_factory=list)
    outcome: Literal["success", "failed", "unknown"] = "unknown"
    had_errors: bool = False
    errors_recovered: bool = False
    diagnostics: list[str] = field(default_factory=list)
    event_positions: dict[str, int] = field(default_factory=dict)

    def to_dict(self, max_items: int = EVIDENCE_MAX_LIST_ITEMS) -> dict[str, Any]:
        """Return deterministic JSON-compatible evidence metadata."""
        def bound(value: Any, depth: int = 0) -> Any:
            if depth > EVIDENCE_MAX_DEPTH:
                return "[truncated]"
            if isinstance(value, dict):
                return {
                    str(key): bound(nested, depth + 1)
                    for key, nested in list(value.items())[:max_items]
                }
            if isinstance(value, list):
                return [bound(nested, depth + 1) for nested in value[:max_items]]
            if isinstance(value, tuple):
                return tuple(bound(nested, depth + 1) for nested in value[:max_items])
            return value

        return bound(asdict(self))


@dataclass(frozen=True)
class _Event:
    index: int
    event_id: str
    event_type: str
    call_id: str | None
    tool_name: str | None
    raw: Mapping[str, Any]


def _safe_get(mapping: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return mapping.get(key, default)
    except Exception:
        return default


def _safe_sequence_length(value: Any) -> int:
    try:
        return len(value)
    except Exception:
        return 0


def _normalize_name(value: Any) -> str:
    return "_".join(sanitize_evidence_text(value, 120).strip().lower().split())


def _normalize_message(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))[:EVIDENCE_MAX_LIST_ITEMS]


def _normalize_path(path: str) -> str:
    cleaned = path.strip().strip("'\"")
    if re.match(r"^[A-Za-z]:\\", cleaned):
        return cleaned
    return cleaned.replace("\\", "/")


def _looks_like_local_file(path: str) -> bool:
    candidate = path.strip().strip("'\"")
    if not candidate or len(candidate) > 300 or "\x00" in candidate or "\n" in candidate:
        return False
    parsed = urlparse(candidate)
    if parsed.scheme.lower() in {"http", "https", "ftp", "data"}:
        return False
    if candidate.startswith("-") or _ENV_ASSIGNMENT_RE.match(candidate):
        return False
    if any(token in candidate for token in (" && ", " || ", " | ", "; ")):
        return False
    without_selector = candidate.split("::", 1)[0]
    basename = re.split(r"[/\\]", without_selector)[-1].lower()
    if basename in _MANIFEST_NAMES or basename in {"dockerfile", "makefile", "license"}:
        return True
    if re.match(r"^[A-Za-z]:[\\/]", without_selector):
        return "." in basename
    return bool(("/" in without_selector or "." in basename) and re.search(r"\.[A-Za-z0-9]{1,12}$", basename))


def _path_values(value: Any, max_items: int = EVIDENCE_MAX_LIST_ITEMS) -> list[str]:
    found: list[str] = []
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen: set[int] = set()
    while stack and len(found) < max_items:
        current, depth = stack.pop()
        if depth > EVIDENCE_MAX_DEPTH:
            continue
        if isinstance(current, str):
            if _looks_like_local_file(current):
                found.append(_normalize_path(current))
            continue
        if isinstance(current, (Mapping, list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
        if isinstance(current, Mapping):
            try:
                nested_values = list(current.values())[:max_items]
            except Exception:
                continue
            stack.extend((nested, depth + 1) for nested in reversed(nested_values))
        elif isinstance(current, (list, tuple)):
            stack.extend((nested, depth + 1) for nested in reversed(current[:max_items]))
    return list(dict.fromkeys(found))


def _find_key_values(value: Any, keys: set[str]) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = defaultdict(list)
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen: set[int] = set()
    visited = 0
    while stack and visited < EVIDENCE_MAX_LIST_ITEMS * EVIDENCE_MAX_DEPTH * 4:
        current, depth = stack.pop()
        visited += 1
        if depth > EVIDENCE_MAX_DEPTH:
            continue
        if isinstance(current, (Mapping, list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
        if isinstance(current, Mapping):
            try:
                items = list(current.items())[:EVIDENCE_MAX_LIST_ITEMS]
            except Exception:
                continue
            for key, nested in reversed(items):
                normalized_key = _normalize_name(key)
                if normalized_key in keys:
                    found[normalized_key].append(nested)
                if normalized_key not in {"content", "output", "output_summary", "message"}:
                    if isinstance(nested, (Mapping, list, tuple)):
                        stack.append((nested, depth + 1))
        elif isinstance(current, (list, tuple)):
            stack.extend((nested, depth + 1) for nested in reversed(current[:EVIDENCE_MAX_LIST_ITEMS]))
    return found


def _command_paths(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except (TypeError, ValueError):
        return []
    paths: list[str] = []
    skip_next = False
    for token in tokens[:EVIDENCE_MAX_LIST_ITEMS * 2]:
        if skip_next:
            skip_next = False
            continue
        if token in {">", ">>", "<", "2>", "2>>"}:
            skip_next = True
            continue
        if token.startswith("-") or _ENV_ASSIGNMENT_RE.match(token):
            continue
        if urlparse(token).scheme.lower() in {"http", "https", "ftp", "data"}:
            continue
        candidate = token.split("::", 1)[0]
        if _looks_like_local_file(candidate):
            paths.append(_normalize_path(candidate))
    return list(dict.fromkeys(paths))[:EVIDENCE_MAX_LIST_ITEMS]


def extract_tool_file_roles(
    tool_name: str,
    payload: Any,
    *,
    event_type: str = "tool_call",
) -> dict[str, list[str]]:
    """Extract role-specific files from explicit fields and known tool semantics."""
    normalized_tool = _normalize_name(tool_name)
    result = {"files_read": [], "files_changed": [], "referenced_files": []}
    if not isinstance(payload, Mapping):
        return result

    explicit = _find_key_values(
        payload,
        {"files_read", "files_changed", "changed_files", "referenced_files"},
    )
    for value in explicit.get("files_read", []):
        result["files_read"].extend(_path_values(value))
    for key in ("files_changed", "changed_files"):
        for value in explicit.get(key, []):
            result["files_changed"].extend(_path_values(value))
    for value in explicit.get("referenced_files", []):
        result["referenced_files"].extend(_path_values(value))

    known_role: str | None = None
    if event_type in {"recovery", "fix", "recovery/fix"}:
        known_role = "files_changed"
    elif event_type == "error":
        known_role = "files_read"
    elif normalized_tool in _READ_TOOLS:
        known_role = "files_read"
    elif normalized_tool in _CHANGE_TOOLS:
        known_role = "files_changed"
    elif normalized_tool in _COMMAND_TOOLS:
        known_role = "referenced_files"

    if known_role:
        values = _find_key_values(payload, PATH_KEYS)
        role_keys = {
            "files_read",
            "files_changed",
            "changed_files",
            "referenced_files",
        }
        for key, nested_values in values.items():
            if key in role_keys:
                continue
            for value in nested_values:
                result[known_role].extend(_path_values(value))

    if normalized_tool in _COMMAND_TOOLS:
        commands = _find_key_values(payload, {"command"}).get("command", [])
        for command in commands:
            if isinstance(command, str):
                result["referenced_files"].extend(_command_paths(command))

    return {
        key: sorted(dict.fromkeys(values))[:EVIDENCE_MAX_LIST_ITEMS]
        for key, values in result.items()
    }


class TraceEvidenceExtractor:
    """Convert an untrusted execution trace into bounded deterministic facts."""

    def extract(
        self,
        task_description: str,
        execution_trace: list[dict[str, Any]],
    ) -> TaskEvidence:
        del task_description  # Facts come from trace events, not task wording.
        events, diagnostics = self._normalize_events(execution_trace)
        file_evidence = self._extract_files(events)
        tools = self._extract_tools(events)
        errors = self._extract_errors(events)
        verification = self._extract_verification(events)
        recoveries = self._extract_recoveries(events, errors, file_evidence)
        suggestions = self._extract_recovery_suggestions(events, errors)
        libraries = self._extract_libraries(events, file_evidence)
        decisions = self._extract_decisions(events)
        outcome = self._extract_outcome(events, verification)

        by_role: dict[str, list[FileEvidence]] = defaultdict(list)
        for item in file_evidence:
            by_role[item.role].append(item)
        had_errors = bool(errors)
        errors_recovered = bool(
            errors
            and recoveries
            and outcome == "success"
            and (verification or any(event.event_type == "task_result" for event in events))
        )
        return TaskEvidence(
            files_read=by_role["read"],
            files_changed=by_role["changed"],
            referenced_files=by_role["referenced"],
            tool_calls=tools,
            libraries=libraries,
            errors=errors,
            recoveries=recoveries,
            recovery_suggestions=suggestions,
            decisions=decisions,
            verification=verification,
            outcome=outcome,
            had_errors=had_errors,
            errors_recovered=errors_recovered,
            diagnostics=diagnostics[:EVIDENCE_MAX_LIST_ITEMS],
            event_positions={event.event_id: event.index for event in events},
        )

    def _normalize_events(
        self, execution_trace: Any
    ) -> tuple[list[_Event], list[str]]:
        events: list[_Event] = []
        diagnostics: list[str] = []
        if not isinstance(execution_trace, list):
            return events, ["execution_trace is not a list"]
        seen_ids: set[str] = set()
        tools_by_call: dict[str, str] = {}
        for index, raw in enumerate(execution_trace[:TRACE_MAX_EVENTS]):
            if not isinstance(raw, Mapping):
                diagnostics.append(f"trace[{index}] is not an object")
                continue
            supplied_id = sanitize_evidence_text(_safe_get(raw, "event_id", ""), 120).strip()
            event_id = supplied_id
            if not event_id or event_id in seen_ids:
                if event_id in seen_ids:
                    diagnostics.append(f"duplicate event_id: {event_id}")
                event_id = f"legacy-event-{index + 1:06d}"
                suffix = 1
                while event_id in seen_ids:
                    event_id = f"legacy-event-{index + 1:06d}-{suffix}"
                    suffix += 1
            seen_ids.add(event_id)
            call_value = _safe_get(raw, "call_id")
            call_id = sanitize_evidence_text(call_value, 120).strip() if call_value is not None else None
            call_id = call_id or None
            tool_value = (
                _safe_get(raw, "tool_name")
                or _safe_get(raw, "name")
                or _safe_get(raw, "toolName")
            )
            tool_name = _normalize_name(tool_value) if tool_value else None
            if call_id and tool_name:
                previous_tool = tools_by_call.setdefault(call_id, tool_name)
                if previous_tool != tool_name:
                    diagnostics.append(
                        f"conflicting tool names for call_id {call_id}: "
                        f"{previous_tool} vs {tool_name}"
                    )
            event_type = _normalize_name(_safe_get(raw, "type", ""))
            events.append(_Event(index, event_id, event_type, call_id, tool_name, raw))
        if _safe_sequence_length(execution_trace) > TRACE_MAX_EVENTS:
            diagnostics.append(f"trace truncated at {TRACE_MAX_EVENTS} events")
        return events, diagnostics

    def _extract_files(self, events: list[_Event]) -> list[FileEvidence]:
        merged: dict[tuple[str, str, str | None], dict[str, Any]] = {}
        role_names = {
            "files_read": "read",
            "files_changed": "changed",
            "referenced_files": "referenced",
        }
        for event in events:
            roles = extract_tool_file_roles(
                event.tool_name or "",
                event.raw,
                event_type=event.event_type,
            )
            for role_key, role in role_names.items():
                for path in roles[role_key]:
                    key = (role, path, event.call_id)
                    record = merged.setdefault(
                        key,
                        {"path": path, "role": role, "event_ids": [], "call_id": event.call_id},
                    )
                    record["event_ids"].append(event.event_id)
        return [
            FileEvidence(
                path=record["path"],
                role=record["role"],
                event_ids=_ordered_unique(record["event_ids"]),
                call_id=record["call_id"],
            )
            for record in merged.values()
        ][: EVIDENCE_MAX_LIST_ITEMS * 3]

    def _extract_tools(self, events: list[_Event]) -> list[ToolEvidence]:
        records: dict[str, dict[str, Any]] = {}
        for event in events:
            if not event.tool_name:
                continue
            key = f"call:{event.call_id}" if event.call_id else f"event:{event.event_id}"
            record = records.setdefault(
                key,
                {
                    "tool_name": event.tool_name,
                    "call_id": event.call_id,
                    "call_event_id": event.event_id,
                    "result_event_ids": [],
                    "statuses": [],
                },
            )
            if event.event_type == "tool_call":
                record["call_event_id"] = event.event_id
            elif event.event_type in {"tool_result", "error", "verification"}:
                record["result_event_ids"].append(event.event_id)
            status = _normalize_name(_safe_get(event.raw, "status", ""))
            if event.event_type == "error" or _safe_get(event.raw, "is_error") or status in {"error", "failed", "failure"}:
                record["statuses"].append("failed")
            elif status in {"success", "completed", "ok", "passed"}:
                record["statuses"].append("success")
        evidence: list[ToolEvidence] = []
        for record in records.values():
            statuses = record["statuses"]
            status: Literal["success", "failed", "unknown"] = "unknown"
            if statuses:
                status = statuses[-1]
            evidence.append(
                ToolEvidence(
                    tool_name=record["tool_name"],
                    call_id=record["call_id"],
                    call_event_id=record["call_event_id"],
                    result_event_ids=_ordered_unique(record["result_event_ids"]),
                    status=status,
                )
            )
        return evidence[: EVIDENCE_MAX_LIST_ITEMS]

    def _error_fields(self, event: _Event) -> tuple[str, str | None]:
        message_value = (
            _safe_get(event.raw, "message")
            or _safe_get(event.raw, "error")
            or _safe_get(event.raw, "output_summary")
            or _safe_get(event.raw, "content")
            or ""
        )
        message = sanitize_evidence_text(message_value)
        error_type_value = _safe_get(event.raw, "error_type") or _safe_get(event.raw, "type_name")
        error_type = sanitize_evidence_text(error_type_value, 120).strip() if error_type_value else None
        if not error_type:
            match = _ERROR_TYPE_RE.search(message)
            if match:
                error_type = match.group(1)
        return message, error_type

    def _error_fingerprint(self, message: str, error_type: str | None) -> str:
        normalized = _normalize_message(message)
        normalized = re.sub(r"\bline\s+\d+\b", "line <n>", normalized)
        if error_type:
            type_name = _normalize_message(error_type)
            position = normalized.rfind(type_name)
            if position >= 0:
                normalized = normalized[position:]
        return re.sub(r"[^\w./\\\-\u4e00-\u9fff]+", " ", normalized).strip()

    def _extract_errors(self, events: list[_Event]) -> list[ErrorEvidence]:
        records: dict[tuple[str, str, str], dict[str, Any]] = {}
        for event in events:
            status = _normalize_name(_safe_get(event.raw, "status", ""))
            is_failure = event.event_type == "error" or (
                event.event_type == "tool_result"
                and (_safe_get(event.raw, "is_error") or status in {"error", "failed", "failure"})
            )
            if not is_failure:
                continue
            message, error_type = self._error_fields(event)
            if not message.strip():
                continue
            fingerprint = self._error_fingerprint(message, error_type)
            identity = event.call_id or f"event:{event.event_id}"
            key = (identity, event.tool_name or "", fingerprint)
            record = records.setdefault(
                key,
                {
                    "call_id": event.call_id,
                    "tool_name": event.tool_name,
                    "error_type": error_type,
                    "message": message,
                    "sources": [],
                },
            )
            record["sources"].append(event.event_id)
            if error_type and (not record["error_type"] or event.event_type == "error"):
                record["error_type"] = error_type
            if event.event_type == "error" or len(message) > len(record["message"]):
                record["message"] = message
        return [
            ErrorEvidence(
                error_id=f"error-{index:06d}",
                call_id=record["call_id"],
                tool_name=record["tool_name"],
                error_type=record["error_type"],
                message=record["message"],
                source_event_ids=_ordered_unique(record["sources"]),
            )
            for index, record in enumerate(records.values(), start=1)
        ][:EVIDENCE_MAX_LIST_ITEMS]

    def _related_error_ids(
        self,
        event: _Event,
        errors: list[ErrorEvidence],
        event_positions: dict[str, int],
    ) -> tuple[str, ...]:
        requested_calls = _safe_get(event.raw, "related_error_call_ids", [])
        call_ids = {
            sanitize_evidence_text(value, 120).strip()
            for value in requested_calls[:EVIDENCE_MAX_LIST_ITEMS]
        } if isinstance(requested_calls, list) else set()
        if event.event_type == "recovery_suggestion" and event.call_id:
            call_ids.add(event.call_id)
        linked = [error.error_id for error in errors if error.call_id in call_ids]
        if linked:
            return tuple(linked)
        prior = [
            error
            for error in errors
            if max((event_positions.get(source, -1) for source in error.source_event_ids), default=-1)
            < event.index
        ]
        return (prior[-1].error_id,) if prior else ()

    def _extract_recoveries(
        self,
        events: list[_Event],
        errors: list[ErrorEvidence],
        files: list[FileEvidence],
    ) -> list[RecoveryEvidence]:
        event_positions = {event.event_id: event.index for event in events}
        result_events_by_call: dict[str, list[str]] = defaultdict(list)
        for event in events:
            if event.call_id and event.event_type == "tool_result":
                result_events_by_call[event.call_id].append(event.event_id)
        recoveries: list[RecoveryEvidence] = []
        for event in events:
            if event.event_type not in {"recovery", "fix", "recovery/fix"}:
                continue
            action_value = (
                _safe_get(event.raw, "action")
                or _safe_get(event.raw, "message")
                or _safe_get(event.raw, "content")
                or _safe_get(event.raw, "summary")
                or ""
            )
            action = sanitize_evidence_text(action_value)
            if not action.strip():
                continue
            changed = [
                item.path
                for item in files
                if item.role == "changed"
                and (event.event_id in item.event_ids or (event.call_id and item.call_id == event.call_id))
            ]
            status_value = _normalize_name(_safe_get(event.raw, "epistemic_status", "confirmed"))
            status: EpistemicStatus = status_value if status_value in {"confirmed", "inferred", "unknown"} else "unknown"  # type: ignore[assignment]
            event_ids = [event.event_id]
            if event.call_id:
                event_ids.extend(result_events_by_call[event.call_id])
            recoveries.append(
                RecoveryEvidence(
                    recovery_id=f"recovery-{len(recoveries) + 1:06d}",
                    related_error_ids=self._related_error_ids(event, errors, event_positions),
                    action=action,
                    event_ids=_ordered_unique(event_ids),
                    files_changed=tuple(dict.fromkeys(changed)),
                    epistemic_status=status,
                )
            )
        return recoveries[:EVIDENCE_MAX_LIST_ITEMS]

    def _extract_recovery_suggestions(
        self, events: list[_Event], errors: list[ErrorEvidence]
    ) -> list[RecoverySuggestionEvidence]:
        positions = {event.event_id: event.index for event in events}
        suggestions: list[RecoverySuggestionEvidence] = []
        for event in events:
            if event.event_type != "recovery_suggestion":
                continue
            text_value = _safe_get(event.raw, "suggestion") or _safe_get(event.raw, "message") or ""
            suggestion = sanitize_evidence_text(text_value)
            if not suggestion.strip():
                continue
            suggestions.append(
                RecoverySuggestionEvidence(
                    suggestion_id=f"suggestion-{len(suggestions) + 1:06d}",
                    related_error_ids=self._related_error_ids(event, errors, positions),
                    suggestion=suggestion,
                    event_ids=(event.event_id,),
                )
            )
        return suggestions[:EVIDENCE_MAX_LIST_ITEMS]

    def _event_text(self, event: _Event) -> str:
        value = (
            _safe_get(event.raw, "output_summary")
            or _safe_get(event.raw, "message")
            or _safe_get(event.raw, "content")
            or _safe_get(event.raw, "summary")
            or ""
        )
        return sanitize_evidence_text(value)

    def _call_event_ids(self, events: list[_Event]) -> dict[str, tuple[str, ...]]:
        by_call: dict[str, list[str]] = defaultdict(list)
        for event in events:
            if event.call_id:
                by_call[event.call_id].append(event.event_id)
        return {call_id: _ordered_unique(ids) for call_id, ids in by_call.items()}

    def _extract_libraries(
        self, events: list[_Event], files: list[FileEvidence]
    ) -> list[LibraryEvidence]:
        records: dict[str, dict[str, Any]] = {}
        call_ids = self._call_event_ids(events)
        paths_by_call: dict[str, set[str]] = defaultdict(set)
        for file in files:
            if file.call_id:
                paths_by_call[file.call_id].add(file.path.lower())

        def add(
            name: str,
            status: Literal["confirmed", "weak_mention"],
            evidence_ids: Sequence[str],
            import_name: str | None = None,
        ) -> None:
            normalized = name.strip().lower().replace("_", "-")
            if not normalized or len(normalized) > 100:
                return
            existing = records.get(normalized)
            if existing is None:
                records[normalized] = {
                    "name": normalized,
                    "status": status,
                    "event_ids": list(evidence_ids),
                    "import_name": import_name,
                }
                return
            existing["event_ids"].extend(evidence_ids)
            if status == "confirmed":
                existing["status"] = "confirmed"
            if import_name:
                existing["import_name"] = import_name

        for event in events:
            text = self._event_text(event)
            lowered = text.lower()
            evidence_ids = call_ids.get(event.call_id, (event.event_id,)) if event.call_id else (event.event_id,)
            structured = _safe_get(event.raw, "structured_result")
            if isinstance(structured, Mapping):
                for key in ("dependencies", "devDependencies", "optionalDependencies"):
                    values = _safe_get(structured, key)
                    if isinstance(values, Mapping):
                        try:
                            names = list(values.keys())[:EVIDENCE_MAX_LIST_ITEMS]
                        except Exception:
                            names = []
                        for name in names:
                            add(sanitize_evidence_text(name, 100), "confirmed", (event.event_id,))
                installed = _safe_get(structured, "installed")
                if isinstance(installed, list):
                    for name in installed[:EVIDENCE_MAX_LIST_ITEMS]:
                        add(sanitize_evidence_text(name, 100), "confirmed", evidence_ids)

            manifest_paths = paths_by_call.get(event.call_id or "", set())
            basenames = {re.split(r"[/\\]", path)[-1] for path in manifest_paths}
            if event.event_type == "tool_result" and basenames & _MANIFEST_NAMES:
                if any(name.startswith("requirements") for name in basenames):
                    for line in text.splitlines()[:EVIDENCE_MAX_LIST_ITEMS]:
                        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(?:\[|[<>=!~]|$)", line)
                        if match:
                            add(match.group(1), "confirmed", (event.event_id,))
                if "pyproject.toml" in basenames:
                    dependency_blocks = re.findall(r"(?i)dependencies\s*=\s*\[([^\]]*)\]", text)
                    for block in dependency_blocks:
                        for name in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", block):
                            if name.lower() not in {"python"}:
                                add(name, "confirmed", (event.event_id,))

            if event.event_type == "tool_result":
                for match in re.finditer(r"(?m)^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", text):
                    import_name = match.group(1).split(".", 1)[0].lower()
                    if import_name not in _STANDARD_LIBRARY_IMPORTS:
                        add(
                            _IMPORT_ALIASES.get(import_name, import_name),
                            "confirmed",
                            (event.event_id,),
                            import_name if import_name in _IMPORT_ALIASES else None,
                        )
                for match in re.finditer(r"(?m)\bfrom\s+['\"]([^'\"]+)['\"]", text):
                    package = match.group(1).split("/", 1)[0]
                    if package and not package.startswith("."):
                        add(package, "confirmed", (event.event_id,))

            commands = _find_key_values(event.raw, {"command"}).get("command", [])
            for command in commands:
                if not isinstance(command, str):
                    continue
                try:
                    tokens = shlex.split(command)
                except ValueError:
                    continue
                lowered_tokens = [token.lower() for token in tokens]
                install_index = next(
                    (index for index, token in enumerate(lowered_tokens) if token in {"install", "add"}),
                    None,
                )
                if install_index is not None:
                    for token in tokens[install_index + 1 : install_index + 1 + EVIDENCE_MAX_LIST_ITEMS]:
                        if token.startswith("-") or _looks_like_local_file(token):
                            continue
                        package = re.split(r"[<>=!~@\[]", token, maxsplit=1)[0]
                        if re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", package):
                            add(package, "confirmed", evidence_ids)
                if lowered_tokens[:2] == ["ruff", "format"] or lowered_tokens[:2] == ["ruff", "check"]:
                    add("ruff", "confirmed", evidence_ids)

            if event.event_type == "existing_memory" and re.search(r"(?i)\bruff\s+format\b", text):
                add("ruff", "confirmed", (event.event_id,))

            if event.event_type in {"assistant", "assistant_step"}:
                for name in sorted(_MENTION_LIBRARIES):
                    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])"
                    for match in re.finditer(pattern, lowered):
                        prefix = lowered[max(0, match.start() - 32) : match.start()]
                        local_path = re.search(r"(?:^|[\s'\"])(?:src|tests?|lib)/$", prefix)
                        negated = re.search(
                            r"(?:do\s+not|does\s+not|did\s+not|don't|doesn't|not)\s+(?:add\s+)?$",
                            prefix,
                        )
                        if not local_path and not negated:
                            add(name, "weak_mention", (event.event_id,))
                        break

        return [
            LibraryEvidence(
                name=record["name"],
                status=record["status"],
                event_ids=_ordered_unique(record["event_ids"]),
                import_name=record["import_name"],
            )
            for record in records.values()
        ][:EVIDENCE_MAX_LIST_ITEMS]

    def _verification_kind(self, tool_name: str, command: str, output: str) -> str | None:
        tool = _normalize_name(tool_name)
        command_lower = command.lower().strip()
        output_lower = output.lower()
        if tool in {"pytest", "unittest", "test_runner"}:
            return "test"
        if tool in {"ruff", "eslint", "flake8", "pylint"}:
            return "lint"
        if tool in {"pyright", "mypy", "tsc"}:
            return "type_check"
        if tool in {"compile"}:
            return "compile"
        if tool in {"build"}:
            return "build"
        try:
            command_tokens = shlex.split(command_lower)
        except ValueError:
            command_tokens = []
        if (
            command_tokens[:1] in (["pytest"], ["unittest"])
            or command_tokens[:3] in (["python", "-m", "pytest"], ["python", "-m", "unittest"])
            or command_tokens[:2] in (["npm", "test"], ["cargo", "test"], ["go", "test"])
        ):
            return "test"
        if re.search(r"\b(?:tests?|fixtures|passed)\b", output_lower) or any(
            marker in output for marker in ("测试通过", "测试失败", "测试未通过")
        ):
            return "test"
        if re.search(r"(?:^|\s)(?:ruff|eslint)\s+check(?:\s|$)", command_lower) or re.search(
            r"\blint(?:ed|ing)?\b", output_lower
        ):
            return "lint"
        if re.search(r"\b(?:pyright|mypy|type[ -]?check)\b", f"{command_lower} {output_lower}"):
            return "type_check"
        if re.search(r"\b(?:compile|compileall)\b", f"{command_lower} {output_lower}"):
            return "compile"
        if re.search(r"\bbuild\b", f"{command_lower} {output_lower}"):
            return "build"
        return None

    def _verification_result(self, status: str, output: str) -> Literal["passed", "failed", "unknown"]:
        lowered = output.lower()
        if status in {"error", "failed", "failure"}:
            return "failed"
        if re.search(r"\b[1-9]\d*\s+(?:\w+\s+)?failed\b", lowered) or "测试失败" in output or "测试未通过" in output:
            return "failed"
        if re.search(r"\b(?:passed|succeeded|successful|success|no errors?)\b", lowered) or "测试通过" in output:
            return "passed"
        if status in {"success", "completed", "ok", "passed"}:
            return "passed"
        return "unknown"

    def _verification_scope(self, command: str, output: str) -> Literal["targeted", "full", "unknown"]:
        lowered = f"{command} {output}".lower()
        if "full suite" in lowered or re.search(r"(?:^|\s)pytest\s+-q(?:\s|$)", command.lower()):
            return "full"
        if _command_paths(command) or re.search(
            r"\b(?:targeted|focused|service tests?|memory tests?|consistency tests?|compatibility tests?|security fixtures?|cache consistency)\b",
            lowered,
        ) or "一致性测试" in output:
            return "targeted"
        return "unknown"

    def _extract_verification(self, events: list[_Event]) -> list[VerificationEvidence]:
        call_events_by_id: dict[str, list[_Event]] = defaultdict(list)
        recovery_calls: set[str] = set()
        for event in events:
            if event.call_id:
                if event.event_type == "tool_call":
                    call_events_by_id[event.call_id].append(event)
                if event.event_type in {"recovery", "fix", "recovery/fix"}:
                    recovery_calls.add(event.call_id)
        verification: list[VerificationEvidence] = []
        failed_tools_seen: dict[str, int] = defaultdict(int)
        for event in events:
            if event.event_type not in {"tool_result", "verification"}:
                continue
            call_events = call_events_by_id.get(event.call_id or "", [])
            command_values: list[str] = []
            for candidate in call_events + [event]:
                for command in _find_key_values(candidate.raw, {"command"}).get("command", []):
                    if isinstance(command, str):
                        command_values.append(sanitize_evidence_text(command))
            command = command_values[0] if command_values else ""
            output = self._event_text(event)
            status = _normalize_name(_safe_get(event.raw, "status", ""))
            kind = _normalize_name(_safe_get(event.raw, "command_kind", "")) or self._verification_kind(
                event.tool_name or (call_events[0].tool_name if call_events else ""),
                command,
                output,
            )
            if not kind and event.call_id in recovery_calls and status in {"error", "failed", "failure"}:
                kind = "recovery_check"
            if (
                not kind
                and status in {"error", "failed", "failure"}
                and event.tool_name
                and failed_tools_seen[event.tool_name] > 0
            ):
                kind = "retry_check"
            if status in {"error", "failed", "failure"} and event.tool_name:
                failed_tools_seen[event.tool_name] += 1
            if not kind:
                continue
            event_ids = [candidate.event_id for candidate in call_events]
            event_ids.append(event.event_id)
            verification.append(
                VerificationEvidence(
                    verification_id=f"verify-{len(verification) + 1:06d}",
                    tool_name=event.tool_name or (call_events[0].tool_name if call_events else None),
                    call_id=event.call_id,
                    command_kind=kind,
                    scope=self._verification_scope(command, output),
                    result=self._verification_result(status, output),
                    event_ids=_ordered_unique(event_ids),
                    summary=output,
                )
            )
        return verification[:EVIDENCE_MAX_LIST_ITEMS]

    def _decision_tokens(self, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[A-Za-z0-9_.()]+|[\u4e00-\u9fff]{2,}", text.lower())
            if len(token) > 2 and token not in {"because", "will", "choose", "decided"}
        }

    def _extract_decisions(self, events: list[_Event]) -> list[DecisionEvidence]:
        candidates: list[dict[str, Any]] = []
        has_error_evidence = any(
            event.event_type == "error"
            or _safe_get(event.raw, "is_error")
            or _normalize_name(_safe_get(event.raw, "status", "")) in {"error", "failed", "failure"}
            for event in events
        )
        has_verification_evidence = any(
            event.event_type == "tool_result"
            and self._verification_kind(
                event.tool_name or "",
                "",
                self._event_text(event),
            )
            is not None
            for event in events
        )
        for event in events:
            text = self._event_text(event).strip()
            if not text:
                continue
            if event.event_type in {"user_constraint", "user_correction"}:
                candidates.append({
                    "statement": text,
                    "rationale": None,
                    "event_ids": [event.event_id],
                    "status": "confirmed",
                    "kind": event.event_type,
                })
                continue
            if event.event_type == "tool_result" and "requires-python" in text.lower():
                version = re.search(r"(?:>=|~=|==|>)\s*([0-9]+(?:\.[0-9]+)+)", text)
                version_text = version.group(1) if version else "unknown"
                candidates.append({
                    "statement": f"Python {version_text} project constraint: {text}",
                    "rationale": text,
                    "event_ids": [event.event_id],
                    "status": "confirmed",
                    "kind": "config_constraint",
                })
                continue
            if event.event_type not in {"assistant", "assistant_step"}:
                continue
            lowered = text.lower()
            if re.search(r"\b(?:i will|start by)\s+(?:read|list|inspect|format)\b", lowered):
                continue
            explicit = bool(
                re.search(r"\b(?:i\s+)?(?:choose|chose|decide|decided|select|selected)\b", lowered)
                or re.search(r"\bi will preserve\b", lowered)
                or re.search(r"\b(?:caused|fixes|root cause is)\b", lowered)
                or re.search(r"(?:选择|决定|导致|根因是)", text)
            )
            if not explicit or re.search(r"\broot cause is not yet known\b", lowered):
                continue
            rationale: str | None = None
            choice = text
            if " because " in lowered:
                split_at = lowered.index(" because ")
                choice = text[:split_at].strip()
                rationale = text[split_at + len(" because ") :].strip()
            candidate = {
                "statement": choice,
                "rationale": rationale,
                "event_ids": [event.event_id],
                "status": (
                    "inferred"
                    if re.search(r"\b(?:caused|fixes|root cause is)\b", lowered)
                    and not (has_error_evidence and has_verification_evidence)
                    else "confirmed"
                ),
                "kind": "assistant_decision",
            }
            merged = False
            for existing in candidates:
                if existing["kind"] not in {"user_constraint", "config_constraint"}:
                    continue
                overlap = self._decision_tokens(existing["statement"]) & self._decision_tokens(text)
                if len(overlap) >= 2 or ("3.11" in existing["statement"] and "3.11" in text):
                    existing["statement"] = f"{existing['statement']} {text}"
                    existing["event_ids"].append(event.event_id)
                    existing["rationale"] = rationale or existing["rationale"]
                    merged = True
                    break
            if not merged:
                candidates.append(candidate)
                if rationale and re.search(r"\b(?:may|might|probably|likely)\b", rationale.lower()):
                    candidates.append({
                        "statement": rationale,
                        "rationale": rationale,
                        "event_ids": [event.event_id],
                        "status": "inferred",
                        "kind": "inferred_rationale",
                    })
        return [
            DecisionEvidence(
                decision_id=f"decision-{index:06d}",
                statement=sanitize_evidence_text(candidate["statement"]),
                rationale=sanitize_evidence_text(candidate["rationale"]) if candidate["rationale"] else None,
                event_ids=_ordered_unique(candidate["event_ids"]),
                epistemic_status=candidate["status"],
                source_kind=candidate["kind"],
            )
            for index, candidate in enumerate(candidates[:EVIDENCE_MAX_LIST_ITEMS], start=1)
        ]

    def _extract_outcome(
        self,
        events: list[_Event],
        verification: list[VerificationEvidence],
    ) -> Literal["success", "failed", "unknown"]:
        positions = {event.event_id: event.index for event in events}
        task_results = [event for event in events if event.event_type == "task_result"]
        last_task = task_results[-1] if task_results else None
        failed_verification_positions = [
            max((positions.get(event_id, -1) for event_id in item.event_ids), default=-1)
            for item in verification
            if item.result == "failed"
        ]
        if last_task:
            later = [
                item
                for item in verification
                if max((positions.get(event_id, -1) for event_id in item.event_ids), default=-1)
                > last_task.index
                and item.result in {"passed", "failed"}
            ]
            if later:
                return "success" if later[-1].result == "passed" else "failed"
            status = _normalize_name(_safe_get(last_task.raw, "final_outcome", "")) or _normalize_name(
                _safe_get(last_task.raw, "status", "")
            )
            if status in {"success", "completed", "ok", "passed"}:
                return "success"
            if status in {"failed", "failure", "error"}:
                return "failed"
            return "unknown"
        if failed_verification_positions:
            return "failed"
        if any(item.result == "passed" for item in verification):
            return "success"
        if any(
            event.event_type in {"assistant", "assistant_step"}
            and _normalize_name(_safe_get(event.raw, "content_kind", "")) == "final"
            for event in events
        ):
            return "success"
        return "unknown"


__all__ = [
    "DecisionEvidence",
    "EpistemicStatus",
    "ErrorEvidence",
    "FileEvidence",
    "LibraryEvidence",
    "RecoveryEvidence",
    "RecoverySuggestionEvidence",
    "TRACE_MAX_EVENTS",
    "TRACE_SCHEMA_VERSION",
    "TaskEvidence",
    "ToolEvidence",
    "TraceEvidenceExtractor",
    "VerificationEvidence",
    "append_trace_event",
    "extract_tool_file_roles",
    "sanitize_evidence_text",
]
