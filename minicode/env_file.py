"""Minimal ``.env`` file loading (no third-party dependency).

The repository ships a ``.env.example`` describing its environment knobs,
but nothing parsed that file — values only worked after a manual
``source .env``. This loader closes that gap for features that read
workspace-local configuration (currently the skill embedding settings).

Semantics:
- ``KEY=VALUE`` lines; ``#`` comments and blank lines ignored;
- surrounding quotes on the value are stripped; a leading ``export `` is
  tolerated;
- ``read_env_files`` merges in order (later files win) without touching the
  process environment;
- ``apply_env_file`` imports values into ``os.environ`` only for keys not
  already set, so real environment variables always take precedence.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 256 * 1024
_MAX_LINES = 2_000


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse one ``.env`` file into a dict; unreadable files yield ``{}``."""
    try:
        file_path = Path(path)
        if not file_path.is_file() or file_path.stat().st_size > _MAX_FILE_BYTES:
            return {}
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    values: dict[str, str] = {}
    for line in text.splitlines()[:_MAX_LINES]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key or any(character.isspace() for character in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def read_env_files(paths: list[str | Path]) -> dict[str, str]:
    """Merge several ``.env`` files in order; later files override earlier."""
    merged: dict[str, str] = {}
    for path in paths:
        merged.update(parse_env_file(path))
    return merged


def apply_env_file(paths: list[str | Path], *, override: bool = False) -> dict[str, str]:
    """Import ``.env`` values into the process environment.

    Existing variables are preserved unless ``override`` is set, so a real
    ``export`` always beats the file.
    """
    applied: dict[str, str] = {}
    for key, value in read_env_files(paths).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    if applied:
        logger.debug("Loaded %d setting(s) from env file(s)", len(applied))
    return applied


__all__ = ["apply_env_file", "parse_env_file", "read_env_files"]
