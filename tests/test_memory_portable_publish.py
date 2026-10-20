"""Portable memory publication used on Windows."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from minicode.memory_authority_publisher import (
    MemoryAuthorityPublisher,
    _replace_published_file,
)


def test_portable_publish_closes_the_temporary_file_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "memory.json"
    order: list[tuple[str, int | None]] = []
    real_mkstemp = tempfile.mkstemp
    real_close = os.close
    real_replace = os.replace
    opened: list[int] = []

    def spy_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        opened.append(fd)
        order.append(("open", fd))
        return fd, path

    def spy_close(fd):
        order.append(("close", fd))
        return real_close(fd)

    def spy_replace(source, destination):
        order.append(("replace", opened[-1] if opened else None))
        return real_replace(source, destination)

    monkeypatch.setattr(tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(os, "close", spy_close)
    monkeypatch.setattr(os, "replace", spy_replace)

    MemoryAuthorityPublisher._publish_portable(target, b"{\"ok\": true}")

    fd = opened[-1]
    assert ("close", fd) in order
    assert order.index(("close", fd)) < order.index(("replace", fd))
    assert target.read_bytes() == b"{\"ok\": true}"


def test_windows_replace_retries_a_sharing_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "incoming"
    source.write_text("published")
    target = tmp_path / "memory.json"
    calls = {"count": 0}
    real_replace = os.replace

    def locked_once(source_path, destination):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError(13, "Access is denied", source_path)
        return real_replace(source_path, destination)

    monkeypatch.setattr(
        "minicode.memory_authority_publisher.os.name",
        "nt",
    )
    monkeypatch.setattr(
        "minicode.memory_authority_publisher.os.replace",
        locked_once,
    )
    monkeypatch.setattr(
        "minicode.memory_authority_publisher.time.sleep",
        lambda _delay: None,
    )

    _replace_published_file(os.fspath(source), os.fspath(target))

    assert calls["count"] == 3
    assert target.read_text() == "published"


def test_replace_does_not_retry_permission_error_off_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "incoming"
    source.write_text("published")
    calls = {"count": 0}

    def locked(source_path, _destination):
        calls["count"] += 1
        raise PermissionError(13, "Access is denied", source_path)

    monkeypatch.setattr(
        "minicode.memory_authority_publisher.os.name",
        "posix",
    )
    monkeypatch.setattr(
        "minicode.memory_authority_publisher.os.replace",
        locked,
    )

    with pytest.raises(PermissionError):
        _replace_published_file(os.fspath(source), os.fspath(tmp_path / "memory.json"))
    assert calls["count"] == 1
