"""Bounded, content-free change detection for Dashboard live refresh.

The feed observes only filesystem metadata and a safe projection of the
process-local MCP registry.  It never opens persisted Dashboard content and
its opaque revisions are invalidation hints, not a second read model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from minicode.config import MINI_CODE_DIR
from minicode.mcp_current_state import normalize_mcp_current_state_snapshot
from minicode.run_journal import stable_workspace_id


_RESOURCE_NAMES = (
    "runs",
    "sessions",
    "turns",
    "memory",
    "skills",
    "connections",
    "permissions",
)
_RUN_DIRECTORY_RE = re.compile(r"run_[0-9a-f]{32}")
_TURN_FILE_RE = re.compile(r"turn_[0-9a-f]{32}\.json")
_SESSION_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.json")
_DELTA_FILE_RE = re.compile(r"delta_[A-Za-z0-9_-]{1,160}\.json")
_VOLATILE_MCP_KEYS = frozenset({"checkedAt", "updatedAt"})
_PERMISSION_REVISION_RE = re.compile(r"permissionrev_[0-9a-f]{32}")
_DIAGNOSTIC_MESSAGES = {
    "scan_unavailable": "Persisted change metadata is temporarily unavailable.",
    "scan_limit_reached": "Persisted change metadata exceeded the bounded scan limit.",
    "unsafe_symlink": "An unsafe persisted path was ignored.",
    "current_state_unavailable": "Current connection state is temporarily unavailable.",
    "workspace_scope_conservative": "Session change metadata uses conservative workspace scoping.",
    "permission_revision_unavailable": "Permission change state is temporarily unavailable.",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _platform_name() -> str:
    return os.name


def _iso_time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


@dataclass(slots=True)
class _Observation:
    facts: list[str] = field(default_factory=list)
    diagnostic_codes: set[str] = field(default_factory=set)
    status: str = "live"

    def add_diagnostic(self, code: str) -> None:
        self.diagnostic_codes.add(code)


@dataclass(slots=True)
class _Budget:
    remaining: int

    def consume(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


class DashboardChangeFeed:
    """Return safe equality markers for the Dashboard's existing authorities."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        data_dir: str | Path | None = None,
        clock: Callable[[], datetime] = _utc_now,
        mcp_current_state_loader: Callable[[], Mapping[str, object]] | None = None,
        permission_revision_loader: Callable[[], str] | None = None,
        max_scan_entries: int = 25_000,
        poll_after_ms: int = 2_000,
    ) -> None:
        if not isinstance(max_scan_entries, int) or isinstance(max_scan_entries, bool):
            raise ValueError("max_scan_entries must be a positive integer")
        if max_scan_entries < 1 or max_scan_entries > 100_000:
            raise ValueError("max_scan_entries must be between 1 and 100000")
        if not isinstance(poll_after_ms, int) or isinstance(poll_after_ms, bool):
            raise ValueError("poll_after_ms must be an integer")
        if poll_after_ms < 1_000 or poll_after_ms > 10_000:
            raise ValueError("poll_after_ms must be between 1000 and 10000")
        self.workspace = Path(workspace).expanduser().resolve()
        self.data_dir = Path(
            data_dir if data_dir is not None else MINI_CODE_DIR
        ).expanduser().resolve(strict=False)
        self.workspace_id = stable_workspace_id(self.workspace)
        self._clock = clock
        self._mcp_current_state_loader = mcp_current_state_loader
        self._permission_revision_loader = permission_revision_loader
        self._max_scan_entries = max_scan_entries
        self._poll_after_ms = poll_after_ms
        self._revision_salt = hashlib.sha256(
            ("minicode-change-feed-v2\0" + str(self.workspace)).encode("utf-8")
        ).hexdigest()

    def snapshot(self) -> dict[str, object]:
        """Return one deterministic, content-free, read-only change snapshot."""
        budget = _Budget(self._max_scan_entries)
        collectors = {
            "runs": self._collect_runs,
            "sessions": self._collect_sessions,
            "turns": self._collect_turns,
            "memory": self._collect_memory,
            "skills": self._collect_skills,
            "connections": self._collect_connections,
            "permissions": self._collect_permissions,
        }
        resources: dict[str, dict[str, str]] = {}
        diagnostics: list[dict[str, str]] = []
        for name in _RESOURCE_NAMES:
            observation = _Observation()
            try:
                collectors[name](observation, budget)
            except Exception:  # one observer must not poison other resources
                if name == "permissions":
                    observation.status = "error"
                    observation.add_diagnostic("permission_revision_unavailable")
                else:
                    observation.add_diagnostic("scan_unavailable")
            codes = sorted(observation.diagnostic_codes)
            status = observation.status
            if codes and status == "live":
                status = "partial"
            resources[name] = {
                "status": status,
                "revision": self._revision(name, status, codes, observation.facts),
            }
            diagnostics.extend(
                {
                    "resource": name,
                    "code": code,
                    "message": _DIAGNOSTIC_MESSAGES[code],
                }
                for code in codes
            )
        return {
            "schemaVersion": 2,
            "generatedAt": _iso_time(self._clock()),
            "mode": "read-only",
            "pollAfterMs": self._poll_after_ms,
            "resources": resources,
            "diagnostics": diagnostics,
        }

    def _revision(
        self,
        resource: str,
        status: str,
        diagnostic_codes: list[str],
        facts: list[str],
    ) -> str:
        digest = hashlib.sha256()
        stable_facts = [] if "scan_limit_reached" in diagnostic_codes else facts
        for value in (
            "schema:2",
            f"salt:{self._revision_salt}",
            f"resource:{resource}",
            f"status:{status}",
            *(f"diagnostic:{code}" for code in diagnostic_codes),
            *sorted(stable_facts),
        ):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        return "rev_" + digest.hexdigest()

    @staticmethod
    def _within(path: Path, anchor: Path) -> bool:
        try:
            path.relative_to(anchor)
        except ValueError:
            return False
        return True

    def _add_stat(
        self,
        observation: _Observation,
        budget: _Budget,
        *,
        path: Path,
        anchor: Path,
        key: str,
        kind: str | None = None,
        ignore_kind_mismatch: bool = False,
    ) -> os.stat_result | None:
        if not budget.consume():
            observation.add_diagnostic("scan_limit_reached")
            return None
        try:
            raw = path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            observation.add_diagnostic("scan_unavailable")
            return None
        resolved_anchor = anchor.resolve(strict=False)
        if stat.S_ISLNK(raw.st_mode):
            observation.add_diagnostic("unsafe_symlink")
            return None
        try:
            parent = path.parent.resolve(strict=False)
        except (OSError, RuntimeError):
            observation.add_diagnostic("unsafe_symlink")
            return None
        if not self._within(parent, resolved_anchor):
            observation.add_diagnostic("unsafe_symlink")
            return None
        if kind == "file" and not stat.S_ISREG(raw.st_mode):
            if ignore_kind_mismatch:
                return None
            observation.add_diagnostic("scan_unavailable")
            return None
        if kind == "directory" and not stat.S_ISDIR(raw.st_mode):
            if ignore_kind_mismatch:
                return None
            observation.add_diagnostic("scan_unavailable")
            return None
        if kind != "directory":
            type_code = "f" if stat.S_ISREG(raw.st_mode) else "o"
            observation.facts.append(
                f"{key}:{type_code}:{raw.st_size}:{raw.st_mtime_ns}:{raw.st_ctime_ns}"
            )
        return raw

    def _safe_children(
        self,
        observation: _Observation,
        budget: _Budget,
        *,
        root: Path,
        anchor: Path,
        key: str,
    ) -> list[Path]:
        root_stat = self._add_stat(
            observation,
            budget,
            path=root,
            anchor=anchor,
            key=key,
            kind="directory",
        )
        if root_stat is None or not stat.S_ISDIR(root_stat.st_mode):
            return []
        children: list[Path] = []
        descriptor: int | None = None
        try:
            if _platform_name() == "posix":
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(root, flags)
                scan_target: int | Path = descriptor
            else:
                if os.path.normcase(os.path.realpath(root)) != os.path.normcase(
                    os.path.abspath(root)
                ):
                    observation.add_diagnostic("unsafe_symlink")
                    return []
                scan_target = root
            with os.scandir(scan_target) as entries:
                for entry in entries:
                    if not budget.consume():
                        observation.add_diagnostic("scan_limit_reached")
                        return []
                    children.append(root / entry.name)
            current_root_stat = os.lstat(root)
            if (
                not stat.S_ISDIR(current_root_stat.st_mode)
                or current_root_stat.st_dev != root_stat.st_dev
                or current_root_stat.st_ino != root_stat.st_ino
            ):
                observation.add_diagnostic("scan_unavailable")
                return []
        except OSError:
            observation.add_diagnostic("scan_unavailable")
            return []
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return sorted(children, key=lambda item: item.name)

    def _collect_runs(self, observation: _Observation, budget: _Budget) -> None:
        root = (
            self.data_dir
            / "dashboard"
            / "workspaces"
            / self.workspace_id
            / "runs"
        )
        self._add_stat(
            observation,
            budget,
            path=root / "index.json",
            anchor=self.data_dir,
            key="index",
            kind="file",
        )
        for child in self._safe_children(
            observation, budget, root=root, anchor=self.data_dir, key="root"
        ):
            if not _RUN_DIRECTORY_RE.fullmatch(child.name):
                continue
            directory_key = hashlib.sha256(child.name.encode()).hexdigest()
            directory_stat = self._add_stat(
                observation,
                budget,
                path=child,
                anchor=self.data_dir,
                key=f"run:{directory_key}",
                kind="directory",
            )
            if directory_stat is None:
                continue
            for filename in ("metadata.json", "events.ndjson"):
                self._add_stat(
                    observation,
                    budget,
                    path=child / filename,
                    anchor=self.data_dir,
                    key=f"run:{directory_key}:{filename}",
                    kind="file",
                )

    def _collect_sessions(self, observation: _Observation, budget: _Budget) -> None:
        facts_before = len(observation.facts)
        self._add_stat(
            observation,
            budget,
            path=self.data_dir / "sessions_index.json",
            anchor=self.data_dir,
            key="index",
            kind="file",
        )
        sessions_root = self.data_dir / "sessions"
        for child in self._safe_children(
            observation,
            budget,
            root=sessions_root,
            anchor=self.data_dir,
            key="root",
        ):
            if child.name == "deltas":
                for delta_dir in self._safe_children(
                    observation,
                    budget,
                    root=child,
                    anchor=self.data_dir,
                    key="deltas",
                ):
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", delta_dir.name):
                        continue
                    session_key = hashlib.sha256(delta_dir.name.encode()).hexdigest()
                    for delta in self._safe_children(
                        observation,
                        budget,
                        root=delta_dir,
                        anchor=self.data_dir,
                        key=f"delta-dir:{session_key}",
                    ):
                        if _DELTA_FILE_RE.fullmatch(delta.name):
                            delta_key = hashlib.sha256(delta.name.encode()).hexdigest()
                            self._add_stat(
                                observation,
                                budget,
                                path=delta,
                                anchor=self.data_dir,
                                key=f"delta:{session_key}:{delta_key}",
                                kind="file",
                            )
            elif _SESSION_FILE_RE.fullmatch(child.name):
                session_key = hashlib.sha256(child.name.encode()).hexdigest()
                self._add_stat(
                    observation,
                    budget,
                    path=child,
                    anchor=self.data_dir,
                    key=f"session:{session_key}",
                    kind="file",
                )
        if len(observation.facts) > facts_before:
            observation.add_diagnostic("workspace_scope_conservative")

    def _collect_turns(self, observation: _Observation, budget: _Budget) -> None:
        root = (
            self.data_dir
            / "dashboard"
            / "workspaces"
            / self.workspace_id
            / "turns"
        )
        for child in self._safe_children(
            observation, budget, root=root, anchor=self.data_dir, key="root"
        ):
            if _TURN_FILE_RE.fullmatch(child.name):
                turn_key = hashlib.sha256(child.name.encode()).hexdigest()
                self._add_stat(
                    observation,
                    budget,
                    path=child,
                    anchor=self.data_dir,
                    key=f"turn:{turn_key}",
                    kind="file",
                )

    def _collect_memory(self, observation: _Observation, budget: _Budget) -> None:
        roots = (
            (self.data_dir / "memory", self.data_dir, "user"),
            (self.workspace / ".mini-code-memory", self.workspace, "project"),
            (
                self.workspace / ".mini-code-memory-local",
                self.workspace,
                "local",
            ),
        )
        for root, anchor, scope in roots:
            for filename in ("memory.json", "MEMORY.md", "approval_audit.json"):
                self._add_stat(
                    observation,
                    budget,
                    path=root / filename,
                    anchor=anchor,
                    key=f"{scope}:{filename}",
                    kind="file",
                )

    def _collect_skills(self, observation: _Observation, budget: _Budget) -> None:
        roots = (
            (self.workspace / ".mini-code" / "skills", self.workspace, "project"),
            (self.data_dir / "skills", self.data_dir, "user"),
            (self.workspace / ".claude" / "skills", self.workspace, "compat-project"),
            (self.data_dir.parent / ".claude" / "skills", self.data_dir.parent, "compat-user"),
        )
        for root, anchor, source in roots:
            for directory in self._safe_children(
                observation,
                budget,
                root=root,
                anchor=anchor,
                key=f"{source}:root",
            ):
                directory_key = hashlib.sha256(directory.name.encode()).hexdigest()
                directory_stat = self._add_stat(
                    observation,
                    budget,
                    path=directory,
                    anchor=anchor,
                    key=f"{source}:dir:{directory_key}",
                    kind="directory",
                    ignore_kind_mismatch=True,
                )
                if directory_stat is None:
                    continue
                summary_present = False
                for filename in ("SKILL.md", "SKILL_DIR.md"):
                    value = self._add_stat(
                        observation,
                        budget,
                        path=directory / filename,
                        anchor=anchor,
                        key=f"{source}:dir:{directory_key}:{filename}",
                        kind="file",
                    )
                    summary_present = summary_present or (
                        filename == "SKILL_DIR.md" and value is not None
                    )
                if not summary_present:
                    continue
                for nested in self._safe_children(
                    observation,
                    budget,
                    root=directory,
                    anchor=anchor,
                    key=f"{source}:nested-root:{directory_key}",
                ):
                    nested_key = hashlib.sha256(nested.name.encode()).hexdigest()
                    nested_stat = self._add_stat(
                        observation,
                        budget,
                        path=nested,
                        anchor=anchor,
                        key=f"{source}:nested:{directory_key}:{nested_key}",
                        kind="directory",
                        ignore_kind_mismatch=True,
                    )
                    if nested_stat is not None:
                        self._add_stat(
                            observation,
                            budget,
                            path=nested / "SKILL.md",
                            anchor=anchor,
                            key=f"{source}:nested:{directory_key}:{nested_key}:SKILL.md",
                            kind="file",
                        )

    def _collect_connections(
        self, observation: _Observation, budget: _Budget
    ) -> None:
        for path, anchor, key in (
            (self.data_dir / "mcp.json", self.data_dir, "user"),
            (self.workspace / ".mcp.json", self.workspace, "project"),
        ):
            self._add_stat(
                observation,
                budget,
                path=path,
                anchor=anchor,
                key=key,
                kind="file",
            )
        if self._mcp_current_state_loader is None:
            observation.facts.append("current:none")
            return
        try:
            payload = self._mcp_current_state_loader()
            normalized = normalize_mcp_current_state_snapshot(payload)
        except BaseException:  # observer faults must stay resource-local
            normalized = None
        if normalized is None:
            observation.add_diagnostic("current_state_unavailable")
            return
        stable = self._without_volatile_mcp_fields(normalized)
        observation.facts.append(
            "current:"
            + hashlib.sha256(
                json.dumps(
                    stable,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )

    def _collect_permissions(
        self, observation: _Observation, _budget: _Budget
    ) -> None:
        if self._permission_revision_loader is None:
            observation.status = "unavailable"
            return
        try:
            revision = self._permission_revision_loader()
        except BaseException:  # authority observation faults stay resource-local
            observation.status = "error"
            observation.add_diagnostic("permission_revision_unavailable")
            return
        if (
            not isinstance(revision, str)
            or _PERMISSION_REVISION_RE.fullmatch(revision) is None
        ):
            observation.status = "error"
            observation.add_diagnostic("permission_revision_unavailable")
            return
        observation.facts.append("authority:" + revision)

    def _without_volatile_mcp_fields(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: self._without_volatile_mcp_fields(item)
                for key, item in sorted(value.items())
                if key not in _VOLATILE_MCP_KEYS
            }
        if isinstance(value, list):
            return [self._without_volatile_mcp_fields(item) for item in value]
        return value


__all__ = ["DashboardChangeFeed"]
