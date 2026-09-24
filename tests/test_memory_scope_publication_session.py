from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from minicode.memory_authority_publisher import MemoryAuthorityPublisher
from minicode.memory_store import (
    MemoryStoreCoordinator,
    MemoryStoreError,
    MemoryStoreUnsafePath,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="root-bound scope publication requires POSIX dirfd support",
)


def _coordinator(workspace: Path) -> MemoryStoreCoordinator:
    workspace.mkdir()
    return MemoryStoreCoordinator(workspace, lock_name="memory-authority.lock")


def test_scope_session_creates_and_publishes_below_transaction_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()
        with MemoryAuthorityPublisher.open_scope(
            binding,
            ".mini-code-memory",
            create=True,
        ) as publication:
            publication.publish_text("memory.json", '{"version":1}\n')

    authority = workspace / ".mini-code-memory" / "memory.json"
    assert authority.read_text(encoding="utf-8") == '{"version":1}\n'


@pytest.mark.parametrize(
    "invalid",
    ["", ".", "..", "nested/scope", "../scope", "/absolute"],
)
def test_scope_session_rejects_non_basename_scope(
    tmp_path: Path,
    invalid: str,
) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()
        with pytest.raises((ValueError, MemoryStoreUnsafePath)):
            with MemoryAuthorityPublisher.open_scope(binding, invalid, create=True):
                pass


@pytest.mark.parametrize(
    "invalid",
    ["", ".", "..", "nested/memory.json", "../memory.json", "/memory.json"],
)
def test_scope_session_rejects_non_basename_target(
    tmp_path: Path,
    invalid: str,
) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()
        with MemoryAuthorityPublisher.open_scope(
            binding,
            ".mini-code-memory",
            create=True,
        ) as publication:
            with pytest.raises((ValueError, MemoryStoreUnsafePath)):
                publication.publish_text(invalid, "unsafe")


def test_root_path_replacement_cannot_redirect_scope_publication(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)
    displaced = tmp_path / "workspace-displaced"
    replacement = tmp_path / "workspace-replacement"
    replacement.mkdir()

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()
        with MemoryAuthorityPublisher.open_scope(
            binding,
            ".mini-code-memory",
            create=True,
        ) as publication:
            workspace.rename(displaced)
            replacement.rename(workspace)
            try:
                with pytest.raises(MemoryStoreError):
                    publication.publish_text("memory.json", "redirected")
                assert not (workspace / ".mini-code-memory").exists()
                assert not (
                    displaced / ".mini-code-memory" / "memory.json"
                ).exists()
            finally:
                workspace.rename(replacement)
                displaced.rename(workspace)


def test_scope_path_replacement_cannot_split_authority_and_blobs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)
    scope = workspace / ".mini-code-memory"
    displaced = workspace / ".mini-code-memory-displaced"

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()
        with MemoryAuthorityPublisher.open_scope(
            binding,
            scope.name,
            create=True,
        ) as publication:
            blob_store = publication.blob_store()
            scope.rename(displaced)
            scope.mkdir()
            try:
                with pytest.raises(MemoryStoreError):
                    blob_store.put_json({"must": "not redirect"})
                with pytest.raises(MemoryStoreError):
                    publication.publish_text("memory.json", "redirected")
                assert list(scope.iterdir()) == []
                assert list(displaced.iterdir()) == []
            finally:
                scope.rmdir()
                displaced.rename(scope)


def test_blob_store_duplicates_the_same_scope_inode_without_path_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)
    duplicated_identities: list[tuple[int, int]] = []

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()
        with MemoryAuthorityPublisher.open_scope(
            binding,
            ".mini-code-memory",
            create=True,
        ) as publication:
            publication_type = type(publication)
            original_duplicate = publication_type.duplicate_scope_fd

            def recording_duplicate(self: object) -> int:
                descriptor = original_duplicate(self)  # type: ignore[arg-type]
                opened = os.fstat(descriptor)
                duplicated_identities.append((opened.st_dev, opened.st_ino))
                return descriptor

            monkeypatch.setattr(
                publication_type,
                "duplicate_scope_fd",
                recording_duplicate,
            )
            store = publication.blob_store()
            reference = store.put_json({"same": "scope inode"})
            assert store.read_json(reference) == {"same": "scope inode"}
            assert duplicated_identities
            assert set(duplicated_identities) == {publication.scope_identity}


def test_scope_session_cannot_be_used_after_context_exit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()
        with MemoryAuthorityPublisher.open_scope(
            binding,
            ".mini-code-memory",
            create=True,
        ) as publication:
            store = publication.blob_store()

        with pytest.raises(MemoryStoreError, match="closed"):
            publication.publish_text("memory.json", "late")
        with pytest.raises(MemoryStoreError, match="closed"):
            store.put_json({"late": True})


def test_scope_session_rejects_binding_after_transaction_exit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()

    with pytest.raises(MemoryStoreError):
        with MemoryAuthorityPublisher.open_scope(
            binding,
            ".mini-code-memory",
            create=True,
        ):
            pass

    assert not (workspace / ".mini-code-memory").exists()


def test_stale_binding_cannot_borrow_a_later_transaction_lease(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)

    with coordinator.transaction():
        stale = coordinator.transaction_root_binding()

    with coordinator.transaction():
        with pytest.raises(MemoryStoreError, match="not active"):
            with MemoryAuthorityPublisher.open_scope(
                stale,
                ".mini-code-memory",
                create=True,
            ):
                pass

    assert not (workspace / ".mini-code-memory").exists()


def test_binding_cannot_cross_its_issuing_thread(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    coordinator = _coordinator(workspace)
    errors: list[BaseException] = []

    with coordinator.transaction():
        binding = coordinator.transaction_root_binding()

        def attempt() -> None:
            try:
                with MemoryAuthorityPublisher.open_scope(
                    binding,
                    ".mini-code-memory",
                    create=True,
                ):
                    pass
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=attempt)
        worker.start()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], MemoryStoreError)
    assert "not active" in str(errors[0])
    assert not (workspace / ".mini-code-memory").exists()
