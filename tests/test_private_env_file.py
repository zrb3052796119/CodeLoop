from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.env_file as env_module
from minicode.env_file import (
    parse_env_file,
    read_private_env_file,
    update_private_env_file,
)


def _private_path(tmp_path: Path) -> Path:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    if os.name == "posix":
        directory.chmod(0o700)
    return directory / ".env"


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    if os.name == "posix":
        path.chmod(0o600)


def test_private_reader_missing_returns_empty_without_mutating_environment(
    tmp_path: Path,
) -> None:
    before = dict(os.environ)

    assert read_private_env_file(tmp_path / "missing" / ".env") == {}
    assert dict(os.environ) == before


def test_private_update_round_trips_values_and_uses_private_modes(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "new-private-directory" / ".env"
    values = {
        "API_KEY": "secret=#=value",
        "EMPTY": "",
        "LINE_SEPARATOR": "before\u2028after",
        "QUOTES": "both '\" and a \\ slash",
        "SPACES": "  keep these spaces  ",
        "UNICODE": "密钥值",
    }
    before = dict(os.environ)

    assert update_private_env_file(destination, values) == values
    assert read_private_env_file(destination) == values
    assert dict(os.environ) == before
    if os.name == "posix":
        assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_private_update_filters_allowed_keys_but_preserves_other_settings(
    tmp_path: Path,
) -> None:
    destination = _private_path(tmp_path)
    update_private_env_file(
        destination,
        {"ALLOWED": "old", "OTHER_COMPONENT": "preserve-me"},
    )

    result = update_private_env_file(
        destination,
        {"ALLOWED": "new"},
        allowed_keys={"ALLOWED"},
    )

    assert result == {"ALLOWED": "new"}
    assert read_private_env_file(destination, allowed_keys={"ALLOWED"}) == result
    assert read_private_env_file(destination) == {
        "ALLOWED": "new",
        "OTHER_COMPONENT": "preserve-me",
    }
    with pytest.raises(RuntimeError, match="private_env_key_not_allowed"):
        update_private_env_file(
            destination,
            {"OTHER_COMPONENT": "replace"},
            allowed_keys={"ALLOWED"},
        )


def test_private_update_none_deletes_a_key(tmp_path: Path) -> None:
    destination = _private_path(tmp_path)
    update_private_env_file(destination, {"KEEP": "yes", "REMOVE": "yes"})

    assert update_private_env_file(destination, {"REMOVE": None}) == {"KEEP": "yes"}
    assert read_private_env_file(destination) == {"KEEP": "yes"}


def test_private_reader_and_update_reject_file_symlink(tmp_path: Path) -> None:
    destination = _private_path(tmp_path)
    target = destination.parent / "target"
    _write_private(target, b"API_KEY=secret\n")
    try:
        destination.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(RuntimeError, match="private_env_symlink"):
        read_private_env_file(destination)
    with pytest.raises(RuntimeError, match="private_env_symlink"):
        update_private_env_file(destination, {"API_KEY": "replacement"})


def test_private_reader_rejects_non_regular_file(tmp_path: Path) -> None:
    destination = _private_path(tmp_path)
    destination.mkdir(mode=0o700)

    with pytest.raises(RuntimeError, match="private_env_not_regular"):
        read_private_env_file(destination)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_private_reader_rejects_group_or_other_file_permissions(tmp_path: Path) -> None:
    destination = _private_path(tmp_path)
    _write_private(destination, b"API_KEY=secret\n")
    destination.chmod(0o640)

    with pytest.raises(RuntimeError, match="private_env_permissions_unsafe"):
        read_private_env_file(destination)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_private_reader_rejects_unsafe_parent_permissions(tmp_path: Path) -> None:
    destination = _private_path(tmp_path)
    _write_private(destination, b"API_KEY=secret\n")
    destination.parent.chmod(0o750)

    with pytest.raises(
        RuntimeError,
        match="private_env_directory_permissions_unsafe",
    ):
        read_private_env_file(destination)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_private_reader_rejects_file_owned_by_another_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = _private_path(tmp_path)
    _write_private(destination, b"API_KEY=secret\n")
    real_lstat = env_module.os.lstat
    metadata = real_lstat(destination)

    def fake_lstat(candidate: str | os.PathLike[str]) -> os.stat_result | SimpleNamespace:
        if Path(candidate) == destination:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_size=metadata.st_size,
                st_uid=metadata.st_uid + 1,
            )
        return real_lstat(candidate)

    monkeypatch.setattr(env_module.os, "lstat", fake_lstat)

    with pytest.raises(RuntimeError, match="private_env_owner_unsafe"):
        read_private_env_file(destination)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"API_KEY=\xff\n", "private_env_utf8_invalid"),
        (b"1INVALID=value\n", "private_env_key_invalid"),
        (b"DUPLICATE=one\nDUPLICATE=two\n", "private_env_duplicate_key"),
        (b"MISSING_EQUALS\n", "private_env_line_invalid"),
        (b'BROKEN_QUOTE="value\n', "private_env_value_quote_invalid"),
        (b"NUL=before\x00after\n", "private_env_value_invalid"),
    ],
)
def test_private_reader_rejects_malformed_content(
    tmp_path: Path,
    payload: bytes,
    reason: str,
) -> None:
    destination = _private_path(tmp_path)
    _write_private(destination, payload)

    with pytest.raises(RuntimeError, match=reason):
        read_private_env_file(destination)


def test_private_reader_enforces_file_and_line_limits(tmp_path: Path) -> None:
    destination = _private_path(tmp_path)
    _write_private(destination, b"A=" + (b"x" * (256 * 1024)))
    with pytest.raises(RuntimeError, match="private_env_too_large"):
        read_private_env_file(destination)

    payload = "".join(f"K{index}=x\n" for index in range(2_001)).encode()
    _write_private(destination, payload)
    with pytest.raises(RuntimeError, match="private_env_too_many_lines"):
        read_private_env_file(destination)


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"BAD-KEY": "value"}, "private_env_key_invalid"),
        ({"VALID": "line one\nline two"}, "private_env_value_invalid"),
        ({"VALID": 123}, "private_env_value_invalid"),
        (["not", "a", "mapping"], "private_env_updates_invalid"),
    ],
)
def test_private_update_rejects_invalid_updates_before_creating_directory(
    tmp_path: Path,
    updates: object,
    reason: str,
) -> None:
    destination = tmp_path / "must-not-be-created" / ".env"

    with pytest.raises(RuntimeError, match=reason):
        update_private_env_file(destination, updates)  # type: ignore[arg-type]

    assert not destination.parent.exists()


def test_private_apis_reject_a_string_as_allowed_keys(tmp_path: Path) -> None:
    destination = _private_path(tmp_path)

    with pytest.raises(RuntimeError, match="private_env_allowed_key_invalid"):
        read_private_env_file(destination, allowed_keys="API_KEY")
    with pytest.raises(RuntimeError, match="private_env_allowed_key_invalid"):
        update_private_env_file(
            destination,
            {"API_KEY": "secret"},
            allowed_keys="API_KEY",
        )


def test_private_update_keeps_original_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = _private_path(tmp_path)
    update_private_env_file(destination, {"API_KEY": "original"})

    def fail_replace(source: str, target: str | Path) -> None:
        del source, target
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(env_module.os, "replace", fail_replace)

    with pytest.raises(RuntimeError, match="private_env_write_failed"):
        update_private_env_file(destination, {"API_KEY": "replacement"})

    assert read_private_env_file(destination) == {"API_KEY": "original"}
    assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))


def test_private_update_fsyncs_file_and_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = _private_path(tmp_path)
    synced_types: list[str] = []
    real_fsync = env_module.os.fsync

    def tracking_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        synced_types.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(env_module.os, "fsync", tracking_fsync)

    update_private_env_file(destination, {"API_KEY": "secret"})

    assert "file" in synced_types
    if os.name == "posix":
        assert "directory" in synced_types


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory mode contract")
def test_private_update_repairs_existing_parent_mode(tmp_path: Path) -> None:
    destination = _private_path(tmp_path)
    destination.parent.chmod(0o755)

    update_private_env_file(destination, {"API_KEY": "secret"})

    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700


def test_permissive_parser_keeps_legacy_behavior(tmp_path: Path) -> None:
    destination = tmp_path / ".env"
    destination.write_text(
        "MISSING_EQUALS\n1LEGACY=value\nQUOTED='still supported'\n",
        encoding="utf-8",
    )
    if os.name == "posix":
        destination.chmod(0o644)

    assert parse_env_file(destination) == {
        "1LEGACY": "value",
        "QUOTED": "still supported",
    }


def test_private_updates_are_serialized_across_callers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = _private_path(tmp_path)
    active = 0
    max_active = 0
    state_lock = threading.Lock()
    original = env_module._update_private_env_file_unlocked

    def observed_update(*args, **kwargs):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        try:
            return original(*args, **kwargs)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(
        env_module,
        "_update_private_env_file_unlocked",
        observed_update,
    )
    threads = [
        threading.Thread(
            target=update_private_env_file,
            args=(destination, {f"KEY_{index}": str(index)}),
        )
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert max_active == 1
    assert read_private_env_file(destination) == {"KEY_0": "0", "KEY_1": "1"}
