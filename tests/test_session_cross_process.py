from __future__ import annotations

import json
import multiprocessing
import os
import stat
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from minicode.advisory_lock import WINDOWS_LOCK_SENTINEL
from minicode.session import (
    AutosaveManager,
    SessionData,
    SessionStoreBusyError,
    SessionStoreLockError,
    cleanup_old_sessions,
    create_new_session,
    delete_session,
    list_sessions,
    load_session,
    save_session,
)


_PROCESS_TIMEOUT_SECONDS = 10


def _configure_session_storage(data_dir: str) -> None:
    from minicode import session as session_module

    root = Path(data_dir)
    session_module.MINI_CODE_DIR = root
    session_module.SESSIONS_DIR = root / "sessions"


def _send_child_error(connection: Connection, error: BaseException) -> None:
    connection.send(("error", type(error).__name__, str(error)[:240]))


def _save_new_session_at_index_rendezvous(
    data_dir: str,
    workspace: str,
    content: str,
    loaded_connection: Connection,
    release_event: multiprocessing.synchronize.Event,
) -> None:
    try:
        _configure_session_storage(data_dir)
        from minicode import session as session_module

        session = create_new_session(workspace)
        session.messages = [{"role": "user", "content": content}]
        real_load_index = session_module._load_session_index

        def load_index_then_pause():
            index = real_load_index()
            loaded_connection.send(
                ("loaded", session.session_id, tuple(sorted(index)))
            )
            if not release_event.wait(_PROCESS_TIMEOUT_SECONDS):
                raise TimeoutError("index rendezvous release timed out")
            return index

        session_module._load_session_index = load_index_then_pause
        save_session(session, force_full=True)
        loaded_connection.send(("saved", session.session_id))
    except BaseException as error:
        _send_child_error(loaded_connection, error)
        raise
    finally:
        loaded_connection.close()


def _save_existing_session(
    data_dir: str,
    session: SessionData,
    result_connection: Connection,
    force_full: bool = False,
) -> None:
    try:
        _configure_session_storage(data_dir)
        save_session(session, force_full=force_full)
        result_connection.send(("saved", session.session_id))
    except Exception as error:
        _send_child_error(result_connection, error)
    finally:
        result_connection.close()


def _save_after_start(
    data_dir: str,
    session: SessionData,
    ready_connection: Connection,
    start_event: multiprocessing.synchronize.Event,
) -> None:
    try:
        _configure_session_storage(data_dir)
        ready_connection.send(("ready", session.session_id))
        if not start_event.wait(_PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("save start timed out")
        save_session(session, force_full=True)
        ready_connection.send(("saved", session.session_id))
    except BaseException as error:
        _send_child_error(ready_connection, error)
        raise
    finally:
        ready_connection.close()


def _delete_after_start(
    data_dir: str,
    session_id: str,
    ready_connection: Connection,
    start_event: multiprocessing.synchronize.Event,
) -> None:
    try:
        _configure_session_storage(data_dir)
        ready_connection.send(("ready", session_id))
        if not start_event.wait(_PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("delete start timed out")
        ready_connection.send(("deleted", session_id, delete_session(session_id)))
    except BaseException as error:
        _send_child_error(ready_connection, error)
        raise
    finally:
        ready_connection.close()


def _reload_append_and_save(
    data_dir: str,
    session_id: str,
    content: str,
    result_connection: Connection,
) -> None:
    try:
        _configure_session_storage(data_dir)
        session = load_session(session_id)
        if session is None:
            raise AssertionError("Session did not reload")
        session.messages.append({"role": "assistant", "content": content})
        save_session(session)
        result_connection.send(("saved", session_id, len(session.messages)))
    except BaseException as error:
        _send_child_error(result_connection, error)
        raise
    finally:
        result_connection.close()


def _hold_session_store_lock(
    data_dir: str,
    ready_connection: Connection,
    release_event: multiprocessing.synchronize.Event,
    abrupt_exit: bool = False,
) -> None:
    try:
        _configure_session_storage(data_dir)
        from minicode.session_store import session_store_transaction

        with session_store_transaction(data_dir):
            ready_connection.send(("locked",))
            if abrupt_exit:
                os._exit(0)
            if not release_event.wait(_PROCESS_TIMEOUT_SECONDS):
                raise TimeoutError("lock holder release timed out")
    except BaseException as error:
        _send_child_error(ready_connection, error)
        raise
    finally:
        ready_connection.close()


def _receive(connection: Connection, *, timeout: float = 5) -> tuple[object, ...]:
    assert connection.poll(timeout), "child process did not report in time"
    message = connection.recv()
    assert isinstance(message, tuple)
    return message


def _join(process: multiprocessing.Process) -> None:
    process.join(_PROCESS_TIMEOUT_SECONDS)
    assert not process.is_alive(), "child process did not terminate"
    assert process.exitcode == 0


def test_two_processes_saving_different_sessions_retain_both_index_entries(
    tmp_path: Path,
) -> None:
    """A controlled index rendezvous exposes a lost update without timing races."""
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = multiprocessing.get_context("spawn")
    first_read, first_write = context.Pipe(duplex=False)
    second_read, second_write = context.Pipe(duplex=False)
    release_first = context.Event()
    release_second = context.Event()
    first = context.Process(
        target=_save_new_session_at_index_rendezvous,
        args=(
            str(data_dir),
            str(workspace),
            "first process",
            first_write,
            release_first,
        ),
    )
    second = context.Process(
        target=_save_new_session_at_index_rendezvous,
        args=(
            str(data_dir),
            str(workspace),
            "second process",
            second_write,
            release_second,
        ),
    )

    first.start()
    first_write.close()
    first_loaded = _receive(first_read)
    assert first_loaded[0] == "loaded"
    second.start()
    second_write.close()

    # Without a cross-process transaction both children load the same empty
    # index. With flock, the second cannot reach index loading until the first
    # complete transaction is released.
    if second_read.poll(1):
        second_loaded = second_read.recv()
        assert second_loaded[0] == "loaded"
        release_first.set()
        release_second.set()
    else:
        release_first.set()
        assert _receive(first_read)[0] == "saved"
        second_loaded = _receive(second_read)
        assert second_loaded[0] == "loaded"
        release_second.set()

    _join(first)
    _join(second)
    first_read.close()
    second_read.close()

    session_ids = {str(first_loaded[1]), str(second_loaded[1])}
    index = json.loads(
        (data_dir / "sessions_index.json").read_text(encoding="utf-8")
    )
    assert set(index) == session_ids
    assert all((data_dir / "sessions" / f"{session_id}.json").exists() for session_id in session_ids)

    _configure_session_storage(str(data_dir))
    from minicode.session import list_sessions

    assert {item.session_id for item in list_sessions()} == session_ids


def test_second_process_with_stale_same_session_revision_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from minicode import session as session_module

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    original = create_new_session(str(workspace))
    original.messages = [{"role": "user", "content": "base"}]
    save_session(original, force_full=True)
    first_writer = load_session(original.session_id)
    stale_writer = load_session(original.session_id)
    assert first_writer is not None and stale_writer is not None
    first_writer.messages.append({"role": "assistant", "content": "winner"})
    stale_writer.messages.append({"role": "assistant", "content": "stale"})

    context = multiprocessing.get_context("spawn")
    first_read, first_write = context.Pipe(duplex=False)
    first = context.Process(
        target=_save_existing_session,
        args=(str(data_dir), first_writer, first_write),
    )
    first.start()
    first_write.close()
    assert _receive(first_read)[0] == "saved"
    _join(first)
    first_read.close()

    stale_read, stale_write = context.Pipe(duplex=False)
    second = context.Process(
        target=_save_existing_session,
        args=(str(data_dir), stale_writer, stale_write, True),
    )
    second.start()
    stale_write.close()
    conflict = _receive(stale_read)
    _join(second)
    stale_read.close()

    assert conflict[:2] == ("error", "SessionWriteConflictError")
    reloaded = load_session(original.session_id)
    assert reloaded is not None
    assert [message["content"] for message in reloaded.messages] == [
        "base",
        "winner",
    ]
    delta_dir = data_dir / "sessions" / "deltas" / original.session_id
    assert [path.name for path in delta_dir.glob("delta_*.json")] == [
        "delta_0000.json"
    ]
    index = json.loads(
        (data_dir / "sessions_index.json").read_text(encoding="utf-8")
    )
    assert index[original.session_id]["message_count"] == 2


def test_cross_process_save_and_delete_keep_files_and_index_consistent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from minicode import session as session_module

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    deleted = create_new_session(str(workspace))
    deleted.messages = [{"role": "user", "content": "delete me"}]
    save_session(deleted, force_full=True)
    saved = create_new_session(str(workspace))
    saved.messages = [{"role": "user", "content": "keep me"}]

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    save_read, save_write = context.Pipe(duplex=False)
    delete_read, delete_write = context.Pipe(duplex=False)
    saver = context.Process(
        target=_save_after_start,
        args=(str(data_dir), saved, save_write, start),
    )
    deleter = context.Process(
        target=_delete_after_start,
        args=(str(data_dir), deleted.session_id, delete_write, start),
    )
    saver.start()
    deleter.start()
    save_write.close()
    delete_write.close()
    assert _receive(save_read)[0] == "ready"
    assert _receive(delete_read)[0] == "ready"
    start.set()
    assert _receive(save_read)[0] == "saved"
    assert _receive(delete_read) == ("deleted", deleted.session_id, True)
    _join(saver)
    _join(deleter)
    save_read.close()
    delete_read.close()

    index_path = data_dir / "sessions_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert set(index) == {saved.session_id}
    assert not (data_dir / "sessions" / f"{deleted.session_id}.json").exists()
    saved_path = data_dir / "sessions" / f"{saved.session_id}.json"
    assert saved_path.exists()
    assert json.loads(saved_path.read_text(encoding="utf-8"))["session_id"] == saved.session_id
    assert {item.session_id for item in list_sessions()} == {saved.session_id}


def test_later_process_reloads_latest_revision_before_appending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from minicode import session as session_module

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    context = multiprocessing.get_context("spawn")

    for expected_count, content in enumerate(("turn one", "turn two"), start=2):
        result_read, result_write = context.Pipe(duplex=False)
        process = context.Process(
            target=_reload_append_and_save,
            args=(str(data_dir), session.session_id, content, result_write),
        )
        process.start()
        result_write.close()
        assert _receive(result_read) == (
            "saved",
            session.session_id,
            expected_count,
        )
        _join(process)
        result_read.close()

    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert [message["content"] for message in reloaded.messages] == [
        "base",
        "turn one",
        "turn two",
    ]
    assert reloaded.metadata.message_count == 3
    assert reloaded.metadata.last_message == "turn two"
    delta_dir = data_dir / "sessions" / "deltas" / session.session_id
    assert sorted(path.name for path in delta_dir.glob("delta_*.json")) == [
        "delta_0000.json",
        "delta_0001.json",
    ]


def test_lock_timeout_preserves_every_session_file_and_autosave_dirty_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from minicode import session as session_module
    from minicode import session_store as store_module

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    monkeypatch.setattr(store_module, "SESSION_STORE_LOCK_TIMEOUT_SECONDS", 0.05)
    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "base"}]
    save_session(session, force_full=True)
    session.messages.append({"role": "assistant", "content": "existing delta"})
    save_session(session)
    session.messages.append({"role": "user", "content": "pending"})
    manager = AutosaveManager(session, interval=0)
    manager.mark_dirty()
    protected_paths = [
        data_dir / "sessions" / f"{session.session_id}.json",
        data_dir / "sessions" / "deltas" / session.session_id / "delta_0000.json",
        data_dir / "sessions_index.json",
    ]
    before = {path: path.read_bytes() for path in protected_paths}

    context = multiprocessing.get_context("spawn")
    ready_read, ready_write = context.Pipe(duplex=False)
    release = context.Event()
    holder = context.Process(
        target=_hold_session_store_lock,
        args=(str(data_dir), ready_write, release),
    )
    holder.start()
    ready_write.close()
    assert _receive(ready_read) == ("locked",)

    with pytest.raises(SessionStoreBusyError, match="session store is busy"):
        save_session(session)
    assert manager.save_now() is False
    assert manager._dirty is True
    assert {path: path.read_bytes() for path in protected_paths} == before
    assert list(data_dir.rglob(".*.tmp")) == []

    release.set()
    _join(holder)
    ready_read.close()
    assert manager.save_now() is True
    assert manager._dirty is False
    reloaded = load_session(session.session_id)
    assert reloaded is not None
    assert [message["content"] for message in reloaded.messages] == [
        "base",
        "existing delta",
        "pending",
    ]


def test_operating_system_releases_lock_after_holder_exits_without_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from minicode import session as session_module

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    context = multiprocessing.get_context("spawn")
    ready_read, ready_write = context.Pipe(duplex=False)
    never_release = context.Event()
    holder = context.Process(
        target=_hold_session_store_lock,
        args=(str(data_dir), ready_write, never_release, True),
    )
    holder.start()
    ready_write.close()
    assert _receive(ready_read) == ("locked",)
    _join(holder)
    ready_read.close()

    session = create_new_session(str(workspace))
    session.messages = [{"role": "user", "content": "saved after crash"}]
    save_session(session, force_full=True)
    assert load_session(session.session_id) is not None
    assert (data_dir / "session-store.lock").exists()


@pytest.mark.parametrize(
    "target_kind",
    [
        "symlink",
        "directory",
        pytest.param(
            "fifo",
            marks=pytest.mark.skipif(
                os.name == "nt", reason="named FIFO targets are POSIX-only"
            ),
        ),
    ],
)
def test_unsafe_lock_targets_fail_without_following_or_writing(
    tmp_path: Path,
    monkeypatch,
    target_kind: str,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    lock_path = data_dir / "session-store.lock"
    victim = tmp_path / "victim"
    victim.write_bytes(b"attacker-controlled-target")
    if target_kind == "symlink":
        lock_path.symlink_to(victim)
    elif target_kind == "directory":
        lock_path.mkdir()
    else:
        os.mkfifo(lock_path, 0o600)
    from minicode import session as session_module

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    session = create_new_session(str(tmp_path / "workspace"))
    session.messages = [{"role": "user", "content": "must not persist"}]

    with pytest.raises(SessionStoreLockError, match="lock unavailable"):
        save_session(session, force_full=True)

    assert victim.read_bytes() == b"attacker-controlled-target"
    assert not (data_dir / "sessions_index.json").exists()
    assert not (data_dir / "sessions" / f"{session.session_id}.json").exists()


def test_lock_open_failure_is_safe_and_low_information(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    from minicode import session as session_module
    from minicode import session_store as store_module

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    monkeypatch.setattr(
        store_module.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError("Bearer secret /private/path")
        ),
    )
    session = create_new_session(str(tmp_path / "workspace"))

    with pytest.raises(SessionStoreLockError) as error:
        save_session(session, force_full=True)

    assert str(error.value) == "session store lock unavailable"
    assert not (data_dir / "sessions_index.json").exists()


def test_lock_file_has_platform_payload_is_persistent_and_not_a_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from minicode import session as session_module
    from minicode.web.read_model import DashboardReadModel

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    for index in range(3):
        session = create_new_session(str(workspace))
        session.messages = [{"role": "user", "content": f"session {index}"}]
        save_session(session, force_full=True)

    lock_path = data_dir / "session-store.lock"
    expected_payload = WINDOWS_LOCK_SENTINEL if os.name == "nt" else b""
    assert lock_path.read_bytes() == expected_payload
    if os.name == "posix":
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert all(item.session_id != lock_path.name for item in list_sessions())
    dashboard = DashboardReadModel(workspace, data_dir=data_dir).sessions(limit=100)
    assert lock_path.name not in json.dumps(dashboard)

    assert cleanup_old_sessions(max_sessions=1) == 2
    remaining = list_sessions()
    assert len(remaining) == 1
    assert delete_session(remaining[0].session_id) is True
    assert lock_path.exists()
    assert lock_path.read_bytes() == expected_payload


def test_stale_writer_autosave_stays_dirty_without_rolling_back_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    from minicode import session as session_module

    monkeypatch.setattr(session_module, "MINI_CODE_DIR", data_dir)
    monkeypatch.setattr(session_module, "SESSIONS_DIR", data_dir / "sessions")
    original = create_new_session(str(workspace))
    original.messages = [{"role": "user", "content": "base"}]
    save_session(original, force_full=True)
    winner = load_session(original.session_id)
    stale = load_session(original.session_id)
    assert winner is not None and stale is not None
    winner.messages.append({"role": "assistant", "content": "winner"})
    save_session(winner)
    stale.messages.append({"role": "assistant", "content": "must not write"})
    manager = AutosaveManager(stale, interval=0)
    manager.mark_dirty()

    assert manager.save_now(force_full=True) is False
    assert manager._dirty is True
    reloaded = load_session(original.session_id)
    assert reloaded is not None
    assert [message["content"] for message in reloaded.messages] == [
        "base",
        "winner",
    ]


def test_each_transaction_uses_the_current_minicode_dir_for_its_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from minicode import session as session_module

    roots = [tmp_path / "first-root", tmp_path / "second-root"]
    for index, root in enumerate(roots):
        monkeypatch.setattr(session_module, "MINI_CODE_DIR", root)
        monkeypatch.setattr(session_module, "SESSIONS_DIR", root / "sessions")
        session = create_new_session(str(tmp_path / "workspace"))
        session.messages = [{"role": "user", "content": f"root {index}"}]
        save_session(session, force_full=True)
        assert (root / "session-store.lock").read_bytes() == (
            WINDOWS_LOCK_SENTINEL if os.name == "nt" else b""
        )

    assert all((root / "sessions_index.json").exists() for root in roots)


@pytest.mark.parametrize(
    "control_flow",
    [KeyboardInterrupt("stop"), SystemExit("stop")],
    ids=["keyboard-interrupt", "system-exit"],
)
@pytest.mark.skipif(os.name != "posix", reason="fcntl fault injection is POSIX-only")
def test_lock_acquisition_preserves_control_flow_identity(
    tmp_path: Path,
    monkeypatch,
    control_flow: BaseException,
) -> None:
    from minicode import advisory_lock as lock_module
    from minicode import session_store as store_module

    def interrupt(*_args, **_kwargs):
        raise control_flow

    assert lock_module.fcntl is not None
    monkeypatch.setattr(lock_module.fcntl, "flock", interrupt)
    caught: BaseException | None = None
    try:
        with store_module.session_store_transaction(tmp_path):
            pytest.fail("transaction must not start")
    except BaseException as error:
        caught = error

    assert caught is control_flow


@pytest.mark.skipif(os.name != "posix", reason="fcntl fault injection is POSIX-only")
def test_busy_timeout_uses_injected_monotonic_clock_without_real_sleep(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from minicode import advisory_lock as lock_module
    from minicode import session_store as store_module

    def always_busy(*_args, **_kwargs):
        raise BlockingIOError("busy")

    assert lock_module.fcntl is not None
    monkeypatch.setattr(lock_module.fcntl, "flock", always_busy)
    readings = iter((10.0, 10.2))
    waits: list[float] = []

    with pytest.raises(SessionStoreBusyError):
        with store_module.session_store_transaction(
            tmp_path,
            timeout=0.1,
            monotonic=lambda: next(readings),
            wait=waits.append,
        ):
            pytest.fail("transaction must not start")

    assert waits == []
