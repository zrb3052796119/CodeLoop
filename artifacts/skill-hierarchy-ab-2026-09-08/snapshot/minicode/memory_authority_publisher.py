"""Crash-durable, directory-contained publication for Memory text files.

The Memory authority is a small replace-in-place JSON document.  Atomic
``rename`` prevents torn readers, but it does not by itself make the new bytes
survive power loss and a path-based temporary file can escape if the parent is
replaced between validation and publication.  This module owns the stronger
POSIX sequence:

1. open and bind the parent directory without following its final component;
2. create and write a private file relative to that directory descriptor;
3. fsync the private file;
4. replace the target relative to the same directory descriptor; and
5. fsync the directory before reporting success.

The portable fallback retains atomic replacement and file fsync.  Platforms
without directory descriptors cannot provide the same containment guarantee,
so every path component available through the portable API is revalidated
immediately before and after replacement.
"""

from __future__ import annotations

import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

from minicode.memory_store import (
    MemoryStoreConflict,
    MemoryStoreRootBinding,
    MemoryStoreUnavailable,
    MemoryStoreUnsafePath,
    _is_windows_reparse_point,
    _windows_path_has_reparse_component,
)

_BOUND_PUBLICATION_ROOT: ContextVar[MemoryStoreRootBinding | None] = ContextVar(
    "memory_authority_publication_root",
    default=None,
)

if TYPE_CHECKING:
    from minicode.memory_blob_store import MemoryBlobStore


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _require_directory(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise MemoryStoreUnsafePath("Memory publication directory is unsafe")


def _require_regular_target(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise MemoryStoreUnsafePath("Memory publication target is unsafe")


def _write_all(descriptor: int, content: bytes) -> None:
    position = 0
    while position < len(content):
        written = os.write(descriptor, content[position:])
        if written <= 0:
            raise OSError("short Memory authority write")
        position += written


def _safe_basename(path: Path) -> str:
    name = path.name
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise ValueError("Memory publication target must have a safe basename")
    return name


def _safe_relative_basename(value: str | Path, *, kind: str) -> str:
    """Return a single, exact child name rather than silently truncating a path."""

    try:
        raw = os.fspath(value)
    except TypeError as error:
        raise TypeError(f"Memory {kind} must be path-like") from error
    if not isinstance(raw, str):
        raise TypeError(f"Memory {kind} must be text")
    name = _safe_basename(Path(raw))
    if raw != name:
        raise MemoryStoreUnsafePath(
            f"Memory {kind} must be a safe basename"
        )
    return name


class MemoryScopePublication:
    """One root-bound POSIX publication session for a Memory scope.

    The coordinator continues to own the transaction-root descriptor.  This
    session owns one descriptor opened below that root and keeps every
    authority and blob operation relative to that exact scope inode.  The
    session deliberately refuses to follow a replacement pathname: a caller
    either commits into the scope it opened or fails closed.
    """

    __slots__ = (
        "_binding",
        "_closed",
        "_scope_fd",
        "_scope_name",
        "_scope_stat",
    )

    def __init__(
        self,
        *,
        binding: MemoryStoreRootBinding,
        scope_name: str,
        scope_fd: int,
        scope_stat: os.stat_result,
    ) -> None:
        self._binding = binding
        self._scope_name = scope_name
        self._scope_fd = scope_fd
        self._scope_stat = scope_stat
        self._closed = False

    @property
    def path(self) -> Path:
        """Return the display/path-compatibility projection for this scope."""

        return self._binding.root / self._scope_name

    @property
    def scope_identity(self) -> tuple[int, int]:
        """Return the device/inode identity held for the whole session."""

        return (self._scope_stat.st_dev, self._scope_stat.st_ino)

    def verify(self) -> None:
        """Fail closed unless root, scope pathname, and open fd still agree."""

        if self._closed:
            raise MemoryStoreConflict("Memory scope publication is closed")
        MemoryAuthorityPublisher._verify_bound_root(self._binding)
        if self._binding.root_fd is None:
            raise MemoryStoreUnavailable(
                "Memory publication root descriptor is unavailable"
            )
        try:
            path_state = os.stat(
                self._scope_name,
                dir_fd=self._binding.root_fd,
                follow_symlinks=False,
            )
            opened_state = os.fstat(self._scope_fd)
        except OSError as error:
            raise MemoryStoreUnsafePath(
                "Memory scope publication changed"
            ) from error
        _require_directory(path_state)
        _require_directory(opened_state)
        if (
            not _same_inode(self._scope_stat, opened_state)
            or not _same_inode(path_state, opened_state)
        ):
            raise MemoryStoreUnsafePath("Memory scope publication changed")

    def duplicate_scope_fd(self) -> int:
        """Return a verified duplicate for a scope-relative auxiliary API."""

        self.verify()
        duplicate: int | None = None
        try:
            duplicate = os.dup(self._scope_fd)
            opened_state = os.fstat(duplicate)
            _require_directory(opened_state)
            if not _same_inode(self._scope_stat, opened_state):
                raise MemoryStoreUnsafePath("Memory scope publication changed")
            self.verify()
            return duplicate
        except Exception:
            if duplicate is not None:
                os.close(duplicate)
            raise

    def publish_text(self, target_name: str | Path, content: str) -> None:
        """Publish one text child relative to this exact scope descriptor."""

        if not isinstance(content, str):
            raise TypeError("Memory publication content must be text")
        name = _safe_relative_basename(
            target_name,
            kind="publication target",
        )
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise MemoryStoreConflict(
                "Memory publication content is not UTF-8"
            ) from error
        self.verify()
        MemoryAuthorityPublisher._publish_posix_directory(
            self._scope_fd,
            name,
            encoded,
            verify=self.verify,
        )

    def blob_store(self) -> MemoryBlobStore:
        """Return a blob store anchored to this session's scope descriptor."""

        self.verify()
        # Local import avoids a publisher/blob-store module cycle.
        from minicode.memory_blob_store import MemoryBlobStore

        return MemoryBlobStore.from_scope_publication(self)

    def close(self) -> None:
        """Close this session's owned scope descriptor exactly once."""

        if self._closed:
            return
        self._closed = True
        os.close(self._scope_fd)


class MemoryAuthorityPublisher:
    """Publish bounded Memory text without a path-based rename window."""

    @classmethod
    @contextmanager
    def open_scope(
        cls,
        binding: MemoryStoreRootBinding,
        scope_name: str | Path,
        *,
        create: bool = False,
    ) -> Iterator[MemoryScopePublication]:
        """Open one safe scope child through an active POSIX root binding.

        ``binding.root_fd`` is borrowed from ``MemoryStoreCoordinator`` and
        therefore must remain live until this context exits.  The returned
        session owns only its scope descriptor.  No publication performed by
        it resolves the scope again through an absolute pathname.
        """

        if not isinstance(binding, MemoryStoreRootBinding):
            raise TypeError("binding must be a MemoryStoreRootBinding")
        if not isinstance(create, bool):
            raise TypeError("create must be a bool")
        name = _safe_relative_basename(scope_name, kind="scope name")
        if os.name != "posix" or binding.root_fd is None:
            raise MemoryStoreUnavailable(
                "Root-bound Memory scope publication requires POSIX dirfd support"
            )

        scope_fd: int | None = None
        session: MemoryScopePublication | None = None
        created = False
        setup_complete = False
        try:
            cls._verify_bound_root(binding)
            try:
                scope_before = os.stat(
                    name,
                    dir_fd=binding.root_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                if not create:
                    raise MemoryStoreUnavailable(
                        "Memory scope publication is unavailable"
                    )
                try:
                    os.mkdir(name, 0o700, dir_fd=binding.root_fd)
                    created = True
                except FileExistsError:
                    created = False
                scope_before = os.stat(
                    name,
                    dir_fd=binding.root_fd,
                    follow_symlinks=False,
                )
            _require_directory(scope_before)
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_flags |= getattr(os, "O_CLOEXEC", 0)
            directory_flags |= getattr(os, "O_NOFOLLOW", 0)
            scope_fd = os.open(name, directory_flags, dir_fd=binding.root_fd)
            scope_opened = os.fstat(scope_fd)
            _require_directory(scope_opened)
            if not _same_inode(scope_before, scope_opened):
                raise MemoryStoreUnsafePath("Memory scope publication changed")
            session = MemoryScopePublication(
                binding=binding,
                scope_name=name,
                scope_fd=scope_fd,
                scope_stat=scope_opened,
            )
            scope_fd = None  # session owns it from here
            session.verify()
            if created:
                os.fsync(session._scope_fd)
                os.fsync(binding.root_fd)
                session.verify()
            setup_complete = True
        except (
            MemoryStoreConflict,
            MemoryStoreUnavailable,
            MemoryStoreUnsafePath,
        ):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory scope publication is unavailable"
            ) from error
        finally:
            if not setup_complete and session is not None:
                session.close()
            elif not setup_complete and scope_fd is not None:
                try:
                    os.close(scope_fd)
                except OSError:
                    pass

        if session is None:  # defensive: setup either succeeds or raises
            raise MemoryStoreUnavailable(
                "Memory scope publication is unavailable"
            )
        try:
            # Exceptions from the caller's commit body retain their original
            # type. Only publisher-owned setup/verification OSErrors are
            # normalized to the Memory storage vocabulary.
            yield session
        except BaseException:
            raise
        else:
            try:
                session.verify()
            except (
                MemoryStoreConflict,
                MemoryStoreUnavailable,
                MemoryStoreUnsafePath,
            ):
                raise
            except OSError as error:
                raise MemoryStoreUnavailable(
                    "Memory scope publication is unavailable"
                ) from error
        finally:
            session.close()

    @classmethod
    @contextmanager
    def bind_transaction_root(
        cls,
        binding: MemoryStoreRootBinding,
    ) -> Iterator[None]:
        """Bind nested publications to an already-open coordinator root."""

        del cls
        if not isinstance(binding, MemoryStoreRootBinding):
            raise TypeError("binding must be a MemoryStoreRootBinding")
        token = _BOUND_PUBLICATION_ROOT.set(binding)
        try:
            yield
        finally:
            _BOUND_PUBLICATION_ROOT.reset(token)

    @classmethod
    def _publish_posix_directory(
        cls,
        directory_fd: int,
        name: str,
        encoded: bytes,
        *,
        verify: Callable[[], None],
    ) -> None:
        """Durably replace one child of a caller-owned verified directory fd."""

        temporary_fd: int | None = None
        temporary_name: str | None = None
        try:
            verify()
            cls._validate_posix_target(directory_fd, name)
            temporary_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            temporary_flags |= getattr(os, "O_CLOEXEC", 0)
            temporary_flags |= getattr(os, "O_NOFOLLOW", 0)
            for _attempt in range(16):
                candidate = f".{name}.{uuid.uuid4().hex}.tmp"
                try:
                    temporary_fd = os.open(
                        candidate,
                        temporary_flags,
                        0o600,
                        dir_fd=directory_fd,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if temporary_fd is None or temporary_name is None:
                raise MemoryStoreUnavailable(
                    "Memory publication temporary file is unavailable"
                )
            os.fchmod(temporary_fd, 0o600)
            _write_all(temporary_fd, encoded)
            os.fsync(temporary_fd)
            temporary_opened = os.fstat(temporary_fd)
            _require_regular_target(temporary_opened)
            if temporary_opened.st_size != len(encoded):
                raise MemoryStoreConflict(
                    "Memory publication temporary file has the wrong size"
                )

            verify()
            cls._validate_posix_target(directory_fd, name)
            temporary_path_state = os.stat(
                temporary_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            _require_regular_target(temporary_path_state)
            if (
                not _same_inode(temporary_opened, temporary_path_state)
                or temporary_path_state.st_size != len(encoded)
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication temporary file changed"
                )
            os.replace(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary_name = None
            published_state = os.stat(
                name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            _require_regular_target(published_state)
            if (
                not _same_inode(temporary_opened, published_state)
                or published_state.st_size != len(encoded)
            ):
                raise MemoryStoreUnsafePath("Memory publication target changed")
            os.fsync(directory_fd)
            verify()
        except (
            MemoryStoreConflict,
            MemoryStoreUnavailable,
            MemoryStoreUnsafePath,
        ):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable("Memory publication failed") from error
        finally:
            if temporary_fd is not None:
                try:
                    os.close(temporary_fd)
                except OSError:
                    pass
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError:
                    pass

    @classmethod
    def ensure_directory(cls, directory: str | Path) -> None:
        """Create one authority directory and durably publish its name.

        The owner directory is allowed to be reached through an intentional
        workspace symlink, but the new/final child itself is never followed.
        Creation and validation are relative to one verified owner descriptor.
        """

        directory_path = Path(directory)
        _safe_basename(directory_path)
        owner = directory_path.parent
        try:
            os.stat(owner)
        except FileNotFoundError:
            if owner == directory_path:
                raise MemoryStoreUnavailable(
                    "Memory publication owner is unavailable"
                )
            # Preserve the historical ``mkdir(parents=True)`` contract while
            # making every newly introduced directory entry durable in order
            # from the nearest existing ancestor down to the scope root.
            cls.ensure_directory(owner)
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory publication owner is unavailable"
            ) from error
        if os.name == "posix":
            cls._ensure_directory_posix(directory_path)
        else:
            cls._ensure_directory_portable(directory_path)

    @classmethod
    def publish_text(cls, target: str | Path, content: str) -> None:
        if not isinstance(content, str):
            raise TypeError("Memory publication content must be text")
        target_path = Path(target)
        _safe_basename(target_path)
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as error:
            raise MemoryStoreConflict(
                "Memory publication content is not UTF-8"
            ) from error
        binding = _BOUND_PUBLICATION_ROOT.get()
        if binding is not None:
            relative_target = cls._bound_relative_target(binding, target_path)
            if os.name == "posix" and binding.root_fd is not None:
                cls._publish_posix_bound(
                    binding,
                    relative_target,
                    encoded,
                )
                return
            cls._verify_portable_bound_root(binding)
            cls._publish_portable(binding.root / relative_target, encoded)
            cls._verify_portable_bound_root(binding)
            return
        if os.name == "posix":
            cls._publish_posix(target_path, encoded)
        else:
            cls._publish_portable(target_path, encoded)

    @staticmethod
    def _bound_relative_target(
        binding: MemoryStoreRootBinding,
        target: Path,
    ) -> Path:
        root = Path(os.path.abspath(os.fspath(binding.root)))
        candidate = Path(os.path.abspath(os.fspath(target)))
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise MemoryStoreUnsafePath(
                "Memory publication target escapes its transaction root"
            ) from error
        if not relative.parts or any(
            part in {"", ".", ".."} or "/" in part or "\\" in part
            for part in relative.parts
        ):
            raise MemoryStoreUnsafePath(
                "Memory publication target escapes its transaction root"
            )
        return relative

    @staticmethod
    def _verify_bound_root(binding: MemoryStoreRootBinding) -> None:
        binding.assert_active()
        if binding.root_fd is None:
            raise MemoryStoreUnavailable(
                "Memory publication root descriptor is unavailable"
            )
        try:
            path_state = os.lstat(binding.root)
            opened_state = os.fstat(binding.root_fd)
        except OSError as error:
            raise MemoryStoreUnsafePath(
                "Memory publication root changed"
            ) from error
        _require_directory(path_state)
        _require_directory(opened_state)
        if (
            not _same_inode(binding.root_stat, opened_state)
            or not _same_inode(path_state, opened_state)
        ):
            raise MemoryStoreUnsafePath("Memory publication root changed")

    @staticmethod
    def _verify_portable_bound_root(binding: MemoryStoreRootBinding) -> None:
        binding.assert_active()
        try:
            current = os.lstat(binding.root)
        except OSError as error:
            raise MemoryStoreUnsafePath(
                "Memory publication root changed"
            ) from error
        _require_directory(current)
        if (
            _is_windows_reparse_point(current)
            or not _same_inode(current, binding.root_stat)
        ):
            raise MemoryStoreUnsafePath("Memory publication root changed")

    @classmethod
    def _publish_posix_bound(
        cls,
        binding: MemoryStoreRootBinding,
        relative_target: Path,
        encoded: bytes,
    ) -> None:
        """Publish through the transaction root fd, never a replaced path."""

        name = _safe_basename(relative_target)
        directory_parts = tuple(relative_target.parts[:-1])
        directory_fds: list[int] = []
        directory_names: list[str] = []
        directory_states: list[os.stat_result] = []
        temporary_fd: int | None = None
        temporary_name: str | None = None

        def verify_chain() -> None:
            cls._verify_bound_root(binding)
            for index, component in enumerate(directory_names):
                parent_fd = directory_fds[index]
                child_fd = directory_fds[index + 1]
                expected = directory_states[index]
                path_state = os.stat(
                    component,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                opened_state = os.fstat(child_fd)
                _require_directory(path_state)
                _require_directory(opened_state)
                if (
                    not _same_inode(expected, opened_state)
                    or not _same_inode(path_state, opened_state)
                ):
                    raise MemoryStoreUnsafePath(
                        "Memory publication directory changed"
                    )

        try:
            cls._verify_bound_root(binding)
            if binding.root_fd is None:  # narrowed by the check above
                raise MemoryStoreUnavailable(
                    "Memory publication root descriptor is unavailable"
                )
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_flags |= getattr(os, "O_CLOEXEC", 0)
            directory_flags |= getattr(os, "O_NOFOLLOW", 0)
            directory_fds.append(os.dup(binding.root_fd))
            for component in directory_parts:
                _safe_basename(Path(component))
                parent_fd = directory_fds[-1]
                path_state = os.stat(
                    component,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                _require_directory(path_state)
                child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
                opened_state = os.fstat(child_fd)
                _require_directory(opened_state)
                if not _same_inode(path_state, opened_state):
                    os.close(child_fd)
                    raise MemoryStoreUnsafePath(
                        "Memory publication directory changed"
                    )
                directory_names.append(component)
                directory_states.append(opened_state)
                directory_fds.append(child_fd)

            target_fd = directory_fds[-1]
            cls._validate_posix_target(target_fd, name)
            temporary_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            temporary_flags |= getattr(os, "O_CLOEXEC", 0)
            temporary_flags |= getattr(os, "O_NOFOLLOW", 0)
            for _attempt in range(16):
                candidate = f".{name}.{uuid.uuid4().hex}.tmp"
                try:
                    temporary_fd = os.open(
                        candidate,
                        temporary_flags,
                        0o600,
                        dir_fd=target_fd,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if temporary_fd is None or temporary_name is None:
                raise MemoryStoreUnavailable(
                    "Memory publication temporary file is unavailable"
                )
            os.fchmod(temporary_fd, 0o600)
            _write_all(temporary_fd, encoded)
            os.fsync(temporary_fd)
            temporary_opened = os.fstat(temporary_fd)
            _require_regular_target(temporary_opened)
            if temporary_opened.st_size != len(encoded):
                raise MemoryStoreConflict(
                    "Memory publication temporary file has the wrong size"
                )

            verify_chain()
            cls._validate_posix_target(target_fd, name)
            temporary_path_state = os.stat(
                temporary_name,
                dir_fd=target_fd,
                follow_symlinks=False,
            )
            _require_regular_target(temporary_path_state)
            if (
                not _same_inode(temporary_opened, temporary_path_state)
                or temporary_path_state.st_size != len(encoded)
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication temporary file changed"
                )
            os.replace(
                temporary_name,
                name,
                src_dir_fd=target_fd,
                dst_dir_fd=target_fd,
            )
            temporary_name = None
            published_state = os.stat(
                name,
                dir_fd=target_fd,
                follow_symlinks=False,
            )
            _require_regular_target(published_state)
            if (
                not _same_inode(temporary_opened, published_state)
                or published_state.st_size != len(encoded)
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication target changed"
                )
            os.fsync(target_fd)
            verify_chain()
        except (MemoryStoreConflict, MemoryStoreUnavailable, MemoryStoreUnsafePath):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable("Memory publication failed") from error
        finally:
            if temporary_fd is not None:
                try:
                    os.close(temporary_fd)
                except OSError:
                    pass
            if temporary_name is not None and directory_fds:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fds[-1])
                except OSError:
                    pass
            for descriptor in reversed(directory_fds):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @staticmethod
    def _verify_posix_owner(
        owner: Path,
        descriptor: int,
        *,
        expected: os.stat_result,
    ) -> None:
        try:
            path_state = os.stat(owner)
            opened_state = os.fstat(descriptor)
        except OSError as error:
            raise MemoryStoreUnsafePath(
                "Memory publication owner changed"
            ) from error
        _require_directory(path_state)
        _require_directory(opened_state)
        if (
            not _same_inode(expected, opened_state)
            or not _same_inode(path_state, opened_state)
        ):
            raise MemoryStoreUnsafePath("Memory publication owner changed")

    @staticmethod
    def _verify_posix_child(
        owner_fd: int,
        name: str,
        child_fd: int,
        *,
        expected: os.stat_result,
    ) -> None:
        try:
            path_state = os.stat(
                name,
                dir_fd=owner_fd,
                follow_symlinks=False,
            )
            opened_state = os.fstat(child_fd)
        except OSError as error:
            raise MemoryStoreUnsafePath(
                "Memory publication directory changed"
            ) from error
        _require_directory(path_state)
        _require_directory(opened_state)
        if (
            not _same_inode(expected, opened_state)
            or not _same_inode(path_state, opened_state)
        ):
            raise MemoryStoreUnsafePath(
                "Memory publication directory changed"
            )

    @classmethod
    def _ensure_directory_posix(cls, directory: Path) -> None:
        owner = directory.parent
        name = _safe_basename(directory)
        try:
            owner_before = os.stat(owner)
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory publication owner is unavailable"
            ) from error
        _require_directory(owner_before)

        owner_fd: int | None = None
        child_fd: int | None = None
        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_flags |= getattr(os, "O_CLOEXEC", 0)
            # Do not use O_NOFOLLOW for the owner: opening a repository through
            # an intentional workspace symlink is supported.  The inode checks
            # still bind the resolved owner throughout this operation.
            owner_fd = os.open(owner, directory_flags)
            owner_opened = os.fstat(owner_fd)
            _require_directory(owner_opened)
            if not _same_inode(owner_before, owner_opened):
                raise MemoryStoreUnsafePath(
                    "Memory publication owner changed"
                )
            try:
                child_before = os.stat(
                    name,
                    dir_fd=owner_fd,
                    follow_symlinks=False,
                )
                created = False
            except FileNotFoundError:
                try:
                    os.mkdir(name, 0o700, dir_fd=owner_fd)
                    created = True
                except FileExistsError:
                    created = False
                child_before = os.stat(
                    name,
                    dir_fd=owner_fd,
                    follow_symlinks=False,
                )
            _require_directory(child_before)
            child_flags = directory_flags | getattr(os, "O_NOFOLLOW", 0)
            child_fd = os.open(name, child_flags, dir_fd=owner_fd)
            child_opened = os.fstat(child_fd)
            _require_directory(child_opened)
            if not _same_inode(child_before, child_opened):
                raise MemoryStoreUnsafePath(
                    "Memory publication directory changed"
                )
            cls._verify_posix_owner(
                owner,
                owner_fd,
                expected=owner_opened,
            )
            cls._verify_posix_child(
                owner_fd,
                name,
                child_fd,
                expected=child_opened,
            )
            if created:
                os.fsync(child_fd)
                os.fsync(owner_fd)
                cls._verify_posix_owner(
                    owner,
                    owner_fd,
                    expected=owner_opened,
                )
                cls._verify_posix_child(
                    owner_fd,
                    name,
                    child_fd,
                    expected=child_opened,
                )
        except (MemoryStoreConflict, MemoryStoreUnavailable, MemoryStoreUnsafePath):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory publication directory is unavailable"
            ) from error
        finally:
            if child_fd is not None:
                try:
                    os.close(child_fd)
                except OSError:
                    pass
            if owner_fd is not None:
                try:
                    os.close(owner_fd)
                except OSError:
                    pass

    @staticmethod
    def _ensure_directory_portable(directory: Path) -> None:
        try:
            if directory.is_symlink() or _windows_path_has_reparse_component(
                directory.parent
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication directory is unsafe"
                )
            directory.mkdir(mode=0o700, exist_ok=True)
            metadata = os.lstat(directory)
        except MemoryStoreUnsafePath:
            raise
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory publication directory is unavailable"
            ) from error
        _require_directory(metadata)
        if _is_windows_reparse_point(metadata):
            raise MemoryStoreUnsafePath(
                "Memory publication directory is unsafe"
            )

    @staticmethod
    def _verify_posix_parent(
        parent: Path,
        descriptor: int,
        *,
        expected: os.stat_result,
    ) -> None:
        try:
            path_state = os.lstat(parent)
            opened_state = os.fstat(descriptor)
        except OSError as error:
            raise MemoryStoreUnsafePath(
                "Memory publication directory changed"
            ) from error
        _require_directory(path_state)
        _require_directory(opened_state)
        if (
            not _same_inode(expected, opened_state)
            or not _same_inode(path_state, opened_state)
        ):
            raise MemoryStoreUnsafePath(
                "Memory publication directory changed"
            )

    @staticmethod
    def _validate_posix_target(parent_fd: int, name: str) -> None:
        try:
            metadata = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        _require_regular_target(metadata)

    @classmethod
    def _publish_posix(cls, target: Path, encoded: bytes) -> None:
        parent = target.parent
        name = _safe_basename(target)
        try:
            parent_before = os.lstat(parent)
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory publication directory is unavailable"
            ) from error
        _require_directory(parent_before)

        directory_fd: int | None = None
        temporary_fd: int | None = None
        temporary_name: str | None = None
        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_flags |= getattr(os, "O_CLOEXEC", 0)
            directory_flags |= getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(parent, directory_flags)
            directory_opened = os.fstat(directory_fd)
            _require_directory(directory_opened)
            if not _same_inode(parent_before, directory_opened):
                raise MemoryStoreUnsafePath(
                    "Memory publication directory changed"
                )
            cls._validate_posix_target(directory_fd, name)

            temporary_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            temporary_flags |= getattr(os, "O_CLOEXEC", 0)
            temporary_flags |= getattr(os, "O_NOFOLLOW", 0)
            for _attempt in range(16):
                candidate = f".{name}.{uuid.uuid4().hex}.tmp"
                try:
                    temporary_fd = os.open(
                        candidate,
                        temporary_flags,
                        0o600,
                        dir_fd=directory_fd,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if temporary_fd is None or temporary_name is None:
                raise MemoryStoreUnavailable(
                    "Memory publication temporary file is unavailable"
                )
            os.fchmod(temporary_fd, 0o600)
            _write_all(temporary_fd, encoded)
            os.fsync(temporary_fd)
            temporary_opened = os.fstat(temporary_fd)
            _require_regular_target(temporary_opened)
            if temporary_opened.st_size != len(encoded):
                raise MemoryStoreConflict(
                    "Memory publication temporary file has the wrong size"
                )

            cls._verify_posix_parent(
                parent,
                directory_fd,
                expected=directory_opened,
            )
            cls._validate_posix_target(directory_fd, name)
            temporary_path_state = os.stat(
                temporary_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            _require_regular_target(temporary_path_state)
            if (
                not _same_inode(temporary_opened, temporary_path_state)
                or temporary_path_state.st_size != len(encoded)
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication temporary file changed"
                )
            os.replace(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary_name = None
            published_state = os.stat(
                name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            _require_regular_target(published_state)
            if (
                not _same_inode(temporary_opened, published_state)
                or published_state.st_size != len(encoded)
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication target changed"
                )
            os.fsync(directory_fd)
            cls._verify_posix_parent(
                parent,
                directory_fd,
                expected=directory_opened,
            )
            durable_state = os.stat(
                name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            _require_regular_target(durable_state)
            if (
                not _same_inode(temporary_opened, durable_state)
                or durable_state.st_size != len(encoded)
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication target changed"
                )
        except (MemoryStoreConflict, MemoryStoreUnavailable, MemoryStoreUnsafePath):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory publication failed"
            ) from error
        finally:
            if temporary_fd is not None:
                try:
                    os.close(temporary_fd)
                except OSError:
                    pass
            if temporary_name is not None and directory_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError:
                    pass
            if directory_fd is not None:
                try:
                    os.close(directory_fd)
                except OSError:
                    pass

    @staticmethod
    def _portable_parent_state(parent: Path) -> os.stat_result:
        try:
            metadata = os.lstat(parent)
        except OSError as error:
            raise MemoryStoreUnavailable(
                "Memory publication directory is unavailable"
            ) from error
        _require_directory(metadata)
        if _windows_path_has_reparse_component(parent):
            raise MemoryStoreUnsafePath(
                "Memory publication directory is unsafe"
            )
        return metadata

    @classmethod
    def _publish_portable(cls, target: Path, encoded: bytes) -> None:
        parent = target.parent
        name = _safe_basename(target)
        parent_before = cls._portable_parent_state(parent)
        try:
            target_before = os.lstat(target)
        except FileNotFoundError:
            pass
        else:
            _require_regular_target(target_before)
            if _is_windows_reparse_point(target_before):
                raise MemoryStoreUnsafePath(
                    "Memory publication target is unsafe"
                )

        temporary_fd: int | None = None
        temporary_path: str | None = None
        try:
            temporary_fd, temporary_path = tempfile.mkstemp(
                dir=os.fspath(parent),
                prefix=f".{name}.",
                suffix=".tmp",
            )
            try:
                os.fchmod(temporary_fd, 0o600)
            except (AttributeError, OSError):
                pass
            _write_all(temporary_fd, encoded)
            os.fsync(temporary_fd)
            temporary_opened = os.fstat(temporary_fd)
            _require_regular_target(temporary_opened)
            if temporary_opened.st_size != len(encoded):
                raise MemoryStoreConflict(
                    "Memory publication temporary file has the wrong size"
                )
            temporary_path_state = os.lstat(temporary_path)
            _require_regular_target(temporary_path_state)
            if (
                not _same_inode(temporary_opened, temporary_path_state)
                or temporary_path_state.st_size != len(encoded)
                or _is_windows_reparse_point(temporary_path_state)
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication temporary file changed"
                )

            parent_now = cls._portable_parent_state(parent)
            if not _same_inode(parent_before, parent_now):
                raise MemoryStoreUnsafePath(
                    "Memory publication directory changed"
                )
            try:
                target_now = os.lstat(target)
            except FileNotFoundError:
                pass
            else:
                _require_regular_target(target_now)
                if _is_windows_reparse_point(target_now):
                    raise MemoryStoreUnsafePath(
                        "Memory publication target is unsafe"
                    )
            os.replace(temporary_path, target)
            temporary_path = None
            published_state = os.lstat(target)
            _require_regular_target(published_state)
            if (
                not _same_inode(temporary_opened, published_state)
                or published_state.st_size != len(encoded)
                or _is_windows_reparse_point(published_state)
            ):
                raise MemoryStoreUnsafePath(
                    "Memory publication target changed"
                )
            parent_after = cls._portable_parent_state(parent)
            if not _same_inode(parent_before, parent_after):
                raise MemoryStoreUnsafePath(
                    "Memory publication directory changed"
                )
        except (MemoryStoreConflict, MemoryStoreUnavailable, MemoryStoreUnsafePath):
            raise
        except OSError as error:
            raise MemoryStoreUnavailable("Memory publication failed") from error
        finally:
            if temporary_fd is not None:
                try:
                    os.close(temporary_fd)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass


__all__ = ["MemoryAuthorityPublisher", "MemoryScopePublication"]
