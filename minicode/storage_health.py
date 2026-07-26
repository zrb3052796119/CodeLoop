"""Read-only, bounded inventory of MiniCode persistence.

This module is the single persistence-health authority.  It deliberately does
not construct storage managers because several existing managers acquire write
locks, create roots, perform retention, or recover data while loading.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Final, Literal

from minicode.config import MINI_CODE_DIR


SCHEMA_VERSION: Final = 1
MAX_DIRECTORY_ENTRIES: Final = 25_000
MAX_PARSED_FILE_BYTES: Final = 2 * 1024 * 1024
MAX_RESPONSE_BYTES: Final = 256 * 1024
MAX_SAFE_INTEGER: Final = (2**53) - 1
MAX_DIAGNOSTICS: Final = 64
MAX_WORKSPACE_NAME_CHARS: Final = 160

StoreStatus = Literal["live", "partial", "unavailable", "error"]
Scope = Literal["workspace", "user", "local", "configuration", "process"]
Durability = Literal["persistent", "process-local", "source"]
ResetDisposition = Literal["planned", "excluded", "not-applicable"]

_WORKSPACE_ID_RE = re.compile(r"^ws_[0-9a-f]{16}$")
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)

_LIVE_MESSAGE = "The bounded read-only scan completed."
_PARTIAL_MESSAGE = "Some persisted facts could not be verified safely."
_UNAVAILABLE_MESSAGE = "This store could not be inspected safely."
_PROCESS_MESSAGE = "Process-local state is not a disk persistence fact."
_ALLOWED_MESSAGES = {
    None,
    _LIVE_MESSAGE,
    _PARTIAL_MESSAGE,
    _UNAVAILABLE_MESSAGE,
    _PROCESS_MESSAGE,
}

_DIAGNOSTIC_MESSAGES: Final = {
    "scan_limited": "The directory entry budget was reached.",
    "root_unsafe": "A configured storage root was not a regular directory.",
    "entry_unsafe": "A symbolic link or special entry was rejected.",
    "read_failed": "A persisted file could not be read safely.",
    "oversized_file": "A file exceeded the bounded parsing limit.",
    "invalid_json": "A persisted JSON document was malformed.",
    "invalid_record": "A persisted record failed bounded validation.",
    "index_drift": "Canonical records and their index did not agree.",
    "orphan_reference": "A cross-store reference had no matching record.",
    "temporary_artifact": "A temporary or backup artifact remains on disk.",
    "legacy_source": "A legacy source exists but was not parsed for record counts.",
    "active_writer": "An active-writer marker is present.",
}


@dataclass(frozen=True, slots=True)
class _StoreSpec:
    id: str
    scope: Scope
    durability: Durability
    reset_disposition: ResetDisposition


_STORE_SPECS: Final = (
    _StoreSpec("sessions", "workspace", "persistent", "planned"),
    _StoreSpec("conversation-turns", "workspace", "persistent", "planned"),
    _StoreSpec("run-journal", "workspace", "persistent", "planned"),
    _StoreSpec("deletion-coordination", "workspace", "persistent", "planned"),
    _StoreSpec("memory-user", "user", "persistent", "excluded"),
    _StoreSpec("memory-project", "workspace", "persistent", "planned"),
    _StoreSpec("memory-local", "local", "persistent", "planned"),
    _StoreSpec("memory-approval-user", "user", "persistent", "excluded"),
    _StoreSpec("memory-approval-project", "workspace", "persistent", "planned"),
    _StoreSpec("memory-approval-local", "local", "persistent", "planned"),
    _StoreSpec("memory-pipeline-state", "workspace", "persistent", "planned"),
    _StoreSpec("tool-results", "workspace", "persistent", "planned"),
    _StoreSpec("permissions", "user", "persistent", "excluded"),
    _StoreSpec("configuration", "configuration", "source", "excluded"),
    _StoreSpec("mcp-configuration", "configuration", "source", "excluded"),
    _StoreSpec("user-profile", "user", "source", "excluded"),
    _StoreSpec("project-profile", "configuration", "source", "excluded"),
    _StoreSpec("skills-user", "user", "source", "excluded"),
    _StoreSpec("skills-project", "configuration", "source", "excluded"),
    _StoreSpec("user-runtime-artifacts", "user", "persistent", "excluded"),
    _StoreSpec(
        "workspace-runtime-artifacts",
        "configuration",
        "source",
        "excluded",
    ),
    _StoreSpec("permission-broker", "process", "process-local", "not-applicable"),
    _StoreSpec(
        "mcp-current-registry",
        "process",
        "process-local",
        "not-applicable",
    ),
    _StoreSpec("gateway-runtime", "process", "process-local", "not-applicable"),
    _StoreSpec("working-memory", "process", "process-local", "not-applicable"),
)
_STORE_BY_ID: Final = {spec.id: spec for spec in _STORE_SPECS}


@dataclass(slots=True)
class _ScanBudget:
    limit: int
    used: int = 0
    exhausted: bool = False

    def consume(self) -> bool:
        if self.used >= self.limit:
            self.exhausted = True
            return False
        self.used += 1
        return True


@dataclass(slots=True)
class _ScanResult:
    status: StoreStatus = "live"
    record_count: int | None = 0
    byte_count: int | None = 0
    updated_at: datetime | None = None
    diagnostics: list[str] = field(default_factory=list)
    identities: set[str] = field(default_factory=set, repr=False)
    references: set[str] = field(default_factory=set, repr=False)
    audit_references: set[str] = field(default_factory=set, repr=False)
    active_fence: bool = field(default=False, repr=False)

    def add_bytes(self, value: int) -> None:
        if self.byte_count is None:
            return
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            self.degrade("invalid_record")
            return
        self.byte_count = min(MAX_SAFE_INTEGER, self.byte_count + value)

    def add_records(self, value: int = 1) -> None:
        if self.record_count is None:
            return
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            self.degrade("invalid_record")
            return
        self.record_count = min(MAX_SAFE_INTEGER, self.record_count + value)

    def observe_mtime(self, value: float) -> None:
        try:
            observed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            self.degrade("invalid_record")
            return
        if observed < datetime(1970, 1, 1, tzinfo=timezone.utc):
            self.degrade("invalid_record")
            return
        if self.updated_at is None or observed > self.updated_at:
            self.updated_at = observed

    def degrade(self, code: str, *, unavailable: bool = False) -> None:
        if code not in _DIAGNOSTIC_MESSAGES:
            code = "read_failed"
        if code not in self.diagnostics:
            self.diagnostics.append(code)
        if unavailable:
            self.status = "unavailable"
            self.record_count = None
            self.byte_count = None
            self.updated_at = None
        elif self.status == "live":
            self.status = "partial"


class PersistenceHealthContractError(ValueError):
    """A health snapshot violated the closed public contract."""


def _exact_int(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_SAFE_INTEGER
    )


def _valid_timestamp(value: object, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0
        and _iso_time(parsed) == value
    )


def _exact_keys(value: object, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def validate_persistence_health_snapshot(
    payload: object,
) -> dict[str, object]:
    """Validate and return one exact schema-v1 snapshot."""
    top_keys = {
        "schemaVersion",
        "generatedAt",
        "mode",
        "status",
        "workspace",
        "summary",
        "stores",
        "maintenancePlan",
        "diagnostics",
    }
    if not _exact_keys(payload, top_keys):
        raise PersistenceHealthContractError("invalid data-health contract")
    assert isinstance(payload, dict)
    if (
        payload["schemaVersion"] != SCHEMA_VERSION
        or isinstance(payload["schemaVersion"], bool)
        or payload["mode"] != "read-only"
        or payload["status"] not in {"live", "partial", "unavailable", "error"}
        or not _valid_timestamp(payload["generatedAt"])
    ):
        raise PersistenceHealthContractError("invalid data-health contract")

    workspace = payload["workspace"]
    if (
        not _exact_keys(workspace, {"id", "name"})
        or not isinstance(workspace["id"], str)
        or _WORKSPACE_ID_RE.fullmatch(workspace["id"]) is None
        or not isinstance(workspace["name"], str)
        or not 1 <= len(workspace["name"]) <= MAX_WORKSPACE_NAME_CHARS
        or any(character in workspace["name"] for character in ("/", "\\", "\x00"))
    ):
        raise PersistenceHealthContractError("invalid data-health workspace")

    summary = payload["summary"]
    if not _exact_keys(
        summary,
        {"storeCount", "knownRecordCount", "knownByteCount", "issueCount"},
    ) or not all(_exact_int(value) for value in summary.values()):
        raise PersistenceHealthContractError("invalid data-health summary")

    stores = payload["stores"]
    if not isinstance(stores, list) or len(stores) > len(_STORE_SPECS):
        raise PersistenceHealthContractError("invalid data-health stores")
    store_keys = {
        "id",
        "scope",
        "durability",
        "status",
        "recordCount",
        "byteCount",
        "updatedAt",
        "resetDisposition",
        "message",
    }
    seen: set[str] = set()
    for store in stores:
        if not _exact_keys(store, store_keys):
            raise PersistenceHealthContractError("invalid data-health store")
        store_id = store["id"]
        spec = _STORE_BY_ID.get(store_id) if isinstance(store_id, str) else None
        if (
            spec is None
            or store_id in seen
            or store["scope"] != spec.scope
            or store["durability"] != spec.durability
            or store["resetDisposition"] != spec.reset_disposition
            or store["status"] not in {"live", "partial", "unavailable", "error"}
            or store["message"] not in _ALLOWED_MESSAGES
            or not _valid_timestamp(store["updatedAt"], nullable=True)
        ):
            raise PersistenceHealthContractError("invalid data-health store")
        seen.add(store_id)
        for count_field in ("recordCount", "byteCount"):
            if store[count_field] is not None and not _exact_int(store[count_field]):
                raise PersistenceHealthContractError("invalid data-health count")
        if spec.durability == "process-local" and (
            store["recordCount"] is not None
            or store["byteCount"] is not None
            or store["updatedAt"] is not None
            or store["status"] != "live"
            or store["message"] != _PROCESS_MESSAGE
        ):
            raise PersistenceHealthContractError("invalid process-local store")

    plan = payload["maintenancePlan"]
    if (
        not _exact_keys(
            plan,
            {
                "status",
                "destructiveActionsAvailable",
                "eligibleStoreIds",
                "excludedStoreIds",
                "blockers",
            },
        )
        or plan["status"] != "planning"
        or plan["destructiveActionsAvailable"] is not False
        or not isinstance(plan["eligibleStoreIds"], list)
        or not isinstance(plan["excludedStoreIds"], list)
        or not isinstance(plan["blockers"], list)
    ):
        raise PersistenceHealthContractError("invalid maintenance plan")
    for store_list_field in ("eligibleStoreIds", "excludedStoreIds"):
        values = plan[store_list_field]
        if len(values) != len(set(values)) or any(
            value not in seen for value in values
        ):
            raise PersistenceHealthContractError("invalid maintenance stores")
    expected_eligible = [
        store["id"] for store in stores if store["resetDisposition"] == "planned"
    ]
    expected_excluded = [
        store["id"] for store in stores if store["resetDisposition"] == "excluded"
    ]
    if (
        plan["eligibleStoreIds"] != expected_eligible
        or plan["excludedStoreIds"] != expected_excluded
    ):
        raise PersistenceHealthContractError("invalid maintenance stores")
    for blocker in plan["blockers"]:
        if (
            not _exact_keys(blocker, {"code", "storeId"})
            or blocker["code"] not in {"store_not_live", "active_maintenance_fence"}
            or blocker["storeId"] not in expected_eligible
            or (
                blocker["code"] == "active_maintenance_fence"
                and blocker["storeId"] != "deletion-coordination"
            )
        ):
            raise PersistenceHealthContractError("invalid maintenance blocker")

    diagnostics = payload["diagnostics"]
    if not isinstance(diagnostics, list) or len(diagnostics) > MAX_DIAGNOSTICS:
        raise PersistenceHealthContractError("invalid diagnostics")
    for diagnostic in diagnostics:
        if (
            not _exact_keys(diagnostic, {"storeId", "code", "message"})
            or diagnostic["storeId"] not in seen
            or not isinstance(diagnostic["code"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", diagnostic["code"])
            or not isinstance(diagnostic["message"], str)
            or not 1 <= len(diagnostic["message"]) <= 160
            or any(
                marker in diagnostic["message"].casefold()
                for marker in ("secret", "token", "password", "/users/", "\\users\\")
            )
        ):
            raise PersistenceHealthContractError("invalid diagnostic")

    if summary["storeCount"] != len(stores):
        raise PersistenceHealthContractError("invalid store count")
    known_records = sum(
        store["recordCount"] for store in stores if store["recordCount"] is not None
    )
    known_bytes = sum(
        store["byteCount"] for store in stores if store["byteCount"] is not None
    )
    issue_count = sum(store["status"] != "live" for store in stores)
    if (
        summary["knownRecordCount"] != min(known_records, MAX_SAFE_INTEGER)
        or summary["knownByteCount"] != min(known_bytes, MAX_SAFE_INTEGER)
        or summary["issueCount"] != issue_count
    ):
        raise PersistenceHealthContractError("invalid summary totals")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PersistenceHealthContractError("invalid response encoding") from error
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise PersistenceHealthContractError("data-health response too large")
    return payload


def _iso_time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock must return an aware datetime")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _workspace_id(workspace: Path) -> str:
    return "ws_" + hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:16]


def _safe_workspace_name(workspace: Path) -> str:
    value = workspace.name.strip()
    if (
        not value
        or len(value) > MAX_WORKSPACE_NAME_CHARS
        or any(character in value for character in ("/", "\\", "\x00"))
    ):
        return "Workspace"
    return value


class PersistenceHealthReader:
    """Inspect only fixed persistence roots for one startup Workspace."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        data_dir: str | Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_directory_entries: int = MAX_DIRECTORY_ENTRIES,
        max_parsed_file_bytes: int = MAX_PARSED_FILE_BYTES,
    ) -> None:
        if (
            not _exact_int(max_directory_entries)
            or not 1 <= max_directory_entries <= MAX_DIRECTORY_ENTRIES
            or not _exact_int(max_parsed_file_bytes)
            or not 1 <= max_parsed_file_bytes <= MAX_PARSED_FILE_BYTES
        ):
            raise ValueError("invalid persistence health budget")
        self.workspace = Path(workspace).expanduser().resolve()
        self.data_dir = Path(
            data_dir if data_dir is not None else MINI_CODE_DIR
        ).expanduser()
        self._clock = clock
        self._budget = _ScanBudget(max_directory_entries)
        self._max_file_bytes = max_parsed_file_bytes
        self._workspace_id = _workspace_id(self.workspace)

    @staticmethod
    def _is_temporary_name(name: str) -> bool:
        return (
            name.endswith(".tmp") or name.endswith(".bak") or name.endswith(".backup")
        )

    @staticmethod
    def _path_under(anchor: Path, target: Path) -> bool:
        try:
            target.relative_to(anchor)
            return True
        except ValueError:
            return False

    def _safe_chain(self, anchor: Path, target: Path) -> str:
        """Classify a fixed path without following a symlink below its anchor."""
        if not self._path_under(anchor, target):
            return "unsafe"
        parts = target.relative_to(anchor).parts
        candidates = (
            anchor,
            *(anchor.joinpath(*parts[:index]) for index in range(1, len(parts) + 1)),
        )
        for index, candidate in enumerate(candidates):
            try:
                info = os.lstat(candidate)
            except FileNotFoundError:
                return "missing"
            except OSError:
                return "error"
            if stat.S_ISLNK(info.st_mode):
                return "unsafe"
            if index < len(candidates) - 1 and not stat.S_ISDIR(info.st_mode):
                return "unsafe"
        return "present"

    def _directory_entries(
        self,
        root: Path,
        *,
        anchor: Path,
        result: _ScanResult,
    ) -> list[os.DirEntry[str]]:
        state = self._safe_chain(anchor, root)
        if state == "missing":
            return []
        if state in {"unsafe", "error"}:
            result.degrade("root_unsafe", unavailable=True)
            return []
        try:
            info = os.lstat(root)
            if not stat.S_ISDIR(info.st_mode):
                result.degrade("root_unsafe", unavailable=True)
                return []
            entries: list[os.DirEntry[str]] = []
            with os.scandir(root) as scanner:
                for entry in scanner:
                    if not self._budget.consume():
                        result.degrade("scan_limited")
                        break
                    entries.append(entry)
            return entries
        except OSError:
            result.degrade("read_failed", unavailable=True)
            return []

    def _read_file(
        self,
        path: Path,
        *,
        anchor: Path,
        result: _ScanResult,
    ) -> bytes | None:
        state = self._safe_chain(anchor, path)
        if state == "missing":
            return None
        if state in {"unsafe", "error"}:
            result.degrade("entry_unsafe")
            return None
        descriptor = -1
        try:
            path_info = os.lstat(path)
            if not stat.S_ISREG(path_info.st_mode):
                result.degrade("entry_unsafe")
                return None
            result.add_bytes(path_info.st_size)
            result.observe_mtime(path_info.st_mtime)
            if path_info.st_size > self._max_file_bytes:
                result.degrade("oversized_file")
                return None
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            descriptor_info = os.fstat(descriptor)
            current_info = os.lstat(path)
            if (
                not stat.S_ISREG(descriptor_info.st_mode)
                or not stat.S_ISREG(current_info.st_mode)
                or descriptor_info.st_dev != current_info.st_dev
                or descriptor_info.st_ino != current_info.st_ino
                or descriptor_info.st_size != current_info.st_size
            ):
                result.degrade("entry_unsafe")
                return None
            chunks: list[bytes] = []
            remaining = self._max_file_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > self._max_file_bytes:
                result.degrade("oversized_file")
                return None
            return data
        except OSError:
            result.degrade("read_failed")
            return None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _stat_file(
        self,
        path: Path,
        *,
        anchor: Path,
        result: _ScanResult,
    ) -> bool:
        """Count one fixed regular file without reading any file content."""
        state = self._safe_chain(anchor, path)
        if state == "missing":
            return False
        if state in {"unsafe", "error"}:
            result.degrade("entry_unsafe")
            return False
        try:
            path_info = os.lstat(path)
        except OSError:
            result.degrade("read_failed")
            return False
        if not stat.S_ISREG(path_info.st_mode):
            result.degrade("entry_unsafe")
            return False
        result.add_bytes(path_info.st_size)
        result.observe_mtime(path_info.st_mtime)
        return True

    def _read_json(
        self,
        path: Path,
        *,
        anchor: Path,
        result: _ScanResult,
    ) -> object | None:
        raw = self._read_file(path, anchor=anchor, result=result)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            result.degrade("invalid_json")
            return None

    def _workspace_matches(self, value: object) -> bool:
        if not isinstance(value, str) or not 1 <= len(value) <= 4_096:
            return False
        try:
            normalized = os.path.normcase(os.path.abspath(os.path.expanduser(value)))
        except (OSError, ValueError):
            return False
        return normalized == os.path.normcase(str(self.workspace))

    def _scan_sessions(self) -> _ScanResult:
        result = _ScanResult()
        index_path = self.data_dir / "sessions_index.json"
        raw_index = self._read_json(index_path, anchor=self.data_dir, result=result)
        indexed_ids: set[str] = set()
        all_indexed_ids: set[str] = set()
        if raw_index is not None:
            if not isinstance(raw_index, dict):
                result.degrade("invalid_record")
            else:
                for session_id, metadata in raw_index.items():
                    valid_identity = (
                        isinstance(session_id, str)
                        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id)
                        and isinstance(metadata, dict)
                        and metadata.get("session_id") == session_id
                    )
                    if valid_identity:
                        all_indexed_ids.add(session_id)
                        if self._workspace_matches(metadata.get("workspace")):
                            indexed_ids.add(session_id)
                    elif isinstance(metadata, dict) and self._workspace_matches(
                        metadata.get("workspace")
                    ):
                        result.degrade("invalid_record")

        sessions_root = self.data_dir / "sessions"
        entries = self._directory_entries(
            sessions_root, anchor=self.data_dir, result=result
        )
        base_ids: set[str] = set()
        generations: dict[str, int] = {}
        for entry in entries:
            if entry.name == "deltas":
                try:
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        result.degrade("entry_unsafe")
                except OSError:
                    result.degrade("read_failed")
                continue
            match = re.fullmatch(
                r"([A-Za-z0-9][A-Za-z0-9_-]{0,127})\.json",
                entry.name,
            )
            if match is None:
                if self._is_temporary_name(entry.name):
                    result.degrade("temporary_artifact")
                elif entry.name not in {".DS_Store"}:
                    result.degrade("entry_unsafe")
                continue
            session_id = match.group(1)
            if session_id not in all_indexed_ids:
                # The Session base root is shared by Workspaces. Without the
                # canonical index, ownership cannot be established without
                # reading a possibly foreign Session body.
                result.degrade("index_drift")
                continue
            if session_id not in indexed_ids:
                continue
            parsed = self._read_json(
                Path(entry.path), anchor=self.data_dir, result=result
            )
            if parsed is None:
                continue
            generation = (
                parsed.get("persistence_generation", 0)
                if isinstance(parsed, dict)
                else None
            )
            if (
                not isinstance(parsed, dict)
                or parsed.get("session_id") != session_id
                or not self._workspace_matches(parsed.get("workspace"))
            ):
                if isinstance(parsed, dict) and self._workspace_matches(
                    parsed.get("workspace")
                ):
                    result.degrade("invalid_record")
                continue
            if (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or not 0 <= generation <= (2**31) - 1
            ):
                result.degrade("invalid_record")
                continue
            base_ids.add(session_id)
            generations[session_id] = generation

        owned_ids = base_ids | indexed_ids
        result.record_count = len(owned_ids)
        result.identities = owned_ids
        if base_ids != indexed_ids and (base_ids or indexed_ids):
            result.degrade("index_drift")

        deltas_root = sessions_root / "deltas"
        delta_sessions = self._directory_entries(
            deltas_root, anchor=self.data_dir, result=result
        )
        for entry in delta_sessions:
            if entry.name not in owned_ids:
                continue
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    result.degrade("entry_unsafe")
                    continue
            except OSError:
                result.degrade("read_failed")
                continue
            delta_entries = self._directory_entries(
                Path(entry.path), anchor=self.data_dir, result=result
            )
            for delta_entry in delta_entries:
                if re.fullmatch(r"delta_[0-9]{4,8}\.json", delta_entry.name) is None:
                    result.degrade(
                        "temporary_artifact"
                        if self._is_temporary_name(delta_entry.name)
                        else "entry_unsafe"
                    )
                    continue
                parsed = self._read_json(
                    Path(delta_entry.path), anchor=self.data_dir, result=result
                )
                if parsed is None:
                    continue
                generation = (
                    parsed.get("persistence_generation", 0)
                    if isinstance(parsed, dict)
                    else None
                )
                if (
                    not isinstance(parsed, dict)
                    or parsed.get("session_id", entry.name) != entry.name
                    or isinstance(generation, bool)
                    or not isinstance(generation, int)
                    or generation != generations.get(entry.name, generation)
                ):
                    result.degrade("invalid_record")
        return result

    def _scan_turns(self) -> _ScanResult:
        result = _ScanResult()
        root = self.data_dir / "dashboard" / "workspaces" / self._workspace_id / "turns"
        for entry in self._directory_entries(root, anchor=self.data_dir, result=result):
            match = re.fullmatch(r"(turn_[0-9a-f]{32})\.json", entry.name)
            if match is None:
                if self._is_temporary_name(entry.name):
                    result.degrade("temporary_artifact")
                else:
                    result.degrade("entry_unsafe")
                continue
            parsed = self._read_json(
                Path(entry.path), anchor=self.data_dir, result=result
            )
            if (
                not isinstance(parsed, dict)
                or parsed.get("schemaVersion") != 1
                or isinstance(parsed.get("schemaVersion"), bool)
                or parsed.get("turnId") != match.group(1)
                or parsed.get("workspaceId") != self._workspace_id
                or parsed.get("status")
                not in {
                    "accepted",
                    "running",
                    "cancel_requested",
                    "committing",
                    "completed",
                    "failed",
                    "interrupted",
                    "cancelled",
                }
            ):
                if parsed is not None:
                    result.degrade("invalid_record")
                continue
            result.add_records()
            result.identities.add(match.group(1))
        return result

    def _scan_runs(self) -> _ScanResult:
        result = _ScanResult()
        root = self.data_dir / "dashboard" / "workspaces" / self._workspace_id / "runs"
        entries = self._directory_entries(root, anchor=self.data_dir, result=result)
        run_ids: set[str] = set()
        index_ids: set[str] | None = None
        for entry in entries:
            if entry.name == "index.json":
                parsed = self._read_json(
                    Path(entry.path), anchor=self.data_dir, result=result
                )
                raw_ids = parsed.get("runIds") if isinstance(parsed, dict) else None
                if (
                    isinstance(parsed, dict)
                    and parsed.get("schemaVersion") == 1
                    and not isinstance(parsed.get("schemaVersion"), bool)
                    and parsed.get("workspaceId") == self._workspace_id
                    and isinstance(raw_ids, list)
                    and all(
                        isinstance(item, str)
                        and re.fullmatch(r"run_[0-9a-f]{32}", item)
                        for item in raw_ids
                    )
                    and len(raw_ids) == len(set(raw_ids))
                ):
                    index_ids = set(raw_ids)
                elif parsed is not None:
                    result.degrade("invalid_record")
                continue
            if entry.name == ".index.lock":
                result.degrade("active_writer")
                continue
            if self._is_temporary_name(entry.name):
                result.degrade("temporary_artifact")
                continue
            if re.fullmatch(r"run_[0-9a-f]{32}", entry.name) is None:
                result.degrade("entry_unsafe")
                continue
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    result.degrade("entry_unsafe")
                    continue
            except OSError:
                result.degrade("read_failed")
                continue
            run_dir = Path(entry.path)
            metadata = self._read_json(
                run_dir / "metadata.json",
                anchor=self.data_dir,
                result=result,
            )
            if (
                not isinstance(metadata, dict)
                or metadata.get("schemaVersion") != 1
                or isinstance(metadata.get("schemaVersion"), bool)
                or metadata.get("id") != entry.name
                or metadata.get("workspaceId") != self._workspace_id
            ):
                if metadata is not None:
                    result.degrade("invalid_record")
                continue
            events_raw = self._read_file(
                run_dir / "events.ndjson",
                anchor=self.data_dir,
                result=result,
            )
            if events_raw is not None:
                if events_raw and not events_raw.endswith(b"\n"):
                    result.degrade("invalid_record")
                else:
                    for line in events_raw.splitlines():
                        try:
                            event = json.loads(line.decode("utf-8", errors="strict"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            result.degrade("invalid_json")
                            break
                        if (
                            not isinstance(event, dict)
                            or event.get("schemaVersion") != 1
                            or isinstance(event.get("schemaVersion"), bool)
                            or event.get("runId") != entry.name
                            or event.get("workspaceId") != self._workspace_id
                        ):
                            result.degrade("invalid_record")
                            break
            children = self._directory_entries(
                run_dir, anchor=self.data_dir, result=result
            )
            for child in children:
                if child.name in {"metadata.json", "events.ndjson"}:
                    continue
                if child.name == ".writer.lock":
                    if self._stat_file(
                        Path(child.path), anchor=self.data_dir, result=result
                    ):
                        result.degrade("active_writer")
                    continue
                result.degrade(
                    "temporary_artifact"
                    if self._is_temporary_name(child.name)
                    else "entry_unsafe"
                )
            run_ids.add(entry.name)
            result.add_records()
        if index_ids is not None and index_ids != run_ids:
            result.degrade("index_drift")
        elif index_ids is None and run_ids:
            result.degrade("index_drift")
        result.identities = run_ids
        return result

    def _memory_root(self, scope: str) -> tuple[Path, Path]:
        if scope == "user":
            return self.data_dir / "memory", self.data_dir
        if scope == "project":
            return self.workspace / ".mini-code-memory", self.workspace
        return self.workspace / ".mini-code-memory-local", self.workspace

    def _scan_memory(self, scope: str) -> _ScanResult:
        result = _ScanResult()
        root, anchor = self._memory_root(scope)
        entries = self._directory_entries(root, anchor=anchor, result=result)
        names = {entry.name for entry in entries}
        memory_path = root / "memory.json"
        parsed = self._read_json(memory_path, anchor=anchor, result=result)
        if parsed is not None:
            raw_entries = parsed.get("entries") if isinstance(parsed, dict) else None
            if (
                not isinstance(parsed, dict)
                or parsed.get("scope", scope) != scope
                or not isinstance(raw_entries, list)
            ):
                result.degrade("invalid_record")
            else:
                for entry in raw_entries:
                    entry_id = entry.get("id") if isinstance(entry, dict) else None
                    content = entry.get("content") if isinstance(entry, dict) else None
                    related = (
                        entry.get("related_to", []) if isinstance(entry, dict) else None
                    )
                    if (
                        not isinstance(entry, dict)
                        or not isinstance(entry_id, str)
                        or not 1 <= len(entry_id) <= 160
                        or entry.get("scope", scope) != scope
                        or not isinstance(content, str)
                        or not isinstance(related, list)
                        or any(not isinstance(item, str) for item in related)
                    ):
                        result.degrade("invalid_record")
                        continue
                    result.add_records()
                    result.identities.add(entry_id)
                    result.references.update(related)
        elif "MEMORY.md" in names:
            result.record_count = None
            result.degrade("legacy_source")
        if "MEMORY.md" in names:
            self._stat_file(root / "MEMORY.md", anchor=anchor, result=result)
        for entry in entries:
            if entry.name in {
                "memory.json",
                "MEMORY.md",
                "approval_audit.json",
                "pipeline_state.json",
            }:
                continue
            if self._is_temporary_name(entry.name):
                self._stat_file(Path(entry.path), anchor=anchor, result=result)
                result.degrade("temporary_artifact")
            elif entry.name not in {".DS_Store"}:
                result.degrade("entry_unsafe")
        return result

    def _scan_memory_audit(self, scope: str) -> _ScanResult:
        result = _ScanResult()
        root, anchor = self._memory_root(scope)
        path = root / "approval_audit.json"
        parsed = self._read_json(path, anchor=anchor, result=result)
        if parsed is None:
            return result
        records = parsed.get("records") if isinstance(parsed, dict) else None
        if (
            not isinstance(parsed, dict)
            or parsed.get("scope", scope) != scope
            or not isinstance(records, list)
        ):
            result.degrade("invalid_record")
            return result
        for record in records:
            entry_id = record.get("entry_id") if isinstance(record, dict) else None
            if not isinstance(record, dict) or not isinstance(entry_id, str):
                result.degrade("invalid_record")
                continue
            result.add_records()
            result.audit_references.add(entry_id)
        return result

    def _scan_deletion_coordination(self) -> _ScanResult:
        result = _ScanResult()
        root = (
            self.data_dir
            / "dashboard"
            / "workspaces"
            / self._workspace_id
            / "deletions"
        )
        for entry in self._directory_entries(root, anchor=self.data_dir, result=result):
            if entry.name == "coordination.lock":
                self._stat_file(Path(entry.path), anchor=self.data_dir, result=result)
                continue
            if self._is_temporary_name(entry.name):
                self._stat_file(Path(entry.path), anchor=self.data_dir, result=result)
                result.degrade("temporary_artifact")
                continue
            if (
                re.fullmatch(
                    r"(conversation|project-memory)-[0-9a-f]{64}\.(fence|receipt)\.json",
                    entry.name,
                )
                is None
            ):
                result.degrade("entry_unsafe")
                continue
            parsed = self._read_json(
                Path(entry.path), anchor=self.data_dir, result=result
            )
            if (
                not isinstance(parsed, dict)
                or parsed.get("schemaVersion") != 1
                or isinstance(parsed.get("schemaVersion"), bool)
                or parsed.get("kind") not in {"conversation", "project-memory"}
                or parsed.get("status") not in {"in_progress", "completed"}
            ):
                if parsed is not None:
                    result.degrade("invalid_record")
                continue
            result.add_records()
            if parsed.get("status") == "in_progress":
                result.active_fence = True
        return result

    def _scan_fixed_files(
        self,
        paths: tuple[tuple[Path, Path, bool], ...],
    ) -> _ScanResult:
        result = _ScanResult()
        for path, anchor, parse_json in paths:
            if parse_json:
                parsed = self._read_json(path, anchor=anchor, result=result)
                if parsed is not None:
                    if isinstance(parsed, (dict, list)):
                        result.add_records()
                    else:
                        result.degrade("invalid_record")
            else:
                if self._stat_file(path, anchor=anchor, result=result):
                    result.add_records()
        return result

    def _scan_file_tree(
        self,
        root: Path,
        *,
        anchor: Path,
        allowed_depth: int,
        required_name: str | None = None,
    ) -> _ScanResult:
        result = _ScanResult()

        def visit(directory: Path, depth: int) -> None:
            for entry in self._directory_entries(
                directory, anchor=anchor, result=result
            ):
                try:
                    if entry.is_symlink():
                        result.degrade("entry_unsafe")
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < allowed_depth:
                            visit(Path(entry.path), depth + 1)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        result.degrade("entry_unsafe")
                        continue
                except OSError:
                    result.degrade("read_failed")
                    continue
                if required_name is not None and entry.name != required_name:
                    continue
                if self._stat_file(Path(entry.path), anchor=anchor, result=result):
                    result.add_records()

        visit(root, 0)
        return result

    def _scan_store_map(self) -> dict[str, _ScanResult]:
        results: dict[str, _ScanResult] = {
            "sessions": self._scan_sessions(),
            "conversation-turns": self._scan_turns(),
            "run-journal": self._scan_runs(),
            "deletion-coordination": self._scan_deletion_coordination(),
        }
        for scope in ("user", "project", "local"):
            results[f"memory-{scope}"] = self._scan_memory(scope)
            results[f"memory-approval-{scope}"] = self._scan_memory_audit(scope)

        all_memory_ids = set().union(
            *(
                results[f"memory-{scope}"].identities
                for scope in ("user", "project", "local")
            )
        )
        for scope in ("user", "project", "local"):
            memory = results[f"memory-{scope}"]
            audit = results[f"memory-approval-{scope}"]
            if memory.references - all_memory_ids:
                memory.degrade("orphan_reference")
            if audit.audit_references - all_memory_ids:
                audit.degrade("orphan_reference")

        results["memory-pipeline-state"] = self._scan_fixed_files(
            (
                (
                    self.workspace / ".mini-code-memory" / "pipeline_state.json",
                    self.workspace,
                    True,
                ),
            )
        )
        results["tool-results"] = self._scan_file_tree(
            self.workspace / ".mini-code-tool-results",
            anchor=self.workspace,
            allowed_depth=0,
        )
        results["permissions"] = self._scan_fixed_files(
            ((self.data_dir / "permissions.json", self.data_dir, True),)
        )
        results["configuration"] = self._scan_fixed_files(
            (
                (self.data_dir / "settings.json", self.data_dir, True),
                (
                    self.data_dir.parent / ".claude" / "settings.json",
                    self.data_dir.parent,
                    True,
                ),
            )
        )
        results["mcp-configuration"] = self._scan_fixed_files(
            (
                (self.data_dir / "mcp.json", self.data_dir, True),
                (self.workspace / ".mcp.json", self.workspace, True),
            )
        )
        results["user-profile"] = self._scan_fixed_files(
            ((self.data_dir / "USER.md", self.data_dir, False),)
        )
        results["project-profile"] = self._scan_fixed_files(
            (
                (
                    self.workspace / ".mini-code" / "USER.md",
                    self.workspace,
                    False,
                ),
            )
        )
        user_skills = self._scan_file_tree(
            self.data_dir / "skills",
            anchor=self.data_dir,
            allowed_depth=2,
            required_name="SKILL.md",
        )
        self._merge_results(
            user_skills,
            self._scan_file_tree(
                self.data_dir.parent / ".claude" / "skills",
                anchor=self.data_dir.parent,
                allowed_depth=2,
                required_name="SKILL.md",
            ),
        )
        results["skills-user"] = user_skills
        project_skills = self._scan_file_tree(
            self.workspace / ".mini-code" / "skills",
            anchor=self.workspace,
            allowed_depth=2,
            required_name="SKILL.md",
        )
        self._merge_results(
            project_skills,
            self._scan_file_tree(
                self.workspace / ".claude" / "skills",
                anchor=self.workspace,
                allowed_depth=2,
                required_name="SKILL.md",
            ),
        )
        results["skills-project"] = project_skills
        user_artifacts = self._scan_fixed_files(
            tuple(
                (self.data_dir / name, self.data_dir, name.endswith(".json"))
                for name in (
                    "history.json",
                    "context_state.json",
                    "cybernetic_supervisor.json",
                    "minicode.log",
                    "session-store.lock",
                    "memory-store.lock",
                )
            )
        )
        for directory in ("tasks", "task_graphs", "audit"):
            nested = self._scan_file_tree(
                self.data_dir / directory,
                anchor=self.data_dir,
                allowed_depth=1,
            )
            self._merge_results(user_artifacts, nested)
        self._merge_results(
            user_artifacts,
            self._scan_file_tree(
                self.data_dir / "bin",
                anchor=self.data_dir,
                allowed_depth=1,
            ),
        )
        results["user-runtime-artifacts"] = user_artifacts
        results["workspace-runtime-artifacts"] = self._scan_fixed_files(
            (
                (
                    self.workspace / ".mini-code" / "cron.json",
                    self.workspace,
                    True,
                ),
            )
        )
        for store_id in (
            "permission-broker",
            "mcp-current-registry",
            "gateway-runtime",
            "working-memory",
        ):
            results[store_id] = _ScanResult(
                status="live",
                record_count=None,
                byte_count=None,
                updated_at=None,
            )
        return results

    @staticmethod
    def _merge_results(target: _ScanResult, source: _ScanResult) -> None:
        if target.record_count is not None and source.record_count is not None:
            target.add_records(source.record_count)
        elif source.record_count is None:
            target.record_count = None
        if target.byte_count is not None and source.byte_count is not None:
            target.add_bytes(source.byte_count)
        elif source.byte_count is None:
            target.byte_count = None
        if source.updated_at is not None and (
            target.updated_at is None or source.updated_at > target.updated_at
        ):
            target.updated_at = source.updated_at
        for diagnostic in source.diagnostics:
            target.degrade(
                diagnostic,
                unavailable=source.status == "unavailable",
            )

    def snapshot(self) -> dict[str, object]:
        self._budget = _ScanBudget(self._budget.limit)
        generated_at = _iso_time(self._clock())
        results = self._scan_store_map()
        stores: list[dict[str, object]] = []
        diagnostics: list[dict[str, str]] = []
        for spec in _STORE_SPECS:
            process_local = spec.durability == "process-local"
            result = results[spec.id]
            message = (
                _PROCESS_MESSAGE
                if process_local
                else _LIVE_MESSAGE
                if result.status == "live"
                else _UNAVAILABLE_MESSAGE
                if result.status == "unavailable"
                else _PARTIAL_MESSAGE
            )
            stores.append(
                {
                    "id": spec.id,
                    "scope": spec.scope,
                    "durability": spec.durability,
                    "status": result.status,
                    "recordCount": result.record_count,
                    "byteCount": result.byte_count,
                    "updatedAt": (
                        _iso_time(result.updated_at)
                        if result.updated_at is not None
                        else None
                    ),
                    "resetDisposition": spec.reset_disposition,
                    "message": message,
                }
            )
            for code in result.diagnostics:
                if len(diagnostics) >= MAX_DIAGNOSTICS:
                    break
                diagnostics.append(
                    {
                        "storeId": spec.id,
                        "code": code,
                        "message": _DIAGNOSTIC_MESSAGES[code],
                    }
                )
        known_record_count = sum(
            store["recordCount"] for store in stores if store["recordCount"] is not None
        )
        known_byte_count = sum(
            store["byteCount"] for store in stores if store["byteCount"] is not None
        )
        issue_count = sum(store["status"] != "live" for store in stores)
        blockers = [
            {"code": "store_not_live", "storeId": store["id"]}
            for store in stores
            if store["resetDisposition"] == "planned" and store["status"] != "live"
        ]
        deletion_result = results["deletion-coordination"]
        if deletion_result.active_fence:
            blockers.append(
                {
                    "code": "active_maintenance_fence",
                    "storeId": "deletion-coordination",
                }
            )
        payload: dict[str, object] = {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": generated_at,
            "mode": "read-only",
            "status": (
                "live"
                if issue_count == 0
                else "unavailable"
                if all(store["status"] == "unavailable" for store in stores)
                else "partial"
            ),
            "workspace": {
                "id": _workspace_id(self.workspace),
                "name": _safe_workspace_name(self.workspace),
            },
            "summary": {
                "storeCount": len(stores),
                "knownRecordCount": min(known_record_count, MAX_SAFE_INTEGER),
                "knownByteCount": min(known_byte_count, MAX_SAFE_INTEGER),
                "issueCount": issue_count,
            },
            "stores": stores,
            "maintenancePlan": {
                "status": "planning",
                "destructiveActionsAvailable": False,
                "eligibleStoreIds": [
                    spec.id
                    for spec in _STORE_SPECS
                    if spec.reset_disposition == "planned"
                ],
                "excludedStoreIds": [
                    spec.id
                    for spec in _STORE_SPECS
                    if spec.reset_disposition == "excluded"
                ],
                "blockers": blockers,
            },
            "diagnostics": diagnostics,
        }
        return validate_persistence_health_snapshot(payload)


__all__ = [
    "MAX_DIRECTORY_ENTRIES",
    "MAX_PARSED_FILE_BYTES",
    "MAX_RESPONSE_BYTES",
    "PersistenceHealthContractError",
    "PersistenceHealthReader",
    "SCHEMA_VERSION",
    "validate_persistence_health_snapshot",
]
