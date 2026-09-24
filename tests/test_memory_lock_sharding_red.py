"""Concurrency contracts for scope-aware Memory write locking.

The authority path, rather than an arbitrary ``data_root`` supplied by one
manager, defines whether two writes conflict.  These tests block the first
writer at its canonical ``memory.json`` publish and observe whether a second
writer can reach its own publish before that block is released.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import minicode.memory as memory_module
from minicode.memory import MemoryManager, MemoryScope
from minicode.memory_authority_publisher import (
    MemoryAuthorityPublisher,
    MemoryScopePublication,
)
from minicode.memory_store import MemoryStoreConflict, MemoryStoreError
from minicode.project_memory_deletion import ProjectMemoryDeletionAuthority

Operation = Callable[[], object]


def _manager(workspace: Path, data_root: Path) -> MemoryManager:
    workspace.mkdir(parents=True, exist_ok=True)
    return MemoryManager(
        project_root=workspace,
        data_root=data_root,
        store_timeout=2.0,
    )


def _publish_private_scope_authority(
    manager: MemoryManager,
    scope: MemoryScope,
    target: Path,
    content: str,
) -> None:
    """Exercise today's raw seam and tomorrow's scope-aware replacement."""

    scope_aware_publish = getattr(manager, "_publish_scope_authority", None)
    if callable(scope_aware_publish):
        scope_aware_publish(scope, target, content)
        return
    manager._atomic_write(target, content)


def _second_reaches_publish_while_first_is_blocked(
    first: MemoryManager,
    first_scope: MemoryScope,
    first_operation: Operation,
    second: MemoryManager,
    second_scope: MemoryScope,
    second_operation: Operation,
    monkeypatch: pytest.MonkeyPatch,
) -> bool:
    """Observe lock overlap without relying on wall-clock completion time."""

    first_target = first._get_scope_path(first_scope) / "memory.json"
    second_target = second._get_scope_path(second_scope) / "memory.json"
    first_original_write = first._atomic_write
    second_original_write = second._atomic_write
    first_blocked = threading.Event()
    release_first = threading.Event()
    second_called = threading.Event()
    second_reached_publish = threading.Event()
    blocked_once = False

    def block_first_publish(target: Path, content: str) -> None:
        nonlocal blocked_once
        if target == first_target and not blocked_once:
            blocked_once = True
            first_blocked.set()
            if not release_first.wait(timeout=5.0):
                raise TimeoutError("test did not release the first Memory writer")
        first_original_write(target, content)

    def observe_second_publish(target: Path, content: str) -> None:
        if target == second_target:
            second_reached_publish.set()
        second_original_write(target, content)

    def run_second() -> object:
        second_called.set()
        return second_operation()

    monkeypatch.setattr(first, "_atomic_write", block_first_publish)
    monkeypatch.setattr(second, "_atomic_write", observe_second_publish)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_operation)
        if not first_blocked.wait(timeout=2.0):
            release_first.set()
            first_future.result(timeout=2.0)
            pytest.fail("first Memory writer never reached its authority publish")
        second_future = executor.submit(run_second)
        assert second_called.wait(timeout=1.0)
        overlapped = second_reached_publish.wait(timeout=0.4)
        release_first.set()
        first_future.result(timeout=2.0)
        second_future.result(timeout=2.0)
    return overlapped


def test_project_only_writes_in_different_workspaces_do_not_block_each_other(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "shared-user-root"
    first = _manager(tmp_path / "workspace-a", data_root)
    second = _manager(tmp_path / "workspace-b", data_root)

    overlapped = _second_reaches_publish_while_first_is_blocked(
        first,
        MemoryScope.PROJECT,
        lambda: first.add_entry(
            MemoryScope.PROJECT,
            "testing",
            "Workspace A has an independent project authority.",
            source="test",
        ),
        second,
        MemoryScope.PROJECT,
        lambda: second.add_entry(
            MemoryScope.PROJECT,
            "testing",
            "Workspace B has an independent project authority.",
            source="test",
        ),
        monkeypatch,
    )

    assert overlapped is True


def test_same_workspace_project_writes_serialize_across_data_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    first = _manager(workspace, tmp_path / "user-root-a")
    second = _manager(workspace, tmp_path / "user-root-b")

    overlapped = _second_reaches_publish_while_first_is_blocked(
        first,
        MemoryScope.PROJECT,
        lambda: first.add_entry(
            MemoryScope.PROJECT,
            "testing",
            "The first writer owns this shared project authority.",
            source="test",
        ),
        second,
        MemoryScope.PROJECT,
        lambda: second.add_entry(
            MemoryScope.PROJECT,
            "testing",
            "The second writer must wait for the same project authority.",
            source="test",
        ),
        monkeypatch,
    )

    assert overlapped is False


def test_user_writes_from_different_workspaces_remain_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "shared-user-root"
    first = _manager(tmp_path / "workspace-a", data_root)
    second = _manager(tmp_path / "workspace-b", data_root)

    overlapped = _second_reaches_publish_while_first_is_blocked(
        first,
        MemoryScope.USER,
        lambda: first.add_entry(
            MemoryScope.USER,
            "testing",
            "The first workspace updates shared user Memory.",
            source="test",
        ),
        second,
        MemoryScope.USER,
        lambda: second.add_entry(
            MemoryScope.USER,
            "testing",
            "The second workspace must wait for shared user Memory.",
            source="test",
        ),
        monkeypatch,
    )

    assert overlapped is False


def test_multiscope_operation_holds_its_project_lock_across_data_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    first_data_root = tmp_path / "user-root-a"
    bootstrap = _manager(workspace, first_data_root)
    project = bootstrap.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Multi-scope accounting touches the project authority.",
        source="test",
    )
    user = bootstrap.add_entry(
        MemoryScope.USER,
        "testing",
        "Multi-scope accounting also touches user authority.",
        source="test",
    )
    assert project is not None
    assert user is not None

    first = _manager(workspace, first_data_root)
    second = _manager(workspace, tmp_path / "user-root-b")
    ids = [project.id, user.id]
    overlapped = _second_reaches_publish_while_first_is_blocked(
        first,
        MemoryScope.PROJECT,
        lambda: first.record_retrievals_and_injections(
            ids,
            ids,
            observation_id="multiscope-lock-contract",
        ),
        second,
        MemoryScope.PROJECT,
        lambda: second.add_entry(
            MemoryScope.PROJECT,
            "testing",
            "A project writer conflicts with the multi-scope transaction.",
            source="test",
        ),
        monkeypatch,
    )

    assert overlapped is False


def test_project_lock_cannot_be_silently_upgraded_to_user_and_project(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    project = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Nested lock upgrade project fixture.",
        source="test",
    )
    user = manager.add_entry(
        MemoryScope.USER,
        "testing",
        "Nested lock upgrade user fixture.",
        source="test",
    )
    assert project is not None
    assert user is not None
    paths = (
        manager._get_scope_path(MemoryScope.PROJECT) / "memory.json",
        manager._get_scope_path(MemoryScope.USER) / "memory.json",
    )
    authority_before = tuple(path.read_bytes() for path in paths)

    with pytest.raises(MemoryStoreConflict):
        manager.coordinated_write(
            (MemoryScope.PROJECT,),
            lambda: manager.record_retrievals_and_injections(
                [project.id, user.id],
                [project.id, user.id],
                observation_id="nested-project-to-multiscope",
            ),
        )

    assert tuple(path.read_bytes() for path in paths) == authority_before


def test_user_lock_cannot_be_silently_upgraded_to_project(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Existing project authority before nested lock upgrade.",
        source="test",
    ) is not None
    assert manager.add_entry(
        MemoryScope.USER,
        "testing",
        "Existing user authority before nested lock upgrade.",
        source="test",
    ) is not None
    paths = (
        manager._get_scope_path(MemoryScope.PROJECT) / "memory.json",
        manager._get_scope_path(MemoryScope.USER) / "memory.json",
    )
    authority_before = tuple(path.read_bytes() for path in paths)

    with pytest.raises(MemoryStoreConflict):
        manager.coordinated_write(
            (MemoryScope.USER,),
            lambda: manager.add_entry(
                MemoryScope.PROJECT,
                "testing",
                "This nested project write must never become authority.",
                source="test",
            ),
        )

    assert tuple(path.read_bytes() for path in paths) == authority_before


def test_project_and_local_writes_share_one_workspace_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    first = _manager(workspace, tmp_path / "user-root-a")
    second = _manager(workspace, tmp_path / "user-root-b")

    overlapped = _second_reaches_publish_while_first_is_blocked(
        first,
        MemoryScope.PROJECT,
        lambda: first.add_entry(
            MemoryScope.PROJECT,
            "testing",
            "Project and local share the workspace write lock.",
            source="test",
        ),
        second,
        MemoryScope.LOCAL,
        lambda: second.add_entry(
            MemoryScope.LOCAL,
            "testing",
            "Local Memory must wait for a project authority write.",
            source="test",
        ),
        monkeypatch,
    )

    assert overlapped is False


def test_same_thread_manager_cannot_borrow_another_managers_project_lock(
    tmp_path: Path,
) -> None:
    """A thread-local lock depth belongs to one Manager transaction capability."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    first = _manager(workspace, data_root)
    stale_second = _manager(workspace, data_root)
    first_entry = None
    second_error: BaseException | None = None

    def write_with_competing_manager() -> None:
        nonlocal first_entry, second_error
        first_entry = first.add_entry(
            MemoryScope.PROJECT,
            "testing",
            "The owning Manager publishes this project entry.",
            source="test",
        )
        try:
            stale_second.add_entry(
                MemoryScope.PROJECT,
                "testing",
                "A different Manager must not borrow the owner's lock depth.",
                source="test",
            )
        except BaseException as error:  # inspect the fail-closed contract below
            second_error = error

    first.coordinated_write(
        (MemoryScope.PROJECT,),
        write_with_competing_manager,
    )

    fresh = _manager(workspace, data_root)
    contents = {
        entry.content for entry in fresh.memories[MemoryScope.PROJECT].entries
    }
    assert isinstance(second_error, MemoryStoreConflict)
    assert first_entry is not None
    assert first_entry.content in contents
    assert "A different Manager must not borrow the owner's lock depth." not in contents


def test_stale_project_writer_rejects_supersedes_reference_to_new_user_entry(
    tmp_path: Path,
) -> None:
    """A one-scope save must validate relationships against current other roots."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    bootstrap = _manager(workspace, data_root)
    baseline = bootstrap.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Existing project authority must survive a rejected stale write.",
        source="test",
    )
    assert baseline is not None

    stale = _manager(workspace, data_root)
    concurrent = _manager(workspace, data_root)
    user_entry = concurrent.add_entry(
        MemoryScope.USER,
        "testing",
        "A concurrently-created user-scoped memory.",
        source="test",
    )
    assert user_entry is not None
    authority_paths = (
        stale._get_scope_path(MemoryScope.PROJECT) / "memory.json",
        stale._get_scope_path(MemoryScope.USER) / "memory.json",
    )
    authority_before = tuple(path.read_bytes() for path in authority_paths)
    stale_error: BaseException | None = None

    try:
        stale.add_entry(
            MemoryScope.PROJECT,
            "testing",
            "This invalid cross-scope replacement must never become authority.",
            metadata={"supersedes": [user_entry.id]},
            source="test",
        )
    except BaseException as error:  # inspect durability after the rejection
        stale_error = error

    assert isinstance(stale_error, MemoryStoreConflict)
    assert tuple(path.read_bytes() for path in authority_paths) == authority_before
    fresh = _manager(workspace, data_root)
    assert any(
        entry.id == user_entry.id
        for entry in fresh.memories[MemoryScope.USER].entries
    )
    assert any(
        entry.id == baseline.id
        for entry in fresh.memories[MemoryScope.PROJECT].entries
    )
    assert all(
        entry.content
        != "This invalid cross-scope replacement must never become authority."
        for entry in fresh.memories[MemoryScope.PROJECT].entries
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory identity")
def test_project_write_does_not_follow_replaced_workspace_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The open workspace lock must anchor authority publication to that root."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Original project authority before workspace replacement.",
        source="test",
    )
    assert entry is not None

    authority_path = manager._get_scope_path(MemoryScope.PROJECT) / "memory.json"
    authority_before = authority_path.read_bytes()
    displaced_workspace = tmp_path / "displaced-workspace"
    replacement_authority = workspace / ".mini-code-memory" / "memory.json"
    replacement_sentinel = b'{"replacement_workspace":true}\n'
    original_write = manager._atomic_write
    swapped = False

    def replace_workspace_before_publish(target: Path, content: str) -> None:
        nonlocal swapped
        if target == authority_path and not swapped:
            swapped = True
            workspace.rename(displaced_workspace)
            workspace.mkdir()
            (workspace / ".mini-code").mkdir()
            replacement_authority.parent.mkdir()
            replacement_authority.write_bytes(replacement_sentinel)
        original_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write", replace_workspace_before_publish)
    write_error: BaseException | None = None
    try:
        manager.update_entry(
            MemoryScope.PROJECT,
            entry.id,
            "A changed entry must not be published into a replacement workspace.",
        )
    except BaseException as error:  # lock-root replacement must fail closed
        write_error = error

    assert swapped is True
    assert isinstance(write_error, MemoryStoreError)
    assert replacement_authority.read_bytes() == replacement_sentinel
    assert (
        displaced_workspace / ".mini-code-memory" / "memory.json"
    ).read_bytes() == authority_before


def test_readonly_load_revision_sampling_cannot_hide_concurrent_project_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The initial snapshot and its sampled revision must describe one view."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    bootstrap = _manager(workspace, data_root)
    baseline = bootstrap.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Project state loaded by the read-only snapshot.",
        source="test",
    )
    assert baseline is not None
    concurrent = _manager(workspace, data_root)
    original_scope_disk_revision = MemoryManager._scope_disk_revision
    inserted = None
    injected_once = False

    def write_between_load_and_revision_sample(
        candidate: MemoryManager,
        scope: MemoryScope,
    ) -> str:
        nonlocal inserted, injected_once
        if (
            not candidate._recover_on_load
            and scope is MemoryScope.PROJECT
            and not injected_once
        ):
            injected_once = True
            inserted = concurrent.add_entry(
                MemoryScope.PROJECT,
                "testing",
                "Concurrent project update between load and revision sampling.",
                source="test",
            )
        return original_scope_disk_revision(candidate, scope)

    monkeypatch.setattr(
        MemoryManager,
        "_scope_disk_revision",
        write_between_load_and_revision_sample,
    )
    stale_snapshot = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        store_timeout=2.0,
        readonly_load=True,
    )
    assert injected_once is True
    assert inserted is not None
    assert not any(
        entry.id == inserted.id
        for entry in stale_snapshot.memories[MemoryScope.PROJECT].entries
    )

    own_entry = stale_snapshot.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The read-only snapshot's first project write.",
        source="test",
    )
    assert own_entry is not None

    fresh = _manager(workspace, data_root)
    project_ids = {
        entry.id for entry in fresh.memories[MemoryScope.PROJECT].entries
    }
    assert {baseline.id, inserted.id, own_entry.id} <= project_ids


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory identity")
def test_publisher_entry_cannot_follow_a_recreated_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The workspace identity fence must remain valid inside publish_text."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Original authority before the publisher-entry race.",
        source="test",
    )
    assert entry is not None
    authority_path = workspace / ".mini-code-memory" / "memory.json"
    authority_before = authority_path.read_bytes()
    displaced = tmp_path / "workspace-before-publisher-race"
    replacement_authority = workspace / ".mini-code-memory" / "memory.json"
    replacement_sentinel = b'{"replacement_workspace":"publisher-entry"}\n'
    original_publish = MemoryScopePublication.publish_text
    raced = False

    def race_at_publisher_entry(
        publication: MemoryScopePublication,
        target: str | Path,
        content: str,
    ) -> None:
        nonlocal raced
        target_path = Path(target)
        if target_path.name == authority_path.name and not raced:
            raced = True
            workspace.rename(displaced)
            workspace.mkdir()
            (workspace / ".mini-code").mkdir()
            replacement_authority.parent.mkdir()
            replacement_authority.write_bytes(replacement_sentinel)
        original_publish(publication, target_path.name, content)

    monkeypatch.setattr(
        MemoryScopePublication,
        "publish_text",
        race_at_publisher_entry,
    )
    write_error: BaseException | None = None
    try:
        manager.update_entry(
            MemoryScope.PROJECT,
            entry.id,
            "This update must remain bound to the original workspace.",
        )
    except BaseException as error:
        write_error = error

    assert raced is True
    assert isinstance(write_error, MemoryStoreError)
    assert replacement_authority.read_bytes() == replacement_sentinel
    assert (
        displaced / ".mini-code-memory" / "memory.json"
    ).read_bytes() == authority_before


@pytest.mark.parametrize("held_scope", [None, MemoryScope.USER])
def test_private_project_publisher_requires_its_project_capability(
    tmp_path: Path,
    held_scope: MemoryScope | None,
) -> None:
    """A private publication seam cannot bypass scope-lock ownership."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    assert manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Project authority protected by a scope-aware publisher.",
        source="test",
    ) is not None
    project_authority = workspace / ".mini-code-memory" / "memory.json"
    authority_before = project_authority.read_bytes()
    publish_error: BaseException | None = None

    def unauthorized_publish() -> None:
        nonlocal publish_error
        try:
            _publish_private_scope_authority(
                manager,
                MemoryScope.PROJECT,
                project_authority,
                '{"unauthorized_private_publish":true}',
            )
        except BaseException as error:
            publish_error = error

    if held_scope is None:
        unauthorized_publish()
    else:
        manager.coordinated_write((held_scope,), unauthorized_publish)

    assert isinstance(publish_error, MemoryStoreConflict)
    assert project_authority.read_bytes() == authority_before


def test_unrelated_project_write_preserves_legacy_dangling_relationship(
    tmp_path: Path,
) -> None:
    """A pre-existing dangling edge is not evidence of a new cross-scope edge."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    workspace.mkdir()
    data_root.mkdir()
    project_authority = workspace / ".mini-code-memory" / "memory.json"
    project_authority.parent.mkdir()
    legacy_id = "project-legacy-dangling-source"
    missing_id = "project-legacy-removed-target"
    project_authority.write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": [
                    {
                        "id": legacy_id,
                        "scope": "project",
                        "category": "testing",
                        "content": "A legacy lesson whose old target was removed.",
                        "related_to": [missing_id],
                    }
                ],
                "approval_audit": [],
            }
        ),
        encoding="utf-8",
    )

    manager = MemoryManager(project_root=workspace, data_root=data_root)
    assert (
        manager.memories[MemoryScope.PROJECT]
        ._id_index[legacy_id]
        .related_to
        == [missing_id]
    )
    unrelated = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "An unrelated project lesson must remain writable.",
        source="test",
    )

    assert unrelated is not None
    fresh = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    assert fresh.memories[MemoryScope.PROJECT]._id_index[unrelated.id].content == (
        "An unrelated project lesson must remain writable."
    )
    assert (
        fresh.memories[MemoryScope.PROJECT]
        ._id_index[legacy_id]
        .related_to
        == [missing_id]
    )


def test_all_scope_telemetry_rejects_mutated_returned_relationship(
    tmp_path: Path,
) -> None:
    """Telemetry cannot turn an unaudited live-object edit into authority."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    user_entry = manager.add_entry(
        MemoryScope.USER,
        "testing",
        "A user-scoped target that project Memory cannot reference.",
        source="test",
    )
    project_entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The returned project object remains mutable for compatibility.",
        source="test",
    )
    assert user_entry is not None and project_entry is not None
    authority_paths = (
        workspace / ".mini-code-memory" / "memory.json",
        data_root / "memory" / "memory.json",
    )
    authority_before = tuple(path.read_bytes() for path in authority_paths)
    project_entry.related_to = [user_entry.id]

    with pytest.raises(MemoryStoreConflict):
        manager.record_retrievals_and_injections(
            [project_entry.id],
            [project_entry.id],
            observation_id="mutated-returned-related-to",
        )

    assert tuple(path.read_bytes() for path in authority_paths) == authority_before
    current = manager.memories[MemoryScope.PROJECT]._id_index[project_entry.id]
    assert current.related_to == []
    fresh = _manager(workspace, data_root)
    assert (
        fresh.memories[MemoryScope.PROJECT]
        ._id_index[project_entry.id]
        .related_to
        == []
    )


def test_nested_different_workspace_manager_fails_before_lock_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One thread cannot invert workspace lock order through another Manager."""

    first = _manager(tmp_path / "workspace-a", tmp_path / "user-root-a")
    second = _manager(tmp_path / "workspace-b", tmp_path / "user-root-b")
    physical_acquisition_attempted = False

    def unexpected_physical_acquisition(*args: object, **kwargs: object) -> None:
        nonlocal physical_acquisition_attempted
        physical_acquisition_attempted = True
        raise AssertionError("nested Manager reached physical lock acquisition")

    monkeypatch.setattr(
        second._workspace_store,
        "transaction",
        unexpected_physical_acquisition,
    )

    def nested_write() -> None:
        with pytest.raises(MemoryStoreConflict, match="another manager"):
            second.add_entry(
                MemoryScope.PROJECT,
                "testing",
                "A nested write in a different workspace must fail immediately.",
                source="test",
            )

    first.coordinated_write((MemoryScope.PROJECT,), nested_write)

    assert physical_acquisition_attempted is False
    assert not (
        tmp_path / "workspace-b" / ".mini-code-memory" / "memory.json"
    ).exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX fork semantics")
def test_forked_manager_cannot_publish_inherited_dirty_project_snapshot(
    tmp_path: Path,
) -> None:
    """A PID change invalidates both lock capability and loaded authority."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    entry = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Canonical content before fork.",
        source="test",
    )
    assert entry is not None
    authority_path = workspace / ".mini-code-memory" / "memory.json"
    authority_before = authority_path.read_bytes()
    entry.content = "Uncommitted parent memory inherited across fork."
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            manager._save_scope(MemoryScope.PROJECT)
        except BaseException as error:
            outcome = type(error).__name__.encode("ascii", errors="replace")
        else:
            outcome = b"success"
        try:
            os.write(write_fd, outcome)
        finally:
            os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    outcome = os.read(read_fd, 128)
    os.close(read_fd)
    waited_pid, status = os.waitpid(pid, 0)

    assert waited_pid == pid
    assert os.waitstatus_to_exitcode(status) == 0
    assert outcome == b"MemoryStoreConflict"
    assert authority_path.read_bytes() == authority_before
    fresh = _manager(workspace, data_root)
    assert fresh.memories[MemoryScope.PROJECT]._id_index[entry.id].content == (
        "Canonical content before fork."
    )


@pytest.mark.parametrize("private_save", ["_save_scope", "_save_approval_audit"])
def test_readonly_private_save_rejects_load_revision_interleaving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    private_save: str,
) -> None:
    """Private recovery seams cannot authorize an unfenced readonly snapshot."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    bootstrap = _manager(workspace, data_root)
    baseline = bootstrap.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Authority loaded before the readonly revision sample.",
        source="test",
    )
    assert baseline is not None
    concurrent = _manager(workspace, data_root)
    original_revision = MemoryManager._scope_disk_revision
    inserted = None
    injected_once = False

    def insert_between_load_and_revision(
        candidate: MemoryManager,
        scope: MemoryScope,
    ) -> str:
        nonlocal inserted, injected_once
        if (
            not candidate._recover_on_load
            and scope is MemoryScope.PROJECT
            and not injected_once
        ):
            injected_once = True
            inserted = concurrent.add_entry(
                MemoryScope.PROJECT,
                "testing",
                "Concurrent authority before readonly revision sampling.",
                source="test",
            )
        return original_revision(candidate, scope)

    monkeypatch.setattr(
        MemoryManager,
        "_scope_disk_revision",
        insert_between_load_and_revision,
    )
    readonly = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    assert inserted is not None
    assert inserted.id not in readonly.memories[MemoryScope.PROJECT]._id_index
    authority_path = workspace / ".mini-code-memory" / "memory.json"
    authority_before = authority_path.read_bytes()

    with pytest.raises(MemoryStoreConflict, match="Unfenced"):
        getattr(readonly, private_save)(MemoryScope.PROJECT)

    assert authority_path.read_bytes() == authority_before
    fresh = _manager(workspace, data_root)
    assert inserted.id in fresh.memories[MemoryScope.PROJECT]._id_index


def test_project_deletion_ignores_unrelated_legacy_dangling_relationship(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting one identity must not reject an unchanged unrelated old edge."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "user-root"
    monkeypatch.setattr(memory_module, "MINI_CODE_DIR", data_root)
    manager = MemoryManager(project_root=workspace)
    target = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "The project memory selected for deletion.",
        source="test",
    )
    survivor = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "A surviving legacy entry with an unrelated missing target.",
        source="test",
    )
    assert target is not None and survivor is not None
    project_authority = workspace / ".mini-code-memory" / "memory.json"
    raw = json.loads(project_authority.read_text(encoding="utf-8"))
    stored_survivor = next(
        item for item in raw["entries"] if item["id"] == survivor.id
    )
    missing_id = "project-legacy-unrelated-removed-target"
    stored_survivor["related_to"] = [missing_id]
    project_authority.write_text(json.dumps(raw), encoding="utf-8")
    deletion = ProjectMemoryDeletionAuthority(workspace, data_dir=data_root)
    preview = deletion.snapshot(target.id)

    result = deletion.delete(target.id, str(preview["deletionRevision"]))

    assert result["status"] == "completed"
    fresh = MemoryManager(
        project_root=workspace,
        data_root=data_root,
        readonly_load=True,
    )
    assert target.id not in fresh.memories[MemoryScope.PROJECT]._id_index
    assert (
        fresh.memories[MemoryScope.PROJECT]
        ._id_index[survivor.id]
        .related_to
        == [missing_id]
    )


def test_writable_constructor_reloads_project_created_during_absent_root_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent-root optimization must re-fence a concurrently-created root."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    concurrent = _manager(workspace, tmp_path / "concurrent-user-root")
    original_prepare = MemoryManager._prepare_scope_authority
    original_save_locked = MemoryManager._save_scope_locked
    project_missing_was_read = threading.Event()
    release_constructor_load = threading.Event()
    writer_started = threading.Event()
    writer_completed = threading.Event()
    created_entry = None
    constructor_project_saves = 0
    constructed: list[MemoryManager] = []
    constructor_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []

    def create_after_missing_project_read(
        candidate: MemoryManager,
        scope: MemoryScope,
    ):
        prepared = original_prepare(candidate, scope)
        if (
            candidate is not concurrent
            and scope is MemoryScope.PROJECT
            and prepared.source is None
            and not project_missing_was_read.is_set()
        ):
            project_missing_was_read.set()
            if not release_constructor_load.wait(timeout=2.0):
                raise TimeoutError("constructor load was not released")
        return prepared

    def observe_constructor_save(
        candidate: MemoryManager,
        scope: MemoryScope,
        **kwargs: object,
    ) -> None:
        nonlocal constructor_project_saves
        if candidate is not concurrent and scope is MemoryScope.PROJECT:
            constructor_project_saves += 1
            assert candidate._transaction_covers((MemoryScope.PROJECT,))
        original_save_locked(candidate, scope, **kwargs)

    monkeypatch.setattr(
        MemoryManager,
        "_prepare_scope_authority",
        create_after_missing_project_read,
    )
    monkeypatch.setattr(
        MemoryManager,
        "_save_scope_locked",
        observe_constructor_save,
    )

    def construct_manager() -> None:
        try:
            constructed.append(
                MemoryManager(
                    project_root=workspace,
                    data_root=data_root,
                    store_timeout=2.0,
                )
            )
        except BaseException as error:
            constructor_errors.append(error)

    def publish_project() -> None:
        nonlocal created_entry
        writer_started.set()
        try:
            created_entry = concurrent.add_entry(
                MemoryScope.PROJECT,
                "testing",
                "Project authority created during constructor loading.",
                source="test",
            )
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_completed.set()

    constructor_thread = threading.Thread(target=construct_manager)
    constructor_thread.start()
    assert project_missing_was_read.wait(timeout=1.0)
    writer_thread = threading.Thread(target=publish_project)
    writer_thread.start()
    assert writer_started.wait(timeout=1.0)
    # A cooperating creator cannot publish under the writable constructor's
    # workspace fence. This is the safe resolution of the absent-root race.
    assert not writer_completed.wait(timeout=0.2)
    release_constructor_load.set()
    constructor_thread.join(timeout=2.0)
    writer_thread.join(timeout=2.0)

    assert not constructor_thread.is_alive()
    assert not writer_thread.is_alive()
    assert constructor_errors == []
    assert writer_errors == []
    assert len(constructed) == 1
    assert created_entry is not None
    assert constructor_project_saves == 0
    # Its empty view was consistent at constructor completion; once the
    # serialized writer commits, the normal stale fence must reveal it.
    assert created_entry.id not in (
        constructed[0].memories[MemoryScope.PROJECT]._id_index
    )
    assert MemoryScope.PROJECT in constructed[0].refresh_if_stale()
    assert created_entry.id in constructed[0].memories[MemoryScope.PROJECT]._id_index
    assert constructed[0]._disk_revisions[MemoryScope.PROJECT] == (
        constructed[0]._scope_disk_revision(MemoryScope.PROJECT)
    )


def test_direct_save_cannot_grandfather_newly_dangling_relationship(
    tmp_path: Path,
) -> None:
    """Deleting a resolved target without its backlink is never legacy state."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    target = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Resolved relationship target before the unsafe private edit.",
        source="test",
    )
    source = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Resolved relationship source before the unsafe private edit.",
        source="test",
    )
    assert target is not None and source is not None

    def persist_resolved_edge() -> None:
        current = manager.memories[MemoryScope.PROJECT]._id_index[source.id]
        current.related_to = [target.id]
        current._seal_authority_state()
        manager._save_scope(MemoryScope.PROJECT)

    manager.coordinated_write((MemoryScope.PROJECT,), persist_resolved_edge)
    authority_path = workspace / ".mini-code-memory" / "memory.json"
    authority_before = authority_path.read_bytes()
    memory_file = manager.memories[MemoryScope.PROJECT]
    memory_file.entries = [
        entry for entry in memory_file.entries if entry.id != target.id
    ]
    memory_file._rebuild_indices()

    with pytest.raises(MemoryStoreConflict):
        manager._save_scope(MemoryScope.PROJECT)

    assert authority_path.read_bytes() == authority_before
    fresh = _manager(workspace, data_root)
    assert target.id in fresh.memories[MemoryScope.PROJECT]._id_index
    assert fresh.memories[MemoryScope.PROJECT]._id_index[source.id].related_to == [
        target.id
    ]


def test_compress_scope_rewrites_every_relationship_to_duplicate_survivor(
    tmp_path: Path,
) -> None:
    """Removing a duplicate must not strand any durable relationship field."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    manager = _manager(workspace, data_root)
    survivor = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use one canonical checkout fixture.",
        source="test",
        allow_duplicate=True,
    )
    duplicate = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "Use one canonical checkout fixture.",
        source="test",
        allow_duplicate=True,
    )
    assert survivor is not None and duplicate is not None
    source = manager.add_entry(
        MemoryScope.PROJECT,
        "testing",
        "This entry references the duplicate through every relationship field.",
        source="test",
        metadata={
            "supersedes": [duplicate.id, duplicate.id],
            "superseded_by": duplicate.id,
        },
    )
    assert source is not None
    assert manager.set_related_memories(
        MemoryScope.PROJECT,
        source.id,
        [duplicate.id, survivor.id],
    )

    result = manager.compress_scope(MemoryScope.PROJECT)

    assert result["removed_count"] == 1
    fresh = _manager(workspace, data_root)
    project = fresh.memories[MemoryScope.PROJECT]._id_index
    assert duplicate.id not in project
    rewritten = project[source.id]
    assert rewritten.related_to == [survivor.id]
    assert rewritten.metadata.get("supersedes") == [survivor.id]
    assert rewritten.metadata.get("superseded_by") == survivor.id


@pytest.mark.parametrize("mutation", ["duplicate", "relation_kind"])
def test_legacy_dangling_grandfather_cannot_expand_an_old_edge(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Grandfathering preserves the exact old edge kind and multiplicity only."""

    workspace = tmp_path / "workspace"
    data_root = tmp_path / "user-root"
    workspace.mkdir()
    data_root.mkdir()
    project_authority = workspace / ".mini-code-memory" / "memory.json"
    project_authority.parent.mkdir()
    source_id = "project-legacy-dangling-source"
    missing_id = "project-legacy-removed-target"
    project_authority.write_text(
        json.dumps(
            {
                "scope": "project",
                "entries": [
                    {
                        "id": source_id,
                        "scope": "project",
                        "category": "testing",
                        "content": "A legacy lesson whose old target was removed.",
                        "related_to": [missing_id],
                    }
                ],
                "approval_audit": [],
            }
        ),
        encoding="utf-8",
    )
    manager = MemoryManager(project_root=workspace, data_root=data_root)
    authority_before = project_authority.read_bytes()

    def expand_old_edge() -> None:
        entry = manager.memories[MemoryScope.PROJECT]._id_index[source_id]
        if mutation == "duplicate":
            entry.related_to = [missing_id, missing_id]
        else:
            entry.related_to = []
            entry.metadata = {**entry.metadata, "supersedes": [missing_id]}
            entry.approval_content_hash = memory_module._approval_hash_for_entry(entry)
        entry._seal_authority_state()
        manager._save_scope(MemoryScope.PROJECT)

    with pytest.raises(MemoryStoreConflict, match="unresolved"):
        manager.coordinated_write(tuple(MemoryScope), expand_old_edge)

    assert project_authority.read_bytes() == authority_before
