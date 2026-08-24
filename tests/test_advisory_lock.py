from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from minicode import advisory_lock as lock_module
from minicode.advisory_lock import WINDOWS_LOCK_SENTINEL
from minicode.deletion_store import DeletionLedger
from minicode.memory_store import MemoryStoreCoordinator
from minicode.session_store import session_store_transaction


class _FakeMsvcrt:
    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self, *, busy: bool = False) -> None:
        self.busy = busy
        self.calls: list[tuple[int, int, int, int]] = []

    def locking(self, file_descriptor: int, mode: int, size: int) -> None:
        offset = os.lseek(file_descriptor, 0, os.SEEK_CUR)
        self.calls.append((file_descriptor, mode, size, offset))
        if self.busy and mode == self.LK_NBLCK:
            raise OSError(errno.EACCES, "fixture contention")


def test_windows_backend_uses_one_byte_nonblocking_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "portable.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    backend = _FakeMsvcrt()
    monkeypatch.setattr(lock_module, "_platform_name", lambda: "nt")
    monkeypatch.setattr(lock_module, "msvcrt", backend)
    try:
        lock_module.prepare_advisory_lock_file(descriptor)
        assert lock_path.read_bytes() == WINDOWS_LOCK_SENTINEL
        assert lock_module.acquire_advisory_lock(descriptor) is True
        lock_module.release_advisory_lock(descriptor)
    finally:
        os.close(descriptor)

    assert [call[1:] for call in backend.calls] == [
        (backend.LK_NBLCK, 1, 0),
        (backend.LK_UNLCK, 1, 0),
    ]


def test_windows_backend_reports_contention_without_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "busy.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    backend = _FakeMsvcrt(busy=True)
    monkeypatch.setattr(lock_module, "_platform_name", lambda: "nt")
    monkeypatch.setattr(lock_module, "msvcrt", backend)
    try:
        lock_module.prepare_advisory_lock_file(descriptor)
        assert lock_module.acquire_advisory_lock(descriptor) is False
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("store_kind", ["memory", "session", "deletion"])
def test_durable_store_lock_uses_platform_contract(
    tmp_path: Path,
    store_kind: str,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if store_kind == "memory":
        transaction = MemoryStoreCoordinator(data_dir).transaction()
        lock_path = data_dir / "memory-store.lock"
    elif store_kind == "session":
        transaction = session_store_transaction(data_dir)
        lock_path = data_dir / "session-store.lock"
    else:
        ledger = DeletionLedger(workspace, data_dir=data_dir)
        transaction = ledger.coordination()
        lock_path = ledger._lock_path

    with transaction:
        info = lock_path.lstat()
        expected_payload = WINDOWS_LOCK_SENTINEL if os.name == "nt" else b""
        assert stat.S_ISREG(info.st_mode)
        assert lock_path.read_bytes() == expected_payload
        if os.name == "posix":
            assert stat.S_IMODE(info.st_mode) == 0o600

    assert lock_path.read_bytes() == expected_payload
