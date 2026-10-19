"""Private, bounded diagnostic data; never a verification/promotion capability."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

MAX_FILE_BYTES = 256 * 1024
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_FILES = 128
MAX_VISITED_ENTRIES = 512
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", re.S)
_DATA_URL = re.compile(r"data:[^\s\"']{0,100};base64,[A-Za-z0-9+/=]+", re.I)
_OPAQUE_BASE64 = re.compile(r"[A-Za-z0-9+/=]{256,}")
_SECRET_KEYS = re.compile(r"authorization|password|secret|credential|api.?key|access.?token|refresh.?token|private.?key", re.I)
_OMITTED_KEYS = {"image", "image_url", "image_data", "audio", "audio_url", "base64", "data", "blob"}
_JSON_FIELD_KEY = re.compile(r'(?<!\\)(?P<key>"(?:\\.|[^"\\])*")\s*:\s*', re.S)
_JSON_STRING_VALUE = re.compile(r'"(?:\\.|[^"\\])*"', re.S)
_PRIVATE_PATHS = {".git", ".aws", ".ssh", ".mini-code", ".codex", "__pycache__", ".pytest_cache", "node_modules", ".venv"}


def _redact_json_secret_fields(text: str) -> str:
    """Redact complete JSON values embedded in logs without rewriting neighbors."""
    from minicode.memory import _is_sensitive_persistence_key

    decoder = json.JSONDecoder()
    sections: list[str] = []
    cursor = 0
    for match in _JSON_FIELD_KEY.finditer(text):
        if match.start() < cursor:
            continue  # An outer credential object already covers this field.
        try:
            key = json.loads(match.group("key"))
        except ValueError:
            continue
        if not _is_sensitive_persistence_key(key):
            continue
        replacement = '"[REDACTED]"'
        try:
            # The JSON decoder owns escaped quotes/newlines and nested value
            # boundaries; a regex alone must not consume neighboring fields.
            _, end = decoder.raw_decode(text, match.end())
        except (ValueError, RecursionError):
            # read_file may have truncated the value before this boundary.
            # Only an unclosed quoted sensitive value consumes the unknown tail;
            # expressions such as {"password": config.password} stay untouched.
            if not text.startswith('"', match.end()) or _JSON_STRING_VALUE.match(text, match.end()):
                continue
            end = len(text)
            replacement = '"[REDACTED: TRUNCATED JSON STRING]"'
        sections.append(text[cursor:match.end()])
        sections.append(replacement)
        cursor = end
    sections.append(text[cursor:])
    return "".join(sections)


def redact_diagnostic_text(value: str, *, max_chars: int) -> str:
    from minicode.memory import _redact_persistence_secrets

    # Apply whole-value redaction before bounding, so a truncated key cannot
    # evade the credential recognizer. No image/audio/base64 bytes are saved.
    text = _redact_json_secret_fields(value)
    text = _PRIVATE_KEY.sub("[PRIVATE KEY OMITTED]", text)
    text = _DATA_URL.sub("[MEDIA OMITTED]", text)
    text = _OPAQUE_BASE64.sub("[OPAQUE DATA OMITTED]", text)
    text = _redact_persistence_secrets(text)
    if len(text) <= max_chars:
        return text
    marker = "\n...[diagnostic truncated]...\n"
    half = max(0, (max_chars - len(marker)) // 2)
    return text[:half] + marker + text[-half:]


def sanitize_diagnostic_value(value: object, *, depth: int = 0) -> object:
    if depth > 4:
        return "[DEPTH OMITTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_diagnostic_text(value, max_chars=8000)
    if isinstance(value, dict):
        result = {}
        for key, nested in list(value.items())[:40]:
            if not isinstance(key, str):
                continue
            if _SECRET_KEYS.search(key):
                result[key[:80]] = "[REDACTED]"
            elif key.lower() in _OMITTED_KEYS:
                result[key[:80]] = "[MEDIA/BINARY OMITTED]"
            else:
                result[key[:80]] = sanitize_diagnostic_value(nested, depth=depth + 1)
        return result
    if isinstance(value, (tuple, list)):
        return [sanitize_diagnostic_value(item, depth=depth + 1) for item in value[:40]]
    return "[UNSUPPORTED VALUE OMITTED]"


def open_private_directory(root: Path, *parts: str) -> int:
    """Open a trusted journal root and walk newly created children by dirfd."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            if not part or part in {".", ".."} or "/" in part or "\\" in part:
                raise ValueError("unsafe diagnostic directory")
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def capture_scoped_files(root: Path, scopes: tuple[str, ...]) -> dict[str, object]:
    """Capture only bounded regular UTF-8 files without following any symlink.

    No subprocess, provider, approval or operation-deadline checkpoint runs:
    this is also usable after the execution budget has already expired.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    root_fd = os.open(root, flags | getattr(os, "O_DIRECTORY", 0))
    files: dict[str, dict[str, object]] = {}
    remaining = MAX_CAPTURE_BYTES
    visited = 0
    incomplete = False
    deadline = time.monotonic() + 2.0

    def visit(parent_fd: int, name: str, relative: str) -> None:
        nonlocal remaining, visited, incomplete
        if relative in files:
            return
        visited += 1
        if visited > MAX_VISITED_ENTRIES or time.monotonic() >= deadline or len(files) >= MAX_CAPTURE_FILES:
            incomplete = True
            return
        if name in _PRIVATE_PATHS or name.startswith(".env") or name.lower().endswith((".pem", ".key", ".p12", ".pfx")):
            incomplete = True
            return
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                files[relative] = {"path": relative, "omitted": "symlink"}
                return
            if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                files[relative] = {"path": relative, "omitted": "non_regular"}
                return
            opened = os.open(name, flags, dir_fd=parent_fd)
            try:
                current = os.fstat(opened)
                if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise OSError("candidate changed while opening")
                if stat.S_ISDIR(current.st_mode):
                    # Bound enumeration itself, not just later file reads.
                    with os.scandir(opened) as entries:
                        for entry in entries:
                            if visited >= MAX_VISITED_ENTRIES or len(files) >= MAX_CAPTURE_FILES or time.monotonic() >= deadline:
                                incomplete = True
                                break
                            visit(opened, entry.name, f"{relative}/{entry.name}".removeprefix("./"))
                    return
                if current.st_nlink != 1:
                    files[relative] = {"path": relative, "omitted": "hardlink"}
                    return
                if current.st_size > MAX_FILE_BYTES or current.st_size > remaining:
                    files[relative] = {"path": relative, "omitted": "size_limit", "byteCount": current.st_size}
                    return
                data = bytearray()
                while len(data) <= current.st_size:
                    chunk = os.read(opened, min(64 * 1024, current.st_size + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                after = os.fstat(opened)
                if len(data) != current.st_size or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (current.st_size, current.st_mtime_ns, current.st_ctime_ns):
                    raise OSError("candidate changed while reading")
                remaining -= len(data)
                try:
                    content = data.decode("utf-8")
                    if "\x00" in content:
                        raise UnicodeError("binary file")
                except UnicodeError:
                    files[relative] = {"path": relative, "omitted": "binary", "byteCount": len(data)}
                    return
                safe = redact_diagnostic_text(content, max_chars=MAX_FILE_BYTES)
                files[relative] = {"path": relative, "content": safe, "redacted": safe != content,
                                   "sha256": hashlib.sha256(data).hexdigest(), "byteCount": len(data)}
            finally:
                os.close(opened)
        except FileNotFoundError:
            return
        except OSError:
            files[relative] = {"path": relative, "omitted": "unavailable_or_changed"}
            incomplete = True

    try:
        for scope in scopes[:MAX_CAPTURE_FILES]:
            components = Path(scope).parts
            if Path(scope).is_absolute() or ".." in components:
                raise ValueError("invalid diagnostic scope")
            if any(part in _PRIVATE_PATHS or part.startswith(".env") for part in components):
                incomplete = True
                continue
            parent = os.dup(root_fd)
            try:
                for part in components[:-1]:
                    child = os.open(part, flags | getattr(os, "O_DIRECTORY", 0), dir_fd=parent)
                    os.close(parent)
                    parent = child
                visit(parent, components[-1] if components else ".", scope)
            except FileNotFoundError:
                continue
            except OSError:
                files[scope] = {"path": scope, "omitted": "unsafe_ancestor"}
                incomplete = True
            finally:
                os.close(parent)
        return {"files": files, "complete": not incomplete and len(scopes) <= MAX_CAPTURE_FILES and not any("omitted" in item for item in files.values())}
    finally:
        os.close(root_fd)


def encode_diagnostic_document(document: object) -> bytes:
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
