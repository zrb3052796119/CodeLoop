"""Test-only process HOME isolation and formal-state guards."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping


TEST_MODEL = "claude-sonnet-4-20250514"
TEST_AUTH_TOKEN = "pytest-mock-auth-not-a-secret"
_CREDENTIAL_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENROUTER_API_KEY",
    "CUSTOM_API_KEY",
    "CUSTOM_API_BASE_URL",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_paths(paths: list[Path] | tuple[Path, ...]) -> dict[str, dict[str, Any]]:
    """Capture only existence, hash, size, and mtime for protected files."""
    snapshot: dict[str, dict[str, Any]] = {}
    for raw_path in paths:
        path = Path(raw_path).expanduser().absolute()
        if not path.exists():
            snapshot[str(path)] = {"exists": False, "sha256": None, "size": None, "mtime_ns": None}
            continue
        metadata = path.stat()
        snapshot[str(path)] = {
            "exists": True,
            "sha256": _sha256(path),
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
        }
    return snapshot


def compare_snapshots(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return privacy-safe path/change summaries without file content."""
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path, {"exists": False})
        new = after.get(path, {"exists": False})
        old_exists = bool(old.get("exists"))
        new_exists = bool(new.get("exists"))
        kinds: list[str] = []
        if old_exists != new_exists:
            kinds.append("created" if new_exists else "deleted")
        elif old_exists:
            if old.get("sha256") != new.get("sha256"):
                kinds.append("content_hash")
            if old.get("size") != new.get("size"):
                kinds.append("size")
            if old.get("mtime_ns") != new.get("mtime_ns"):
                kinds.append("mtime_ns")
        if kinds:
            changes.append({"path": path, "changes": kinds})
    return changes


class FormalStateChanged(RuntimeError):
    """Raised when a protected real-home file changes during pytest."""


class GlobalStateGuard:
    """Hash/stat guard that detects formal-state changes and never rolls back."""

    def __init__(
        self,
        protected_paths: list[Path] | tuple[Path, ...],
        *,
        protected_roots: list[Path] | tuple[Path, ...] = (),
    ) -> None:
        self._protected_paths = tuple(Path(path) for path in protected_paths)
        self._protected_roots = tuple(Path(path) for path in protected_roots)
        self._start: dict[str, dict[str, Any]] | None = None

    @property
    def start_snapshot(self) -> dict[str, dict[str, Any]] | None:
        return self._start

    def _capture(self, include_paths: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
        paths = set(self._protected_paths)
        paths.update(Path(path) for path in include_paths)
        for root in self._protected_roots:
            if root.exists():
                paths.update(path for path in root.rglob("*") if path.is_file())
        return snapshot_paths(tuple(sorted(paths)))

    def capture_start(self) -> None:
        self._start = self._capture()

    def assert_unchanged(self) -> None:
        if self._start is None:
            raise RuntimeError("formal-state guard start snapshot was not captured")
        changes = compare_snapshots(self._start, self._capture(self._start))
        if changes:
            summary = "; ".join(
                f"{item['path']} ({','.join(item['changes'])})" for item in changes
            )
            raise FormalStateChanged(f"protected real-home state changed: {summary}")


def assert_minicode_not_preloaded(
    modules: Mapping[str, object],
    *,
    real_home: Path,
) -> None:
    """Fail before collection if MiniCode observed the real HOME already."""
    preloaded = sorted(name for name in modules if name == "minicode" or name.startswith("minicode."))
    if preloaded:
        raise RuntimeError(
            "minicode modules were loaded before pytest HOME isolation: "
            f"{', '.join(preloaded)}; protected real home: {real_home}"
        )


def _safe_worker_name(worker_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", worker_id) or "main"


@dataclass
class IsolatedTestHome:
    """One process/xdist-worker HOME whose global state is reset per test."""

    real_home: Path
    home: Path
    settings_path: Path
    environ: MutableMapping[str, str]

    @classmethod
    def create(
        cls,
        *,
        real_home: Path,
        environ: MutableMapping[str, str],
        temp_root: Path | None = None,
        process_id: int | None = None,
        worker_id: str | None = None,
    ) -> "IsolatedTestHome":
        pid = process_id if process_id is not None else os.getpid()
        worker = _safe_worker_name(worker_id or environ.get("PYTEST_XDIST_WORKER", "main"))
        if temp_root is not None:
            Path(temp_root).mkdir(parents=True, exist_ok=True)
        home = Path(
            tempfile.mkdtemp(
                prefix=f"minicode-pytest-{pid}-{worker}-",
                dir=str(temp_root) if temp_root is not None else None,
            )
        ).resolve()
        os.chmod(home, 0o700)

        for name in _CREDENTIAL_ENV_NAMES:
            environ.pop(name, None)
        environ.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "MINI_CODE_MODEL_MODE": "mock",
                "MINI_CODE_SHOW_GUIDE": "0",
                "MINI_CODE_TOOL_PROFILE": "core",
                "MINI_CODE_MODEL": TEST_MODEL,
                "ANTHROPIC_AUTH_TOKEN": TEST_AUTH_TOKEN,
                "MINICODE_PYTEST_REAL_HOME": str(real_home.resolve()),
                "MINICODE_PYTEST_ISOLATED_HOME": str(home),
            }
        )
        isolation = cls(
            real_home=real_home.resolve(),
            home=home,
            settings_path=home / ".mini-code" / "settings.json",
            environ=environ,
        )
        isolation.reset()
        return isolation

    def reset(self) -> None:
        """Restore a deterministic empty MiniCode home while preserving config."""
        if self.home.exists():
            for child in self.home.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            self.home.mkdir(parents=True, mode=0o700)
        os.chmod(self.home, 0o700)
        (self.home / ".config").mkdir(mode=0o700)
        self.settings_path.parent.mkdir(mode=0o700)
        self.settings_path.write_text(
            json.dumps({"model": TEST_MODEL, "toolProfile": "core"}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.settings_path, 0o600)

    def cleanup(self) -> None:
        if self.home.exists():
            shutil.rmtree(self.home)
