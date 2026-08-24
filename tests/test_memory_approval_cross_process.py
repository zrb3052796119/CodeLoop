from __future__ import annotations

import multiprocessing
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import minicode.memory as memory_mod
import minicode.memory_store as memory_store_mod
from minicode.advisory_lock import WINDOWS_LOCK_SENTINEL
from minicode.memory import MemoryApprovalPolicy, MemoryManager, MemoryScope
from minicode.memory_approval import MemoryApprovalAuthority, MemoryApprovalError
from minicode.memory_store import MemoryStoreCoordinator, MemoryStoreUnavailable


def _stale_writer(
    workspace: str,
    home: str,
    content: str,
    scope_value: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    result: multiprocessing.queues.Queue,
) -> None:
    memory_mod.MINI_CODE_DIR = Path(home)
    manager = MemoryManager(project_root=workspace)
    ready.set()
    try:
        if not release.wait(timeout=10):
            raise RuntimeError("parent did not release deterministic writer")
        entry = manager.add_entry(MemoryScope(scope_value), "note", content)
        result.put((entry.id if entry else None, None))
    except BaseException as error:  # pragma: no cover - returned to parent
        result.put((None, type(error).__name__))


def _decision_worker(
    workspace: str,
    home: str,
    memory_id: str,
    review_revision: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    result: multiprocessing.queues.Queue,
    timeout: float = 5.0,
) -> None:
    memory_mod.MINI_CODE_DIR = Path(home)
    authority = MemoryApprovalAuthority(workspace, store_timeout=timeout)
    ready.set()
    try:
        if not release.wait(timeout=10):
            raise RuntimeError("parent did not release deterministic decision")
        decision = authority.decide(
            memory_id=memory_id,
            decision="approve",
            review_revision=review_revision,
        )
        result.put((decision.status, decision.decision_accepted, None))
    except MemoryApprovalError as error:
        result.put((None, None, error.code))
    except BaseException as error:  # pragma: no cover - returned to parent
        result.put((None, None, type(error).__name__))


def _update_worker(
    workspace: str,
    home: str,
    memory_id: str,
    content: str,
    result: multiprocessing.queues.Queue,
) -> None:
    memory_mod.MINI_CODE_DIR = Path(home)
    try:
        manager = MemoryManager(project_root=workspace)
        changed = manager.update_entry(MemoryScope.PROJECT, memory_id, content)
        result.put((changed, None))
    except BaseException as error:  # pragma: no cover - returned to parent
        result.put((False, type(error).__name__))


def test_spawned_stale_writers_do_not_lose_either_project_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home" / ".mini-code"
    ready_a = context.Event()
    ready_b = context.Event()
    release_a = context.Event()
    release_b = context.Event()
    result = context.Queue()
    process_a = context.Process(
        target=_stale_writer,
        args=(str(workspace), str(home), "writer alpha durable note", "project", ready_a, release_a, result),
    )
    process_b = context.Process(
        target=_stale_writer,
        args=(str(workspace), str(home), "writer beta durable note", "project", ready_b, release_b, result),
    )
    process_a.start()
    process_b.start()
    assert ready_a.wait(timeout=10)
    assert ready_b.wait(timeout=10)
    release_a.set()
    release_b.set()
    process_a.join(timeout=10)
    assert process_a.exitcode == 0
    process_b.join(timeout=10)
    assert process_b.exitcode == 0
    first = result.get(timeout=2)
    second = result.get(timeout=2)
    assert first[1] is None and second[1] is None

    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", home)
    reloaded = MemoryManager(project_root=workspace)
    contents = {entry.content for entry in reloaded.memories[MemoryScope.PROJECT].entries}
    assert contents == {"writer alpha durable note", "writer beta durable note"}


def test_spawned_different_scope_writers_preserve_both_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home" / ".mini-code"
    ready_a = context.Event()
    ready_b = context.Event()
    release = context.Event()
    result = context.Queue()
    processes = [
        context.Process(
            target=_stale_writer,
            args=(
                str(workspace),
                str(home),
                content,
                scope,
                ready,
                release,
                result,
            ),
        )
        for content, scope, ready in (
            ("project process note", "project", ready_a),
            ("local process note", "local", ready_b),
        )
    ]
    for process in processes:
        process.start()
    assert ready_a.wait(timeout=10)
    assert ready_b.wait(timeout=10)
    release.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert all(result.get(timeout=2)[1] is None for _ in processes)

    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", home)
    reloaded = MemoryManager(project_root=workspace)
    assert {entry.content for entry in reloaded.memories[MemoryScope.PROJECT].entries} == {
        "project process note"
    }
    assert {entry.content for entry in reloaded.memories[MemoryScope.LOCAL].entries} == {
        "local process note"
    }


def test_spawned_same_pending_decision_has_one_accepted_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", home)
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "Review this cross-process parser rule",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    revision = MemoryApprovalAuthority(workspace).snapshot()["items"][0]["reviewRevision"]
    ready_a = context.Event()
    ready_b = context.Event()
    release = context.Event()
    result = context.Queue()
    processes = [
        context.Process(
            target=_decision_worker,
            args=(
                str(workspace),
                str(home),
                entry.id,
                revision,
                ready,
                release,
                result,
            ),
        )
        for ready in (ready_a, ready_b)
    ]
    for process in processes:
        process.start()
    assert ready_a.wait(timeout=10)
    assert ready_b.wait(timeout=10)
    release.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    outcomes = [result.get(timeout=2), result.get(timeout=2)]

    assert sorted(outcome[1] for outcome in outcomes) == [False, True]
    assert {outcome[0] for outcome in outcomes} == {"approved"}
    assert all(outcome[2] is None for outcome in outcomes)


def test_spawned_content_change_fences_parent_process_stale_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", home)
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "Review the old cross-process content",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    authority = MemoryApprovalAuthority(workspace)
    revision = authority.snapshot()["items"][0]["reviewRevision"]
    changed_content = "Review the new cross-process content"
    result = context.Queue()
    process = context.Process(
        target=_update_worker,
        args=(str(workspace), str(home), entry.id, changed_content, result),
    )
    process.start()
    process.join(timeout=10)

    assert process.exitcode == 0
    assert result.get(timeout=2) == (True, None)
    with pytest.raises(MemoryApprovalError, match="memory_review_stale"):
        authority.decide(
            memory_id=entry.id,
            decision="approve",
            review_revision=revision,
        )
    current = MemoryManager(project_root=workspace).memories[
        MemoryScope.PROJECT
    ]._id_index[entry.id]
    assert current.content == changed_content
    assert current.approval_status == "pending"
    assert current.is_active is False


def test_spawned_lock_timeout_returns_fixed_busy_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home" / ".mini-code"
    monkeypatch.setattr(memory_mod, "MINI_CODE_DIR", home)
    manager = MemoryManager(project_root=workspace)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "note",
        "Review this busy lock rule",
        source="reflection",
        approval_policy=MemoryApprovalPolicy.USER_REVIEW_REQUIRED,
    )
    assert entry is not None
    revision = MemoryApprovalAuthority(workspace).snapshot()["items"][0]["reviewRevision"]
    ready = context.Event()
    release = context.Event()
    release.set()
    result = context.Queue()
    process = context.Process(
        target=_decision_worker,
        args=(
            str(workspace),
            str(home),
            entry.id,
            revision,
            ready,
            release,
            result,
            0.1,
        ),
    )
    coordinator = MemoryStoreCoordinator(home)
    with coordinator.transaction():
        process.start()
        assert ready.wait(timeout=10)
        outcome = result.get(timeout=10)
        process.join(timeout=10)

    assert process.exitcode == 0
    assert outcome == (None, None, "memory_store_busy")


def test_coordination_lock_has_platform_payload_and_is_persistent(tmp_path: Path) -> None:
    root = tmp_path / "home" / ".mini-code"
    coordinator = MemoryStoreCoordinator(root)

    with coordinator.transaction():
        lock_path = root / "memory-store.lock"
        lock_stat = lock_path.lstat()
        assert stat.S_ISREG(lock_stat.st_mode)
        expected_payload = WINDOWS_LOCK_SENTINEL if os.name == "nt" else b""
        if os.name == "posix":
            assert stat.S_IMODE(lock_stat.st_mode) == 0o600
        assert lock_stat.st_size == len(expected_payload)
        if os.name == "nt":
            with pytest.raises(PermissionError):
                lock_path.read_bytes()
        else:
            assert lock_path.read_bytes() == expected_payload

    assert lock_path.is_file()
    assert lock_path.read_bytes() == expected_payload


def test_coordination_lock_refuses_symlinked_store_root(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    root = tmp_path / "memory-root"
    root.symlink_to(external, target_is_directory=True)

    with pytest.raises(MemoryStoreUnavailable):
        with MemoryStoreCoordinator(root).transaction():
            pass


def test_windows_final_path_alias_is_not_treated_as_a_reparse_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "memory-root"
    root.mkdir()
    monkeypatch.setattr(memory_store_mod, "_platform_name", lambda: "nt")

    def unexpected_realpath(_path: object) -> str:
        raise AssertionError("Windows root safety must not compare path spellings")

    monkeypatch.setattr(memory_store_mod.os.path, "realpath", unexpected_realpath)

    with MemoryStoreCoordinator(root).transaction():
        assert (root / "memory-store.lock").is_file()


@pytest.mark.parametrize("target_kind", ["ancestor", "root", "lock"])
def test_windows_reparse_store_targets_and_ancestors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    ancestor = tmp_path / "memory-parent"
    ancestor.mkdir()
    root = ancestor / "memory-root"
    root.mkdir()
    lock_path = root / "memory-store.lock"
    real_lstat = memory_store_mod.os.lstat
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    target = {
        "ancestor": ancestor,
        "root": root,
        "lock": lock_path,
    }[target_kind]

    def reparse_lstat(path: object) -> os.stat_result | SimpleNamespace:
        metadata = real_lstat(path)
        if Path(path) != target:
            return metadata
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_file_attributes=reparse_flag,
        )

    monkeypatch.setattr(memory_store_mod, "_platform_name", lambda: "nt")
    monkeypatch.setattr(memory_store_mod.os, "lstat", reparse_lstat)

    with pytest.raises(MemoryStoreUnavailable):
        with MemoryStoreCoordinator(root).transaction():
            pass
